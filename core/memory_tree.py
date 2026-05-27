"""
MemoryTree — Summarización jerárquica de memoria a largo plazo.

Estructura:   hora → día → mes → año → root (identidad)

Cada nivel consolida el anterior en resúmenes cada vez más densos.
El root node contiene la esencia de toda la interacción con el usuario
(personalidad, preferencias profundas, patrones).

Inspirado en OpenHuman memory architecture.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("origin.memory_tree")

# ── Config ────────────────────────────────────────────────────
TREE_DIR = Path(__file__).parent.parent / "data" / "memory"
TREE_FILE = TREE_DIR / "memory_tree.json"

# Consolidation thresholds
HOUR_MIN_MESSAGES = 6  # min messages to create an hour node
DAY_MIN_HOURS = 2  # min hour nodes to create a day node
MONTH_MIN_DAYS = 3  # min day nodes to create a month node
YEAR_MIN_MONTHS = 2  # min month nodes to create a year node

# Max nodes to keep per level (oldest get pruned after summarization)
MAX_HOUR_NODES = 72  # ~3 days of hours
MAX_DAY_NODES = 90  # ~3 months of days
MAX_MONTH_NODES = 24  # 2 years of months

# ── Prompts per level ─────────────────────────────────────────

PROMPTS = {
    "hour": {
        "system": (
            "Eres un sistema de compresión de memoria de corto plazo. "
            "Genera un resumen denso de la última hora de conversación. "
            "Preserva: hechos concretos, decisiones tomadas, tareas pendientes, "
            "emociones detectadas, temas técnicos. Máximo 4 oraciones."
        ),
        "user": "Resume esta hora de conversación:\n\n{text}",
    },
    "day": {
        "system": (
            "Eres un sistema de consolidación de memoria diaria. "
            "A partir de resúmenes por hora, genera un resumen del día completo. "
            "Enfócate en: logros del día, decisiones importantes, problemas resueltos, "
            "tareas pendientes, cambios de humor/tono. Máximo 5 oraciones."
        ),
        "user": "Resúmenes por hora del día {date}:\n\n{text}",
    },
    "month": {
        "system": (
            "Eres un sistema de consolidación de memoria mensual. "
            "A partir de resúmenes diarios, identifica patrones y tendencias del mes. "
            "Enfócate en: proyectos activos, habilidades desarrolladas, preferencias "
            "emergentes, patrones de uso, evolución del usuario. Máximo 5 oraciones."
        ),
        "user": "Resúmenes diarios del mes {date}:\n\n{text}",
    },
    "year": {
        "system": (
            "Eres un sistema de consolidación de memoria anual. "
            "A partir de resúmenes mensuales, genera una visión panorámica del año. "
            "Enfócate en: evolución general, grandes proyectos, cambios en preferencias "
            "y hábitos, hitos importantes. Máximo 5 oraciones."
        ),
        "user": "Resúmenes mensuales del año {date}:\n\n{text}",
    },
    "root": {
        "system": (
            "Eres el módulo de identidad de un asistente IA personal. "
            "A partir de toda la historia con el usuario, genera un perfil de identidad "
            "que capture: quién es el usuario, sus valores, su forma de trabajar, sus "
            "proyectos principales, sus preferencias profundas, cómo le gusta que le hablen. "
            "Este perfil se inyecta en cada respuesta futura. Máximo 6 oraciones. "
            "Escribe en segunda persona (tú/tu usuario)."
        ),
        "user": "Historia completa con el usuario:\n\n{text}",
    },
}


class MemoryTreeNode:
    """Un nodo del árbol de memoria."""

    __slots__ = ("key", "level", "summary", "message_count", "topics", "created_at", "source_keys")

    def __init__(
        self,
        key: str,
        level: str,
        summary: str,
        message_count: int = 0,
        topics: Optional[List[str]] = None,
        created_at: Optional[str] = None,
        source_keys: Optional[List[str]] = None,
    ):
        self.key = key
        self.level = level
        self.summary = summary
        self.message_count = message_count
        self.topics = topics or []
        self.created_at = created_at or datetime.now().isoformat()
        self.source_keys = source_keys or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "level": self.level,
            "summary": self.summary,
            "message_count": self.message_count,
            "topics": self.topics,
            "created_at": self.created_at,
            "source_keys": self.source_keys,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryTreeNode":
        return cls(
            key=data["key"],
            level=data["level"],
            summary=data["summary"],
            message_count=data.get("message_count", 0),
            topics=data.get("topics", []),
            created_at=data.get("created_at"),
            source_keys=data.get("source_keys", []),
        )


class MemoryTree:
    """
    Árbol jerárquico de memoria: hora → día → mes → año → root.

    Cada nivel consolida el anterior mediante LLM summarization.
    El árbol persiste en JSON y se actualiza en background.
    """

    def __init__(self):
        self._nodes: Dict[str, MemoryTreeNode] = {}
        self._root_summary: str = ""
        self._root_updated_at: Optional[str] = None
        self._pending_messages: List[Dict[str, Any]] = []
        self._last_hour_key: Optional[str] = None

        TREE_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

        node_counts = {}
        for n in self._nodes.values():
            node_counts[n.level] = node_counts.get(n.level, 0) + 1
        logger.info(
            f"MemoryTree loaded: {len(self._nodes)} nodes "
            f"({node_counts}) root={'yes' if self._root_summary else 'no'}"
        )

    # ── Persistence ────────────────────────────────────────────

    def _load(self):
        if not TREE_FILE.exists():
            return
        try:
            data = json.loads(TREE_FILE.read_text(encoding="utf-8"))
            for node_data in data.get("nodes", []):
                try:
                    node = MemoryTreeNode.from_dict(node_data)
                    self._nodes[node.key] = node
                except Exception as e:
                    logger.warning(f"Skip corrupt tree node: {e}")
            self._root_summary = data.get("root_summary", "")
            self._root_updated_at = data.get("root_updated_at")
            self._pending_messages = data.get("pending_messages", [])
            self._last_hour_key = data.get("last_hour_key")
        except Exception as e:
            logger.warning(f"Failed to load memory tree: {e}")

    def _save(self):
        try:
            data = {
                "nodes": [n.to_dict() for n in self._nodes.values()],
                "root_summary": self._root_summary,
                "root_updated_at": self._root_updated_at,
                "pending_messages": self._pending_messages[-50:],
                "last_hour_key": self._last_hour_key,
                "saved_at": datetime.now().isoformat(),
                "node_count": len(self._nodes),
            }
            TREE_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save memory tree: {e}")

    # ── Message ingestion ──────────────────────────────────────

    def ingest_message(self, role: str, content: str, timestamp: Optional[str] = None):
        """Ingiere un mensaje para futura consolidación horaria."""
        self._pending_messages.append(
            {
                "role": role,
                "content": content[:500],
                "timestamp": timestamp or datetime.now().isoformat(),
            }
        )
        # Cap pending buffer
        if len(self._pending_messages) > 100:
            self._pending_messages = self._pending_messages[-100:]
        self._save()

    # ── Key generation helpers ─────────────────────────────────

    @staticmethod
    def _hour_key(dt: datetime) -> str:
        return dt.strftime("hour_%Y%m%d_%H")

    @staticmethod
    def _day_key(dt: datetime) -> str:
        return dt.strftime("day_%Y%m%d")

    @staticmethod
    def _month_key(dt: datetime) -> str:
        return dt.strftime("month_%Y%m")

    @staticmethod
    def _year_key(dt: datetime) -> str:
        return dt.strftime("year_%Y")

    # ── Level queries ──────────────────────────────────────────

    def _get_nodes(self, level: str) -> List[MemoryTreeNode]:
        """Return all nodes for a level, sorted by key (chronological)."""
        return sorted(
            [n for n in self._nodes.values() if n.level == level],
            key=lambda n: n.key,
        )

    def _get_unsummarized(self, child_level: str, parent_level: str, key_prefix: str) -> List[MemoryTreeNode]:
        """Return child nodes that haven't been rolled up into a parent yet."""
        # Collect all source_keys from parent nodes with matching prefix
        parent_sources = set()
        for n in self._nodes.values():
            if n.level == parent_level and n.key.startswith(key_prefix):
                parent_sources.update(n.source_keys)

        return [
            n
            for n in self._nodes.values()
            if n.level == child_level and n.key.startswith(key_prefix) and n.key not in parent_sources
        ]

    # ── Consolidation: Hour ────────────────────────────────────

    async def consolidate_hour(self, llm_router) -> Optional[str]:
        """Consolidate pending messages into an hour node."""
        if len(self._pending_messages) < HOUR_MIN_MESSAGES:
            return None

        now = datetime.now()
        hour_key = self._hour_key(now)

        # Don't re-consolidate same hour
        if hour_key == self._last_hour_key and hour_key in self._nodes:
            return None

        # Build text from pending messages
        text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in self._pending_messages)

        topics = self._extract_topics(text)

        prompt_cfg = PROMPTS["hour"]
        result = await llm_router.call_llm(
            prompt=prompt_cfg["user"].format(text=text[:3000]),
            system_message=prompt_cfg["system"],
            temperature=0.2,
            task_type="memory_consolidation",
        )

        if not result.get("success"):
            logger.warning(f"Hour consolidation failed: {result.get('error', '')[:80]}")
            return None

        summary = result["content"]
        node = MemoryTreeNode(
            key=hour_key,
            level="hour",
            summary=summary,
            message_count=len(self._pending_messages),
            topics=topics,
        )
        self._nodes[hour_key] = node
        self._last_hour_key = hour_key
        self._pending_messages = []

        # Prune old hour nodes
        self._prune_level("hour", MAX_HOUR_NODES)
        self._save()

        logger.info(f"Hour consolidated: {hour_key} ({node.message_count} msgs, {len(summary)} chars)")
        return hour_key

    # ── Consolidation: Day ─────────────────────────────────────

    async def consolidate_day(self, llm_router, target_date: Optional[datetime] = None) -> Optional[str]:
        """Roll up hour nodes into a day node."""
        dt = target_date or datetime.now()
        day_key = self._day_key(dt)
        day_prefix = dt.strftime("hour_%Y%m%d")

        # Find hour nodes for this day not yet rolled up
        hour_nodes = [n for n in self._nodes.values() if n.level == "hour" and n.key.startswith(day_prefix)]

        # Check if day node already exists and covers all hours
        existing = self._nodes.get(day_key)
        if existing and len(hour_nodes) <= len(existing.source_keys):
            return None

        if len(hour_nodes) < DAY_MIN_HOURS:
            return None

        hour_nodes.sort(key=lambda n: n.key)
        text = "\n\n".join(f"[{n.key}] ({n.message_count} msgs): {n.summary}" for n in hour_nodes)
        all_topics = []
        total_msgs = 0
        for n in hour_nodes:
            all_topics.extend(n.topics)
            total_msgs += n.message_count

        prompt_cfg = PROMPTS["day"]
        result = await llm_router.call_llm(
            prompt=prompt_cfg["user"].format(date=dt.strftime("%Y-%m-%d"), text=text[:4000]),
            system_message=prompt_cfg["system"],
            temperature=0.2,
            task_type="memory_consolidation",
        )

        if not result.get("success"):
            logger.warning(f"Day consolidation failed: {result.get('error', '')[:80]}")
            return None

        summary = result["content"]
        node = MemoryTreeNode(
            key=day_key,
            level="day",
            summary=summary,
            message_count=total_msgs,
            topics=list(set(all_topics))[:10],
            source_keys=[n.key for n in hour_nodes],
        )
        self._nodes[day_key] = node
        self._prune_level("day", MAX_DAY_NODES)
        self._save()

        logger.info(f"Day consolidated: {day_key} ({len(hour_nodes)} hours, {total_msgs} msgs)")
        return day_key

    # ── Consolidation: Month ───────────────────────────────────

    async def consolidate_month(self, llm_router, target_date: Optional[datetime] = None) -> Optional[str]:
        """Roll up day nodes into a month node."""
        dt = target_date or datetime.now()
        month_key = self._month_key(dt)
        day_prefix = dt.strftime("day_%Y%m")

        day_nodes = [n for n in self._nodes.values() if n.level == "day" and n.key.startswith(day_prefix)]

        existing = self._nodes.get(month_key)
        if existing and len(day_nodes) <= len(existing.source_keys):
            return None

        if len(day_nodes) < MONTH_MIN_DAYS:
            return None

        day_nodes.sort(key=lambda n: n.key)
        text = "\n\n".join(f"[{n.key}] ({n.message_count} msgs): {n.summary}" for n in day_nodes)
        all_topics = []
        total_msgs = 0
        for n in day_nodes:
            all_topics.extend(n.topics)
            total_msgs += n.message_count

        prompt_cfg = PROMPTS["month"]
        result = await llm_router.call_llm(
            prompt=prompt_cfg["user"].format(date=dt.strftime("%Y-%m"), text=text[:5000]),
            system_message=prompt_cfg["system"],
            temperature=0.3,
            task_type="memory_consolidation",
        )

        if not result.get("success"):
            logger.warning(f"Month consolidation failed: {result.get('error', '')[:80]}")
            return None

        summary = result["content"]
        node = MemoryTreeNode(
            key=month_key,
            level="month",
            summary=summary,
            message_count=total_msgs,
            topics=list(set(all_topics))[:15],
            source_keys=[n.key for n in day_nodes],
        )
        self._nodes[month_key] = node
        self._prune_level("month", MAX_MONTH_NODES)
        self._save()

        logger.info(f"Month consolidated: {month_key} ({len(day_nodes)} days, {total_msgs} msgs)")
        return month_key

    # ── Consolidation: Year ────────────────────────────────────

    async def consolidate_year(self, llm_router, target_date: Optional[datetime] = None) -> Optional[str]:
        """Roll up month nodes into a year node."""
        dt = target_date or datetime.now()
        year_key = self._year_key(dt)
        month_prefix = dt.strftime("month_%Y")

        month_nodes = [n for n in self._nodes.values() if n.level == "month" and n.key.startswith(month_prefix)]

        existing = self._nodes.get(year_key)
        if existing and len(month_nodes) <= len(existing.source_keys):
            return None

        if len(month_nodes) < YEAR_MIN_MONTHS:
            return None

        month_nodes.sort(key=lambda n: n.key)
        text = "\n\n".join(f"[{n.key}] ({n.message_count} msgs): {n.summary}" for n in month_nodes)
        total_msgs = sum(n.message_count for n in month_nodes)

        prompt_cfg = PROMPTS["year"]
        result = await llm_router.call_llm(
            prompt=prompt_cfg["user"].format(date=dt.strftime("%Y"), text=text[:6000]),
            system_message=prompt_cfg["system"],
            temperature=0.3,
            task_type="memory_consolidation",
        )

        if not result.get("success"):
            return None

        node = MemoryTreeNode(
            key=year_key,
            level="year",
            summary=result["content"],
            message_count=total_msgs,
            source_keys=[n.key for n in month_nodes],
        )
        self._nodes[year_key] = node
        self._save()

        logger.info(f"Year consolidated: {year_key} ({len(month_nodes)} months)")
        return year_key

    # ── Root identity ──────────────────────────────────────────

    async def update_root(self, llm_router) -> bool:
        """Update the root identity summary from highest-level nodes."""
        # Gather: year summaries + latest month + latest day
        year_nodes = self._get_nodes("year")
        month_nodes = self._get_nodes("month")
        day_nodes = self._get_nodes("day")

        parts = []
        for n in year_nodes[-3:]:
            parts.append(f"[{n.key}]: {n.summary}")
        for n in month_nodes[-3:]:
            parts.append(f"[{n.key}]: {n.summary}")
        for n in day_nodes[-5:]:
            parts.append(f"[{n.key}]: {n.summary}")

        if not parts:
            return False

        text = "\n\n".join(parts)

        prompt_cfg = PROMPTS["root"]
        result = await llm_router.call_llm(
            prompt=prompt_cfg["user"].format(text=text[:6000]),
            system_message=prompt_cfg["system"],
            temperature=0.4,
            task_type="memory_consolidation",
        )

        if not result.get("success"):
            return False

        self._root_summary = result["content"]
        self._root_updated_at = datetime.now().isoformat()
        self._save()

        logger.info(f"Root identity updated ({len(self._root_summary)} chars)")
        return True

    # ── Auto-consolidation (call from background) ──────────────

    async def auto_consolidate(self, llm_router) -> Dict[str, Any]:
        """Run all pending consolidations. Returns what was consolidated."""
        results = {
            "hour": None,
            "day": None,
            "month": None,
            "year": None,
            "root": False,
        }

        try:
            # 1. Hour: always try if enough pending messages
            results["hour"] = await self.consolidate_hour(llm_router)

            # 2. Day: try for today and yesterday
            now = datetime.now()
            results["day"] = await self.consolidate_day(llm_router, now)
            if not results["day"]:
                yesterday = now - timedelta(days=1)
                results["day"] = await self.consolidate_day(llm_router, yesterday)

            # 3. Month: try current month
            results["month"] = await self.consolidate_month(llm_router, now)

            # 4. Year: try current year
            results["year"] = await self.consolidate_year(llm_router, now)

            # 5. Root: update if month or year changed, or if stale (>24h)
            root_stale = False
            if self._root_updated_at:
                try:
                    root_age = (datetime.now() - datetime.fromisoformat(self._root_updated_at)).total_seconds()
                    root_stale = root_age > 86400  # 24 hours
                except Exception:
                    root_stale = True
            else:
                root_stale = True  # Never updated

            if results["month"] or results["year"] or root_stale:
                results["root"] = await self.update_root(llm_router)

        except Exception as e:
            logger.error(f"Auto-consolidation error: {e}")

        consolidated = [k for k, v in results.items() if v]
        if consolidated:
            logger.info(f"Auto-consolidation done: {consolidated}")

        return results

    # ── Context retrieval for reasoning loop ───────────────────

    def get_context_layers(self) -> Dict[str, Any]:
        """Return multi-scale memory context for the reasoning loop.

        Returns summaries from each relevant level:
        - root: identity/personality (always)
        - current month: recent patterns
        - today: what happened today
        - last 2-3 hour nodes: immediate context
        """
        now = datetime.now()
        layers: Dict[str, Any] = {
            "root_identity": self._root_summary or None,
            "root_updated_at": self._root_updated_at,
        }

        # Current month
        month_key = self._month_key(now)
        month_node = self._nodes.get(month_key)
        if month_node:
            layers["current_month"] = {
                "summary": month_node.summary,
                "topics": month_node.topics,
                "message_count": month_node.message_count,
            }

        # Today
        day_key = self._day_key(now)
        day_node = self._nodes.get(day_key)
        if day_node:
            layers["today"] = {
                "summary": day_node.summary,
                "topics": day_node.topics,
                "message_count": day_node.message_count,
            }

        # Recent hours (last 3)
        hour_nodes = sorted(
            [n for n in self._nodes.values() if n.level == "hour"],
            key=lambda n: n.key,
            reverse=True,
        )[:3]
        if hour_nodes:
            layers["recent_hours"] = [
                {
                    "key": n.key,
                    "summary": n.summary,
                    "message_count": n.message_count,
                    "topics": n.topics,
                }
                for n in hour_nodes
            ]

        # Pending messages count (not yet consolidated)
        layers["pending_messages"] = len(self._pending_messages)

        return layers

    def get_formatted_context(self) -> str:
        """Return a formatted string of memory tree context for LLM injection."""
        layers = self.get_context_layers()
        parts = []

        if layers.get("root_identity"):
            parts.append(f"[IDENTIDAD DEL USUARIO]\n{layers['root_identity']}")

        if layers.get("current_month"):
            m = layers["current_month"]
            parts.append(f"[ESTE MES] ({m['message_count']} msgs)\n{m['summary']}")

        if layers.get("today"):
            d = layers["today"]
            parts.append(f"[HOY] ({d['message_count']} msgs)\n{d['summary']}")

        if layers.get("recent_hours"):
            hour_parts = []
            for h in layers["recent_hours"]:
                hour_parts.append(f"  - {h['key']}: {h['summary']}")
            parts.append("[HORAS RECIENTES]\n" + "\n".join(hour_parts))

        if not parts:
            return ""

        return "\n\n".join(parts)

    # ── Utilities ──────────────────────────────────────────────

    @staticmethod
    def _extract_topics(text: str) -> List[str]:
        """Extract topic keywords from text."""
        import re

        tech_keywords = {
            "python",
            "javascript",
            "typescript",
            "rust",
            "api",
            "docker",
            "git",
            "database",
            "frontend",
            "backend",
            "react",
            "css",
            "tts",
            "voz",
            "voice",
            "memoria",
            "memory",
            "llm",
            "gpt",
            "skill",
            "error",
            "bug",
            "fix",
            "test",
            "deploy",
            "config",
            "websocket",
            "http",
            "json",
            "html",
            "audio",
            "video",
            "origin",
            "openai",
            "anthropic",
            "groq",
            "deepseek",
            "gemini",
            "elevenlabs",
            "fish",
            "edge",
            "ollama",
            "nvidia",
        }
        words = set(re.findall(r"\b\w{3,}\b", text.lower()))
        found = words & tech_keywords
        return sorted(found)[:8]

    def _prune_level(self, level: str, max_nodes: int):
        """Remove oldest nodes when exceeding max."""
        nodes = self._get_nodes(level)
        if len(nodes) <= max_nodes:
            return
        to_remove = nodes[: len(nodes) - max_nodes]
        for n in to_remove:
            del self._nodes[n.key]
        logger.debug(f"Pruned {len(to_remove)} old {level} nodes")

    def get_stats(self) -> Dict[str, Any]:
        """Stats for dashboard."""
        level_counts = {}
        for n in self._nodes.values():
            level_counts[n.level] = level_counts.get(n.level, 0) + 1

        return {
            "total_nodes": len(self._nodes),
            "by_level": level_counts,
            "pending_messages": len(self._pending_messages),
            "has_root": bool(self._root_summary),
            "root_updated_at": self._root_updated_at,
            "tree_file": str(TREE_FILE),
            "tree_file_size_kb": (round(TREE_FILE.stat().st_size / 1024, 1) if TREE_FILE.exists() else 0),
        }
