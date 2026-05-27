"""
SubconsciousEngine — Motor de reflexión autónoma en background.

Corre periódicamente sin intervención del usuario. Analiza memorias,
conversaciones y patrones para generar "pensamientos" (insights) que
se inyectan en el contexto cuando son relevantes.

Tipos de reflexión:
  - pattern:    Detecta patrones de uso y comportamiento
  - unfinished: Identifica tareas mencionadas pero no completadas
  - connection: Encuentra conexiones entre temas separados
  - suggestion: Genera sugerencias proactivas
  - insight:    Observaciones sobre el usuario o el sistema

Inspirado en OpenHuman Subconscious Engine.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable

logger = logging.getLogger("origin.subconscious")

# ── Config ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "memory"
THOUGHTS_FILE = DATA_DIR / "subconscious_thoughts.json"

REFLECTION_INTERVAL = 180  # seconds between reflection cycles
MAX_THOUGHTS = 200  # max stored thoughts (oldest pruned)
THOUGHTS_PER_CYCLE = 3  # max new thoughts per cycle
RELEVANCE_DECAY_HOURS = 72  # thoughts older than this lose relevance
MIN_CONVERSATIONS_FOR_REFLECTION = 4  # need at least this many to reflect


@dataclass
class Thought:
    """Un pensamiento generado por el subconsciente."""

    id: str
    type: str  # pattern | unfinished | connection | suggestion | insight
    content: str  # the actual thought/insight
    confidence: float  # 0.0 - 1.0
    source: str  # what triggered this thought
    created_at: str
    surfaced: bool = False  # has it been shown to the user?
    surfaced_at: Optional[str] = None
    relevance_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "surfaced": self.surfaced,
            "surfaced_at": self.surfaced_at,
            "relevance_tags": self.relevance_tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Thought":
        return cls(
            id=data["id"],
            type=data["type"],
            content=data["content"],
            confidence=data.get("confidence", 0.5),
            source=data.get("source", ""),
            created_at=data.get("created_at", datetime.now().isoformat()),
            surfaced=data.get("surfaced", False),
            surfaced_at=data.get("surfaced_at"),
            relevance_tags=data.get("relevance_tags", []),
        )


# ── Reflection prompts ────────────────────────────────────────

_REFLECTION_PROMPTS = {
    "pattern": {
        "system": (
            "Eres el subconsciente de un asistente IA personal llamado Origin. "
            "Analizas conversaciones recientes para detectar PATRONES de comportamiento "
            "del usuario: temas recurrentes, hábitos de trabajo, horarios, herramientas "
            "preferidas, frustraciones comunes. "
            "Genera 1-2 observaciones cortas (1 oración cada una). "
            'Formato JSON: [{"type":"pattern","content":"...","confidence":0.8,"tags":["..."]}]'
        ),
    },
    "unfinished": {
        "system": (
            "Eres el subconsciente de Origin. Analizas conversaciones recientes para "
            "detectar TAREAS PENDIENTES: cosas que el usuario mencionó hacer pero no completó, "
            "bugs mencionados pero no resueltos, promesas hechas, ideas planteadas sin seguimiento. "
            "Genera 1-2 observaciones. "
            'Formato JSON: [{"type":"unfinished","content":"...","confidence":0.7,"tags":["..."]}]'
        ),
    },
    "connection": {
        "system": (
            "Eres el subconsciente de Origin. Buscas CONEXIONES no obvias entre temas "
            "discutidos en diferentes momentos. Relaciones entre proyectos, habilidades "
            "transferibles, o patrones que el usuario podría no ver. "
            "Genera 1 observación máximo. "
            'Formato JSON: [{"type":"connection","content":"...","confidence":0.6,"tags":["..."]}]'
        ),
    },
    "suggestion": {
        "system": (
            "Eres el subconsciente de Origin. Basándote en lo que sabes del usuario, "
            "genera 1 SUGERENCIA PROACTIVA concreta y útil: algo que el usuario no pidió "
            "pero que le beneficiaría (automatizar algo repetitivo, aprender una herramienta, "
            "refactorizar código, tomar un descanso, documentar algo). "
            'Formato JSON: [{"type":"suggestion","content":"...","confidence":0.6,"tags":["..."]}]'
        ),
    },
}


class SubconsciousEngine:
    """
    Motor de reflexión autónoma. Corre en background y genera
    pensamientos/insights que se inyectan en el contexto del reasoning loop.
    """

    def __init__(self, llm_router, memory_mgr, memory_tree):
        self._llm = llm_router
        self._memory = memory_mgr
        self._tree = memory_tree

        self._thoughts: List[Thought] = []
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._cycle_count = 0
        self._last_cycle_at: Optional[str] = None
        self._broadcast: Optional[Callable[[dict], Awaitable[None]]] = None

        # Rotate through reflection types each cycle
        self._reflection_queue = list(_REFLECTION_PROMPTS.keys())
        self._queue_idx = 0

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

        logger.info(f"Subconscious loaded: {len(self._thoughts)} thoughts, " f"{self._cycle_count} past cycles")

    # ── Persistence ────────────────────────────────────────────

    def _load(self):
        if not THOUGHTS_FILE.exists():
            return
        try:
            data = json.loads(THOUGHTS_FILE.read_text(encoding="utf-8"))
            for td in data.get("thoughts", []):
                try:
                    self._thoughts.append(Thought.from_dict(td))
                except Exception as e:
                    logger.warning(f"Skip corrupt thought: {e}")
            self._cycle_count = data.get("cycle_count", 0)
            self._last_cycle_at = data.get("last_cycle_at")
        except Exception as e:
            logger.warning(f"Failed to load subconscious state: {e}")

    def _save(self):
        try:
            data = {
                "thoughts": [t.to_dict() for t in self._thoughts[-MAX_THOUGHTS:]],
                "cycle_count": self._cycle_count,
                "last_cycle_at": self._last_cycle_at,
                "saved_at": datetime.now().isoformat(),
            }
            THOUGHTS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save subconscious state: {e}")

    # ── Lifecycle ──────────────────────────────────────────────

    def set_broadcast(self, broadcast_fn: Callable[[dict], Awaitable[None]]):
        """Set WebSocket broadcast function for proactive notifications."""
        self._broadcast = broadcast_fn

    def start(self):
        """Start the background reflection loop."""
        if self._task and not self._task.done():
            logger.info("Subconscious already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Subconscious started (interval={REFLECTION_INTERVAL}s)")

    def stop(self):
        """Stop the background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Subconscious stopped")

    async def _run_loop(self):
        """Main background loop. Reflects periodically."""
        # Initial delay: let the system stabilize before first reflection
        await asyncio.sleep(60)

        while self._running:
            try:
                new_thoughts = await self._reflect()
                self._cycle_count += 1
                self._last_cycle_at = datetime.now().isoformat()
                self._save()

                if new_thoughts:
                    logger.info(f"Subconscious cycle #{self._cycle_count}: " f"{len(new_thoughts)} new thoughts")
                    # Notify via WebSocket if we have high-confidence insights
                    await self._notify_important(new_thoughts)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Subconscious cycle error: {e}")

            await asyncio.sleep(REFLECTION_INTERVAL)

    # ── Core reflection ────────────────────────────────────────

    async def _reflect(self) -> List[Thought]:
        """Run one reflection cycle. Returns new thoughts generated."""
        # Check if we have enough data to reflect on
        conv = self._memory.get_recent_conversation(n=30)
        if len(conv) < MIN_CONVERSATIONS_FOR_REFLECTION:
            return []

        # Pick next reflection type (rotate)
        ref_type = self._reflection_queue[self._queue_idx % len(self._reflection_queue)]
        self._queue_idx += 1

        # Build context for reflection
        context = self._build_reflection_context(conv, ref_type)
        if not context:
            return []

        # Call LLM
        prompt_cfg = _REFLECTION_PROMPTS[ref_type]
        result = await self._llm.call_llm(
            prompt=f"Conversaciones recientes del usuario:\n\n{context}",
            system_message=prompt_cfg["system"],
            temperature=0.5,
            task_type="subconscious_reflection",
        )

        if not result.get("success"):
            logger.debug(f"Reflection failed: {result.get('error', '')[:80]}")
            return []

        # Parse thoughts from LLM response
        new_thoughts = self._parse_thoughts(result["content"], ref_type)

        # Deduplicate against existing thoughts
        new_thoughts = self._deduplicate(new_thoughts)

        # Store
        self._thoughts.extend(new_thoughts)

        # Prune old thoughts
        if len(self._thoughts) > MAX_THOUGHTS:
            self._thoughts = self._thoughts[-MAX_THOUGHTS:]

        return new_thoughts

    def _build_reflection_context(self, conv: List[Dict], ref_type: str) -> str:
        """Build a context string for reflection, including tree summaries."""
        parts = []

        # Recent conversations (compact format)
        for msg in conv[-20:]:
            role = msg.get("role", "?")
            content = msg.get("message", msg.get("content", ""))
            if isinstance(content, str):
                parts.append(f"{role}: {content[:200]}")

        # Memory tree layers for richer context
        tree_ctx = self._tree.get_formatted_context()
        if tree_ctx:
            parts.append(f"\n--- Memoria a largo plazo ---\n{tree_ctx}")

        # For unfinished tasks, include recent summaries
        if ref_type == "unfinished":
            summary = self._memory.get_latest_summary()
            if summary:
                parts.append(f"\n--- Resumen de sesion anterior ---\n{summary}")

        return "\n".join(parts)

    def _parse_thoughts(self, llm_response: str, default_type: str) -> List[Thought]:
        """Parse LLM JSON response into Thought objects."""
        thoughts = []

        # Try to extract JSON array from response
        try:
            # Find JSON array in response (might be wrapped in markdown)
            json_match = re.search(r"\[.*\]", llm_response, re.DOTALL)
            if json_match:
                items = json.loads(json_match.group())
            else:
                items = json.loads(llm_response)
        except (json.JSONDecodeError, TypeError):
            # Fallback: treat entire response as a single thought
            if llm_response.strip():
                items = [
                    {
                        "type": default_type,
                        "content": llm_response.strip()[:300],
                        "confidence": 0.5,
                        "tags": [],
                    }
                ]
            else:
                return []

        if not isinstance(items, list):
            items = [items]

        now = datetime.now()
        for i, item in enumerate(items[:THOUGHTS_PER_CYCLE]):
            if not isinstance(item, dict) or not item.get("content"):
                continue

            thought = Thought(
                id=f"thought_{now.timestamp()}_{i}",
                type=item.get("type", default_type),
                content=item["content"][:500],
                confidence=min(1.0, max(0.0, float(item.get("confidence", 0.5)))),
                source=f"cycle_{self._cycle_count}_{default_type}",
                created_at=now.isoformat(),
                relevance_tags=item.get("tags", [])[:5],
            )
            thoughts.append(thought)

        return thoughts

    def _deduplicate(self, new_thoughts: List[Thought]) -> List[Thought]:
        """Remove thoughts too similar to existing ones (simple keyword overlap)."""
        existing_contents = {t.content.lower() for t in self._thoughts[-50:]}
        unique = []
        for t in new_thoughts:
            t_lower = t.content.lower()
            # Check for high similarity with existing
            is_dup = False
            for existing in existing_contents:
                t_words = set(t_lower.split())
                e_words = set(existing.split())
                if t_words and e_words:
                    overlap = len(t_words & e_words) / max(len(t_words | e_words), 1)
                    if overlap > 0.6:
                        is_dup = True
                        break
            if not is_dup:
                unique.append(t)
                existing_contents.add(t_lower)
        return unique

    # ── Proactive notifications ────────────────────────────────

    async def _notify_important(self, thoughts: List[Thought]):
        """Notify user via WebSocket of high-confidence thoughts."""
        if not self._broadcast:
            return
        for t in thoughts:
            if t.confidence >= 0.7:
                try:
                    await self._broadcast(
                        {
                            "type": "subconscious_thought",
                            "thought_type": t.type,
                            "content": t.content,
                            "confidence": t.confidence,
                        }
                    )
                    logger.info(f"Subconscious notified: [{t.type}] {t.content[:60]}...")
                except Exception as e:
                    logger.debug(f"Broadcast error: {e}")

    # ── Context retrieval for reasoning loop ───────────────────

    def get_relevant_thoughts(self, query: str, max_results: int = 3) -> List[Dict[str, Any]]:
        """Return thoughts relevant to the current query.

        Uses keyword matching + recency + confidence scoring.
        """
        if not self._thoughts:
            return []

        query_words = set(re.findall(r"\b\w{3,}\b", query.lower()))
        if not query_words:
            return self._get_recent_unsurfaced(max_results)

        now = datetime.now()
        scored = []

        for t in self._thoughts:
            # Keyword relevance
            t_words = set(re.findall(r"\b\w{3,}\b", t.content.lower()))
            tag_words = set(w.lower() for w in t.relevance_tags)
            all_t_words = t_words | tag_words

            if not all_t_words:
                continue

            keyword_score = len(query_words & all_t_words) / max(len(query_words), 1)

            # Recency decay
            try:
                created = datetime.fromisoformat(t.created_at)
                age_hours = (now - created).total_seconds() / 3600
                recency_score = max(0.1, 1.0 - (age_hours / RELEVANCE_DECAY_HOURS))
            except (ValueError, TypeError):
                recency_score = 0.5

            # Not-yet-surfaced bonus
            surfaced_penalty = 0.3 if t.surfaced else 0.0

            # Combined score
            score = keyword_score * 0.5 + t.confidence * 0.25 + recency_score * 0.25 - surfaced_penalty

            if score > 0.1:
                scored.append((score, t))

        scored.sort(key=lambda x: -x[0])

        results = []
        for score, t in scored[:max_results]:
            t.surfaced = True
            t.surfaced_at = now.isoformat()
            results.append(
                {
                    "type": t.type,
                    "content": t.content,
                    "confidence": t.confidence,
                    "score": round(score, 3),
                }
            )

        if results:
            self._save()

        return results

    def _get_recent_unsurfaced(self, n: int) -> List[Dict[str, Any]]:
        """Fallback: return most recent unsurfaced thoughts."""
        unsurfaced = [t for t in reversed(self._thoughts) if not t.surfaced]
        results = []
        for t in unsurfaced[:n]:
            t.surfaced = True
            t.surfaced_at = datetime.now().isoformat()
            results.append(
                {
                    "type": t.type,
                    "content": t.content,
                    "confidence": t.confidence,
                    "score": t.confidence,
                }
            )
        if results:
            self._save()
        return results

    def get_formatted_context(self, query: str) -> str:
        """Return formatted string of relevant thoughts for LLM injection."""
        thoughts = self.get_relevant_thoughts(query)
        if not thoughts:
            return ""

        lines = []
        for t in thoughts:
            emoji = {"pattern": "P", "unfinished": "!", "connection": "~", "suggestion": "+", "insight": "*"}.get(
                t["type"], "?"
            )
            lines.append(f"  [{emoji}] ({t['type']}) {t['content']}")

        return "[PENSAMIENTOS DEL SUBCONSCIENTE]\n" + "\n".join(lines)

    # ── Stats & Dashboard ──────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        type_counts: Dict[str, int] = {}
        for t in self._thoughts:
            type_counts[t.type] = type_counts.get(t.type, 0) + 1

        surfaced = sum(1 for t in self._thoughts if t.surfaced)

        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "last_cycle_at": self._last_cycle_at,
            "total_thoughts": len(self._thoughts),
            "by_type": type_counts,
            "surfaced": surfaced,
            "unsurfaced": len(self._thoughts) - surfaced,
            "interval_seconds": REFLECTION_INTERVAL,
        }

    def get_recent_thoughts(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the N most recent thoughts for the dashboard."""
        return [t.to_dict() for t in reversed(self._thoughts[-n:])]

    async def force_reflect(self) -> List[Dict[str, Any]]:
        """Manually trigger a reflection cycle (for dashboard/testing)."""
        new_thoughts = await self._reflect()
        self._cycle_count += 1
        self._last_cycle_at = datetime.now().isoformat()
        self._save()
        return [t.to_dict() for t in new_thoughts]
