"""
Memory Manager para Origin.
Gestiona memoria persistente con embeddings y recuperación semántica.

Persistence: PostgreSQL (primary) → JSON file (fallback) → RAM (always)
Al iniciar, carga memorias desde DB/JSON. Cada nueva memoria se persiste.
"""

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter
import json
import re
import logging
import numpy as np

logger = logging.getLogger("origin.memory")

# ── Config ────────────────────────────────────────────────────────
MEMORY_DIR = Path(__file__).parent.parent / "data" / "memory"
MEMORY_FILE = MEMORY_DIR / "memories.json"
PREFS_FILE = MEMORY_DIR / "preferences.json"
CONVERSATION_FILE = MEMORY_DIR / "conversations.json"
MAX_CONVERSATION_HISTORY = 200
MAX_MEMORIES = 5000

# ── Feature config ─────────────────────────────────────────────────
SUMMARY_CHECKPOINT_EVERY = 20  # Trigger summary every N conversation turns
TOPIC_SIMILARITY_MIN = 0.15  # Keyword overlap below this → new topic segment

# ── Tone detection lookups ─────────────────────────────────────────
_STOPWORDS: frozenset = frozenset(
    {
        "de",
        "la",
        "el",
        "en",
        "y",
        "a",
        "que",
        "es",
        "se",
        "no",
        "te",
        "lo",
        "con",
        "por",
        "para",
        "su",
        "una",
        "un",
        "los",
        "las",
        "me",
        "al",
        "del",
        "más",
        "como",
        "mi",
        "si",
        "ya",
        "pero",
        "o",
        "le",
        "the",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "can",
        "may",
        "might",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "this",
        "that",
        "these",
        "those",
        "what",
        "how",
    }
)

_TONE_KEYWORDS: Dict[str, frozenset] = {
    "casual": frozenset(
        {
            "oye",
            "hey",
            "hola",
            "buenas",
            "jaja",
            "jajaja",
            "ok",
            "dale",
            "che",
            "bro",
            "genial",
            "cool",
            "lol",
            "hi",
            "hello",
            "thanks",
            "thx",
            "btw",
            "idk",
            "omg",
            "xd",
            "ntp",
            "pues",
            "vamos",
            "venga",
        }
    ),
    "formal": frozenset(
        {
            "estimado",
            "agradecería",
            "solicito",
            "quisiera",
            "regarding",
            "furthermore",
            "however",
            "therefore",
            "please",
            "kindly",
            "consequently",
            "pursuant",
            "hereby",
            "wherein",
            "aforementioned",
        }
    ),
    "technical": frozenset(
        {
            "error",
            "bug",
            "función",
            "funcion",
            "api",
            "debug",
            "stack",
            "trace",
            "exception",
            "import",
            "module",
            "class",
            "method",
            "variable",
            "json",
            "http",
            "async",
            "await",
            "database",
            "query",
            "endpoint",
            "deploy",
            "git",
            "branch",
            "null",
            "return",
            "código",
            "codigo",
            "script",
            "loop",
            "array",
            "object",
            "type",
            "interface",
            "schema",
            "request",
            "response",
            "server",
            "client",
            "token",
            "auth",
        }
    ),
}

_EXPLICIT_TONE: Dict[str, List[str]] = {
    "casual": ["habla normal", "más relajado", "mas relajado", "coloquial", "casual", "informal"],
    "formal": ["sé formal", "se formal", "más formal", "mas formal", "formal mode", "registro formal"],
    "technical": ["modo técnico", "modo tecnico", "technical mode", "sé técnico", "se tecnico"],
}


@dataclass
class Memory:
    """Representa una memoria individual"""

    id: str
    content: str
    type: str  # "conversation", "decision", "learning", "profile", "preference", "fact", "pattern"
    timestamp: datetime
    embedding: Optional[List[float]] = None
    relevance_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    access_count: int = 0
    last_accessed: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata or {},
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Memory":
        return cls(
            id=data["id"],
            content=data["content"],
            type=data.get("type", "conversation"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
            access_count=data.get("access_count", 0),
            last_accessed=datetime.fromisoformat(data["last_accessed"]) if data.get("last_accessed") else None,
        )


class MemoryManager:
    """
    Gestiona memoria persistente del usuario con embeddings y recuperación semántica.

    Layers:
        1. RAM (always) — fast access during session
        2. JSON file — survives API restarts, no DB needed
        3. PostgreSQL — full persistence with vector search (optional)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
        self.memories: Dict[str, Memory] = {}
        self.conversation_history: List[Dict[str, str]] = []
        self.preferences: Dict[str, Any] = {}
        self.embedding_dim = 384

        # Feature 1: Context checkpointing
        self.conversation_summaries: List[Dict[str, Any]] = []
        self._messages_since_checkpoint: int = 0

        # Feature 2: Cross-reference index
        self._conv_counter: int = 0
        self._current_segment_id: int = 0
        self._last_user_message: str = ""

        # Feature 3: Adaptive tone
        self.current_tone: str = "technical"
        self._tone_confidence: float = 0.5
        self._tone_history: List[str] = []

        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._load_from_disk()

        # ChromaDB como capa vectorial adicional (import lazy para no bloquear startup)
        try:
            from skills.chromadb_memory import ChromaMemoryBackend
            self._chroma = ChromaMemoryBackend()
        except Exception:
            self._chroma = type("_NullChroma", (), {"is_ready": False})()
        if self._chroma.is_ready:
            logger.info("ChromaDB vector backend: active")
            import threading
            threading.Thread(target=self._sync_memories_to_chroma, daemon=True, name="chroma-sync").start()
        else:
            logger.info("ChromaDB vector backend: not available (fallback to numpy)")

        logger.info(
            f"Memory loaded: {len(self.memories)} memories, "
            f"{len(self.preferences)} preferences, "
            f"{len(self.conversation_history)} conversations"
        )

    def _sync_memories_to_chroma(self):
        """Sincroniza memorias RAM → ChromaDB al arrancar."""
        if not self._chroma.is_ready:
            return
        for mem_id, mem in self.memories.items():
            if mem.type in ("decision", "learning", "fact", "pattern"):
                self._chroma.add(mem.content, {"type": mem.type, "memory_id": mem_id, "source": "origin"}, mem_id)

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    # ── Persistence: Load ──────────────────────────────────────────

    def _load_from_disk(self):
        """Load memories, preferences and conversations from JSON files."""
        # Memories
        if MEMORY_FILE.exists():
            try:
                data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                for mem_data in data.get("memories", []):
                    try:
                        mem = Memory.from_dict(mem_data)
                        self.memories[mem.id] = mem
                    except Exception as e:
                        logger.warning(f"Skip corrupt memory: {e}")
            except Exception as e:
                logger.warning(f"Failed to load memories: {e}")

        # Preferences
        if PREFS_FILE.exists():
            try:
                self.preferences = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load preferences: {e}")

        # Recent conversations + persisted state
        if CONVERSATION_FILE.exists():
            try:
                data = json.loads(CONVERSATION_FILE.read_text(encoding="utf-8"))
                self.conversation_history = data.get("conversations", [])[-MAX_CONVERSATION_HISTORY:]
                self.conversation_summaries = data.get("summaries", [])[-10:]
                state = data.get("_state", {})
                self._conv_counter = state.get("conv_counter", len(self.conversation_history))
                self._current_segment_id = state.get("segment_id", 0)
                self._messages_since_checkpoint = state.get("messages_since_checkpoint", 0)
                self.current_tone = state.get("current_tone", "technical")
            except Exception as e:
                logger.warning(f"Failed to load conversations: {e}")

    # ── Persistence: Save ──────────────────────────────────────────

    def _save_memories(self):
        """Save all memories to JSON file."""
        try:
            data = {
                "memories": [mem.to_dict() for mem in self.memories.values()],
                "saved_at": datetime.now().isoformat(),
                "count": len(self.memories),
            }
            MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save memories: {e}")

    def _save_preferences(self):
        """Save preferences to JSON file."""
        try:
            self.preferences["_updated_at"] = datetime.now().isoformat()
            PREFS_FILE.write_text(json.dumps(self.preferences, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save preferences: {e}")

    def _save_conversations(self):
        """Save recent conversations, summaries and runtime state to JSON file."""
        try:
            data = {
                "conversations": self.conversation_history[-MAX_CONVERSATION_HISTORY:],
                "summaries": self.conversation_summaries[-10:],
                "_state": {
                    "conv_counter": self._conv_counter,
                    "segment_id": self._current_segment_id,
                    "messages_since_checkpoint": self._messages_since_checkpoint,
                    "current_tone": self.current_tone,
                },
                "saved_at": datetime.now().isoformat(),
            }
            CONVERSATION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save conversations: {e}")

    # ── Core: Add Memory ───────────────────────────────────────────

    def add_memory(
        self, content: str, memory_type: str = "conversation", metadata: Optional[Dict[str, Any]] = None
    ) -> Memory:
        """Añade una nueva memoria con embedding y persiste."""
        memory_id = f"{memory_type}_{datetime.now().timestamp()}"

        # Generate embedding
        try:
            embedding = self.model.encode(content).tolist()
        except Exception:
            embedding = None

        memory = Memory(
            id=memory_id,
            content=content,
            type=memory_type,
            timestamp=datetime.now(),
            embedding=embedding,
            metadata=metadata or {},
        )

        self.memories[memory_id] = memory

        # Auto-persist
        self._save_memories()

        # Extract preferences if applicable
        if memory_type in ("decision", "learning", "preference"):
            self._extract_preference(content, metadata)

        logger.debug(f"Memory added: {memory_type} ({len(content)} chars)")
        return memory

    # ── Core: Conversation Tracking ────────────────────────────────

    def add_conversation(self, role: str, message: str, metadata: Optional[Dict] = None) -> None:
        """Registra un mensaje con índice, segmento de tema y tono detectado."""
        self._conv_counter += 1

        # Feature 2: topic tracking on user turns
        if role == "user":
            if self._last_user_message:
                if self._detect_topic_change(self._last_user_message, message):
                    self._current_segment_id += 1
                    logger.debug(f"Topic change detected → segment {self._current_segment_id}")
            self._last_user_message = message
            # Feature 3: tone detection
            self.update_tone(message)

        entry = {
            "role": role,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "idx": self._conv_counter,
            "topic_id": self._current_segment_id,
        }
        if metadata:
            entry["metadata"] = metadata

        self.conversation_history.append(entry)

        if len(self.conversation_history) > MAX_CONVERSATION_HISTORY:
            self.conversation_history = self.conversation_history[-MAX_CONVERSATION_HISTORY:]

        # Feature 1: checkpoint counter
        self._messages_since_checkpoint += 1

        self._save_conversations()

    def get_recent_conversation(self, n: int = 20) -> List[Dict[str, str]]:
        """Obtiene las últimas N interacciones."""
        return self.conversation_history[-n:]

    # ── Feature 1: Context Checkpointing ───────────────────────────

    def should_checkpoint(self) -> bool:
        """True when enough messages have accumulated for a new summary."""
        return self._messages_since_checkpoint >= SUMMARY_CHECKPOINT_EVERY

    def record_checkpoint(self, summary: str) -> None:
        """Store a generated summary and reset the checkpoint counter."""
        self.conversation_summaries.append(
            {
                "summary": summary,
                "timestamp": datetime.now().isoformat(),
                "message_count_at": self._conv_counter,
            }
        )
        self.conversation_summaries = self.conversation_summaries[-10:]
        self._messages_since_checkpoint = 0
        self._save_conversations()
        logger.info(f"Checkpoint recorded ({len(summary)} chars)")

    def get_latest_summary(self) -> Optional[str]:
        """Return the most recent rolling summary, if any."""
        if not self.conversation_summaries:
            return None
        return self.conversation_summaries[-1]["summary"]

    # ── Feature 2: Cross-Reference Index ───────────────────────────

    def _extract_keywords(self, text: str) -> frozenset:
        words = re.findall(r"\b\w{3,}\b", text.lower())
        return frozenset(w for w in words if w not in _STOPWORDS)

    def _detect_topic_change(self, prev_text: str, curr_text: str) -> bool:
        """Returns True when keyword overlap between consecutive user messages is below threshold."""
        prev_kw = self._extract_keywords(prev_text)
        curr_kw = self._extract_keywords(curr_text)
        if not prev_kw or not curr_kw:
            return False
        overlap = len(prev_kw & curr_kw) / max(len(prev_kw | curr_kw), 1)
        return overlap < TOPIC_SIMILARITY_MIN

    def get_cross_reference_index(self, last_n: int = 8) -> List[Dict[str, Any]]:
        """Return last N messages with their sequential index and topic segment."""
        recent = self.conversation_history[-last_n:]
        return [
            {
                "idx": entry.get("idx", i + 1),
                "topic_id": entry.get("topic_id", 0),
                "role": entry["role"],
                "message": entry["message"][:120],
            }
            for i, entry in enumerate(recent)
        ]

    # ── Feature 3: Adaptive Tone Detection ─────────────────────────

    def detect_tone(self, text: str) -> Tuple[str, float]:
        """Detect tone from text. Explicit commands take priority; otherwise keyword scoring."""
        text_lower = text.lower()

        # Explicit commands → immediate override
        for tone, phrases in _EXPLICIT_TONE.items():
            if any(phrase in text_lower for phrase in phrases):
                return tone, 1.0

        # Keyword scoring
        words = frozenset(re.findall(r"\b\w+\b", text_lower))
        scores: Dict[str, float] = {}
        for tone, keywords in _TONE_KEYWORDS.items():
            matches = len(words & keywords)
            scores[tone] = matches / max(len(words), 1)

        best = max(scores, key=lambda k: scores[k])
        best_score = scores[best]

        if best_score < 0.02:
            return "technical", 0.3

        return best, min(0.5 + best_score * 5, 1.0)

    def update_tone(self, text: str) -> str:
        """Update current_tone using a sliding window of the last 5 detections."""
        detected, confidence = self.detect_tone(text)

        # Explicit command → immediate reset
        if confidence == 1.0:
            self.current_tone = detected
            self._tone_confidence = 1.0
            self._tone_history = [detected] * 3
            logger.info(f"Tone explicitly set to: {detected}")
            return self.current_tone

        self._tone_history.append(detected)
        if len(self._tone_history) > 5:
            self._tone_history = self._tone_history[-5:]

        self.current_tone = Counter(self._tone_history).most_common(1)[0][0]
        self._tone_confidence = confidence
        return self.current_tone

    # ── Core: Search ───────────────────────────────────────────────

    def search_memories(self, query: str, top_k: int = 5, memory_types: Optional[List[str]] = None) -> List[Memory]:
        """Busca memorias por similitud semántica.
        Usa ChromaDB si está disponible, sino numpy fallback."""
        if not self.memories:
            return []

        # Try ChromaDB first for vector search
        if self._chroma.is_ready:
            try:
                chroma_results = self._chroma.search(query, top_k=top_k * 2)
                memory_map = {m.id: m for m in self.memories.values()}
                scored = []
                for cr in chroma_results:
                    mem_id = (cr.get("metadata") or {}).get("memory_id", "")
                    if mem_id in memory_map:
                        mem = memory_map[mem_id]
                        mem.relevance_score = max(0, 1.0 - cr.get("distance", 0) / 2.0)
                        if memory_types is None or mem.type in memory_types:
                            scored.append(mem)
                if scored:
                    scored.sort(key=lambda x: x.relevance_score, reverse=True)
                    for mem in scored[:top_k]:
                        mem.access_count += 1
                        mem.last_accessed = datetime.now()
                    return scored[:top_k]
            except Exception:
                pass

        # Fallback: numpy dot product — requires sentence_transformers
        try:
            query_embedding = self.model.encode(query)
        except (ModuleNotFoundError, ImportError):
            # sentence_transformers not installed: return empty rather than crash
            return []

        candidates = [mem for mem in self.memories.values() if memory_types is None or mem.type in memory_types]

        if not candidates:
            return []

        scores = []
        for mem in candidates:
            if mem.embedding:
                similarity = np.dot(query_embedding, mem.embedding) / (
                    np.linalg.norm(query_embedding) * np.linalg.norm(mem.embedding) + 1e-8
                )
                mem.relevance_score = float(similarity)
                scores.append(mem)

        scores.sort(key=lambda x: x.relevance_score, reverse=True)

        for mem in scores[:top_k]:
            mem.access_count += 1
            mem.last_accessed = datetime.now()

        return scores[:top_k]

    # ── Preferences ────────────────────────────────────────────────

    def set_preference(self, key: str, value: Any, source: str = "explicit") -> None:
        """Establece una preferencia del usuario."""
        self.preferences[key] = {
            "value": value,
            "source": source,  # "explicit" (user said) | "inferred" (Origin learned)
            "updated_at": datetime.now().isoformat(),
            "confidence": 1.0 if source == "explicit" else 0.7,
        }
        self._save_preferences()
        logger.info(f"Preference set: {key} = {value} ({source})")

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Obtiene una preferencia."""
        pref = self.preferences.get(key)
        if pref is None:
            return default
        return pref.get("value", default)

    def get_all_preferences(self) -> Dict[str, Any]:
        """Retorna todas las preferencias (sin metadatos internos)."""
        return {
            k: v.get("value") if isinstance(v, dict) else v
            for k, v in self.preferences.items()
            if not k.startswith("_")
        }

    def _extract_preference(self, content: str, metadata: Optional[Dict] = None):
        """Intenta extraer preferencias del contenido de una memoria."""
        content_lower = content.lower()

        # Patrones simples de preferencia
        patterns = {
            "editor": ["vscode", "vs code", "sublime", "vim", "neovim", "code"],
            "browser": ["chrome", "firefox", "edge", "brave"],
            "language": ["python", "javascript", "typescript", "rust", "go", "java"],
            "os": ["windows", "linux", "macos", "mac"],
            "theme": ["dark", "light", "oscuro", "claro"],
        }

        for category, keywords in patterns.items():
            for kw in keywords:
                if kw in content_lower:
                    current = self.preferences.get(category, {})
                    if not isinstance(current, dict) or current.get("source") != "explicit":
                        self.set_preference(category, kw, source="inferred")
                    break

    # ── Context Building ───────────────────────────────────────────

    def get_memory_context(self, query: str, recent_interactions: int = 10) -> Dict[str, Any]:
        """Construye contexto completo para el reasoning loop."""
        relevant_memories = self.search_memories(query, top_k=5)
        recent_conv = self.get_recent_conversation(n=recent_interactions)
        prefs = self.get_all_preferences()

        return {
            "relevant_memories": [
                {
                    "content": mem.content,
                    "type": mem.type,
                    "relevance": round(mem.relevance_score, 3),
                    "timestamp": mem.timestamp.isoformat(),
                }
                for mem in relevant_memories
            ],
            "recent_conversation": recent_conv,
            "user_preferences": prefs,
            "memory_count": len(self.memories),
            "preference_count": len(prefs),
            # Feature 1: rolling context summary
            "latest_summary": self.get_latest_summary(),
            "summaries_count": len(self.conversation_summaries),
            # Feature 2: cross-reference index
            "cross_reference_index": self.get_cross_reference_index(),
            "current_topic_id": self._current_segment_id,
            # Feature 3: adaptive tone
            "current_tone": self.current_tone,
            "tone_confidence": round(self._tone_confidence, 2),
        }

    # ── Stats & Maintenance ────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del sistema de memoria."""
        type_counts = {}
        for mem in self.memories.values():
            type_counts[mem.type] = type_counts.get(mem.type, 0) + 1

        return {
            "total_memories": len(self.memories),
            "by_type": type_counts,
            "total_conversations": len(self.conversation_history),
            "total_preferences": len([k for k in self.preferences if not k.startswith("_")]),
            "memory_file": str(MEMORY_FILE),
            "memory_file_exists": MEMORY_FILE.exists(),
            "memory_file_size_kb": round(MEMORY_FILE.stat().st_size / 1024, 1) if MEMORY_FILE.exists() else 0,
        }

    def get_profile_memories(self) -> List[Memory]:
        return [mem for mem in self.memories.values() if mem.type == "profile"]

    def get_decision_memories(self) -> List[Memory]:
        return [mem for mem in self.memories.values() if mem.type == "decision"]

    def clear_old_memories(self, days: int = 30) -> int:
        """Limpia memorias antiguas y persiste."""
        cutoff = datetime.now() - timedelta(days=days)
        initial = len(self.memories)
        self.memories = {k: v for k, v in self.memories.items() if v.timestamp > cutoff}
        removed = initial - len(self.memories)
        if removed > 0:
            self._save_memories()
        return removed

    def clear_all(self) -> None:
        """Erase all memories, preferences, and conversation history (GDPR erasure)."""
        self.memories.clear()
        self.conversation_history.clear()
        self.preferences.clear()
        self.conversation_summaries.clear()
        for path in (MEMORY_FILE, PREFS_FILE, CONVERSATION_FILE):
            try:
                if path.exists():
                    path.unlink()
            except OSError as e:
                logger.warning(f"Could not delete {path}: {e}")
        if getattr(self._chroma, "is_ready", False):
            try:
                self._chroma.clear()
            except Exception:
                pass
        logger.info("Memory store cleared (GDPR erasure)")

    def export_memories(self) -> Dict[str, Any]:
        return {
            "memories": [mem.to_dict() for mem in self.memories.values()],
            "preferences": self.get_all_preferences(),
            "conversation_count": len(self.conversation_history),
            "exported_at": datetime.now().isoformat(),
        }
