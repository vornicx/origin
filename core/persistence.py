import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("origin.mind.persistence")

_DATA_DIR = Path(__file__).parent.parent / "data"
_SQLITE_PATH = _DATA_DIR / "origin.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'general',
    metadata    TEXT,
    created_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(content, memory_type, content=memories, content_rowid=rowid);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, memory_type)
    VALUES (new.rowid, new.content, new.memory_type);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, memory_type)
    VALUES ('delete', old.rowid, old.content, old.memory_type);
    INSERT INTO memories_fts(rowid, content, memory_type)
    VALUES (new.rowid, new.content, new.memory_type);
END;

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    cycle_id    TEXT,
    intent      TEXT,
    plan        TEXT,
    provider    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    action_type TEXT NOT NULL,
    details     TEXT,
    cycle_id    TEXT,
    created_at  TEXT NOT NULL
);
"""


class _SQLitePersistence:
    """SQLite backend — activated when PostgreSQL is unavailable.

    Provides FTS5-based text search for memories and full conversation history.
    Not a replacement for vector search, but significantly better than RAM-only.
    """

    def __init__(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._path = str(_SQLITE_PATH)
        self._init_schema()
        logger.info(f"SQLite persistence active: {self._path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def save_memory(
        self,
        user_id: str,
        content: str,
        memory_type: str,
        metadata: Optional[dict] = None,
        mem_id: Optional[str] = None,
    ) -> str:
        mid = mem_id or str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories (id, user_id, content, memory_type, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, user_id, content, memory_type, json.dumps(metadata or {}), datetime.utcnow().isoformat()),
            )
        return mid

    def search_memories(self, user_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Full-text search over memories using FTS5."""
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT m.id, m.content, m.memory_type, m.metadata, m.created_at, "
                    "       rank AS score "
                    "FROM memories_fts "
                    "JOIN memories m ON m.rowid = memories_fts.rowid "
                    "WHERE memories_fts MATCH ? AND m.user_id = ? "
                    "ORDER BY rank LIMIT ?",
                    (query, user_id, top_k),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                # FTS5 not supported or query syntax error — fallback to LIKE
                rows = conn.execute(
                    "SELECT id, content, memory_type, metadata, created_at "
                    "FROM memories WHERE user_id = ? AND content LIKE ? LIMIT ?",
                    (user_id, f"%{query}%", top_k),
                ).fetchall()
                return [dict(r) for r in rows]

    def save_conversation(
        self,
        user_id: str,
        role: str,
        content: str,
        cycle_id: Optional[str] = None,
        intent: Optional[dict] = None,
        plan: Optional[list] = None,
        provider: Optional[str] = None,
    ):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (user_id, role, content, cycle_id, intent, plan, provider, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, role, content, cycle_id,
                    json.dumps(intent) if intent else None,
                    json.dumps(plan) if plan else None,
                    provider,
                    datetime.utcnow().isoformat(),
                ),
            )

    def get_recent_conversations(self, user_id: str, n: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM conversations "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, n),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def save_audit(
        self,
        user_id: str,
        action: str,
        action_type: str,
        details: Optional[dict] = None,
        cycle_id: Optional[str] = None,
    ):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (user_id, action, action_type, details, cycle_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, action, action_type, json.dumps(details or {}), cycle_id, datetime.utcnow().isoformat()),
            )


class PersistenceManager:
    """Maneja persistencia con fallback en cascada: PostgreSQL → SQLite → RAM.

    - PostgreSQL: persistencia completa con búsqueda vectorial
    - SQLite: persistencia con FTS5 (full-text search) cuando no hay PG
    - RAM: sin persistencia entre reinicios (último recurso)
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._db_available = False
        self._sqlite: Optional[_SQLitePersistence] = None
        self._SessionLocal = None
        self._MemoryRepository = None
        self._ConversationRepository = None
        self._AuditRepository = None
        self._MemoryType = None
        self._init_db()

    def _init_db(self):
        # Try PostgreSQL first
        try:
            from db.connection import SessionLocal
            from db.repository import MemoryRepository, ConversationRepository, AuditRepository
            from db.models import MemoryType
            import sqlalchemy

            self._SessionLocal = SessionLocal
            self._MemoryRepository = MemoryRepository
            self._ConversationRepository = ConversationRepository
            self._AuditRepository = AuditRepository
            self._MemoryType = MemoryType
            db = SessionLocal()
            db.execute(sqlalchemy.text("SELECT 1"))
            db.close()
            self._db_available = True
            logger.info("Database persistence layer: PostgreSQL ACTIVE")
            return
        except Exception as e:
            logger.warning(f"PostgreSQL unavailable ({e}), trying SQLite fallback")

        # SQLite fallback
        try:
            self._sqlite = _SQLitePersistence()
            logger.info("Database persistence layer: SQLite ACTIVE")
        except Exception as e:
            logger.warning(f"SQLite also unavailable: {e} — running in RAM-only mode")

    @property
    def backend(self) -> str:
        if self._db_available:
            return "postgresql"
        if self._sqlite:
            return "sqlite"
        return "ram"

    def _get_pg_db(self):
        if not self._db_available:
            return None
        return self._SessionLocal()

    def persist_memory(self, content: str, memory_type_str: str, metadata: dict = None, embedding: list = None):
        if self._db_available:
            db = None
            try:
                db = self._get_pg_db()
                repo = self._MemoryRepository(db)
                mt = self._MemoryType(memory_type_str)
                repo.save(
                    user_id=self.user_id,
                    content=content,
                    memory_type=mt,
                    embedding=embedding,
                    metadata=metadata,
                )
                logger.debug(f"Memory persisted to PostgreSQL: {memory_type_str}")
            except Exception as e:
                logger.warning(f"Failed to persist memory to PG: {e}")
            finally:
                if db:
                    db.close()
        elif self._sqlite:
            try:
                self._sqlite.save_memory(self.user_id, content, memory_type_str, metadata)
                logger.debug(f"Memory persisted to SQLite: {memory_type_str}")
            except Exception as e:
                logger.warning(f"Failed to persist memory to SQLite: {e}")

    def persist_conversation(
        self,
        role: str,
        content: str,
        cycle_id: str = None,
        intent: dict = None,
        plan: list = None,
        provider: str = None,
    ):
        if self._db_available:
            db = None
            try:
                db = self._get_pg_db()
                repo = self._ConversationRepository(db)
                repo.save(
                    user_id=self.user_id,
                    role=role,
                    content=content,
                    cycle_id=cycle_id,
                    intent=intent,
                    plan=plan,
                    provider_used=provider,
                )
                logger.debug(f"Conversation persisted to PostgreSQL: {role}")
            except Exception as e:
                logger.warning(f"Failed to persist conversation to PG: {e}")
            finally:
                if db:
                    db.close()
        elif self._sqlite:
            try:
                self._sqlite.save_conversation(self.user_id, role, content, cycle_id, intent, plan, provider)
                logger.debug(f"Conversation persisted to SQLite: {role}")
            except Exception as e:
                logger.warning(f"Failed to persist conversation to SQLite: {e}")

    def persist_audit(self, action: str, action_type: str, details: dict = None, cycle_id: str = None):
        if self._db_available:
            db = None
            try:
                db = self._get_pg_db()
                repo = self._AuditRepository(db)
                repo.log(
                    user_id=self.user_id,
                    action=action,
                    action_type=action_type,
                    details=details,
                    cycle_id=cycle_id,
                )
            except Exception as e:
                logger.warning(f"Failed to persist audit to PG: {e}")
            finally:
                if db:
                    db.close()
        elif self._sqlite:
            try:
                self._sqlite.save_audit(self.user_id, action, action_type, details, cycle_id)
            except Exception as e:
                logger.warning(f"Failed to persist audit to SQLite: {e}")

    def search_memories_text(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """FTS5-based text search (SQLite only). Returns [] when using PostgreSQL
        (vector search is handled by ChromaDB/MemoryManager instead)."""
        if self._sqlite and not self._db_available:
            return self._sqlite.search_memories(self.user_id, query, top_k)
        return []

    async def generate_summary(self, memory_mgr, llm_router) -> None:
        from core.memory_manager import SUMMARY_CHECKPOINT_EVERY

        recent = memory_mgr.get_recent_conversation(n=SUMMARY_CHECKPOINT_EVERY)
        if len(recent) < 5:
            return

        turns_text = "\n".join(
            f"{e['role'].upper()} [msg#{e.get('idx', '?')} tema_{e.get('topic_id', 0)}]: {e['message'][:200]}"
            for e in recent
        )

        result = await llm_router.call_llm(
            prompt=(
                "Resume la siguiente conversación en 3-5 oraciones densas. "
                "Preserva: hechos concretos, decisiones tomadas, contexto técnico, estado de tareas pendientes.\n\n"
                f"{turns_text}"
            ),
            system_message=(
                "Eres un sistema de compresión de contexto para un asistente IA. "
                "Tu resumen será inyectado como contexto comprimido en futuras respuestas. "
                "Sé denso y preciso. NO uses bullet points, escribe prosa compacta."
            ),
            temperature=0.2,
            task_type="summarization",
        )
        if result.get("success"):
            memory_mgr.record_checkpoint(result["content"])
            logger.info(f"Context checkpoint generated ({len(result['content'])} chars)")
        else:
            logger.warning(f"Checkpoint generation failed: {result.get('error', '')[:100]}")
