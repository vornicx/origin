from sqlalchemy import (
    Column, String, Text, Float, DateTime, ForeignKey,
    JSON, Enum as SAEnum
)
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from .connection import Base

try:
    from pgvector.sqlalchemy import Vector
    # Verifica que la extensión pgvector esté realmente disponible en la DB
    from .connection import engine
    from sqlalchemy import text as _text
    with engine.connect() as _conn:
        _result = _conn.execute(_text("SELECT 1 FROM pg_extension WHERE extname='vector'"))
        VECTOR_SUPPORT = _result.fetchone() is not None
    if not VECTOR_SUPPORT:
        Vector = None  # noqa: F811
except Exception:
    VECTOR_SUPPORT = False
    Vector = None


def _uuid():
    return str(uuid.uuid4())


class MemoryType(str, enum.Enum):
    CONVERSATION = "conversation"
    DECISION = "decision"
    LEARNING = "learning"
    PROFILE = "profile"
    SKILL_RESULT = "skill_result"


class UserProfile(Base):
    """Perfil del usuario. Una fila por usuario."""
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, unique=True, nullable=False, index=True)  # "vadim_vornic"
    nombre = Column(String, nullable=False)
    estilos = Column(JSON, nullable=False, default=dict)
    preferencias_tecnicas = Column(JSON, nullable=False, default=dict)
    prioridades_actuales = Column(JSON, nullable=False, default=list)
    decisiones_previas = Column(JSON, nullable=False, default=list)
    humor_contexto = Column(String, default="neutro")
    interrupt_mode = Column(String, default="focus")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")


class Memory(Base):
    """Memoria con embedding vectorial para búsqueda semántica."""
    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("user_profiles.user_id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    memory_type = Column(
        SAEnum(MemoryType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=MemoryType.CONVERSATION,
    )
    relevance_score = Column(Float, default=0.0)
    metadata_ = Column("metadata", JSON, default=dict)

    # Vector embedding (384 dims para MiniLM-L6-v2)
    # Si pgvector no está disponible, se guarda como Text (JSON serializado)
    if VECTOR_SUPPORT:
        embedding = Column(Vector(384), nullable=True)
    else:
        embedding = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("UserProfile", back_populates="memories")


class Conversation(Base):
    """Historial de conversaciones."""
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("user_profiles.user_id"), nullable=False, index=True)
    cycle_id = Column(String, nullable=True, index=True)  # Vinculado a un ciclo de razonamiento
    role = Column(String, nullable=False)   # "user" | "assistant"
    content = Column(Text, nullable=False)
    intent = Column(JSON, nullable=True)    # Intent parseado (step 1)
    plan = Column(JSON, nullable=True)      # Plan generado (step 2)
    provider_used = Column(String, nullable=True)  # Qué LLM respondió

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("UserProfile", back_populates="conversations")


class AuditLog(Base):
    """Log de acciones críticas. Solo INSERT, nunca UPDATE/DELETE."""
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)      # "web_search", "delete_file", etc.
    action_type = Column(String, nullable=False)  # "auto_allowed" | "confirmed" | "blocked"
    details = Column(JSON, default=dict)
    cycle_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
