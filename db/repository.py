from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc, text

from .models import UserProfile, Memory, Conversation, AuditLog, MemoryType


class UserProfileRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: str) -> Optional[UserProfile]:
        return self.db.query(UserProfile).filter(UserProfile.user_id == user_id).first()

    def create(self, user_id: str, nombre: str, data: Dict[str, Any]) -> UserProfile:
        profile = UserProfile(
            user_id=user_id,
            nombre=nombre,
            estilos=data.get("estilos", {}),
            preferencias_tecnicas=data.get("preferencias_tecnicas", {}),
            prioridades_actuales=data.get("prioridades_actuales", []),
            decisiones_previas=data.get("decisiones_previas", []),
            humor_contexto=data.get("humor_contexto", "neutro"),
        )
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def update_humor(self, user_id: str, humor: str) -> None:
        self.db.query(UserProfile).filter(
            UserProfile.user_id == user_id
        ).update({"humor_contexto": humor, "updated_at": datetime.utcnow()})
        self.db.commit()

    def update_priorities(self, user_id: str, priorities: List[str]) -> None:
        self.db.query(UserProfile).filter(
            UserProfile.user_id == user_id
        ).update({"prioridades_actuales": priorities, "updated_at": datetime.utcnow()})
        self.db.commit()


class MemoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        user_id: str,
        content: str,
        memory_type: MemoryType,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Memory:
        memory = Memory(
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            embedding=embedding if embedding else None,
            metadata_=metadata or {},
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def get_recent(self, user_id: str, limit: int = 50, memory_type: Optional[MemoryType] = None) -> List[Memory]:
        q = self.db.query(Memory).filter(Memory.user_id == user_id)
        if memory_type:
            q = q.filter(Memory.memory_type == memory_type)
        return q.order_by(desc(Memory.created_at)).limit(limit).all()

    def search_by_vector(self, user_id: str, embedding: List[float], top_k: int = 5) -> List[Memory]:
        """Búsqueda vectorial con pgvector. Fallback a scan si no hay soporte."""
        try:
            vec_str = f"[{','.join(str(x) for x in embedding)}]"
            results = self.db.execute(
                text("""
                    SELECT id, content, memory_type, relevance_score, metadata, created_at,
                           1 - (embedding <=> :vec::vector) AS similarity
                    FROM memories
                    WHERE user_id = :uid AND embedding IS NOT NULL
                    ORDER BY embedding <=> :vec::vector
                    LIMIT :k
                """),
                {"vec": vec_str, "uid": user_id, "k": top_k}
            ).fetchall()
            return results
        except Exception:
            # Fallback: retorna las más recientes
            return self.get_recent(user_id, limit=top_k)


class ConversationRepository:
    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        user_id: str,
        role: str,
        content: str,
        cycle_id: Optional[str] = None,
        intent: Optional[Dict] = None,
        plan: Optional[List] = None,
        provider_used: Optional[str] = None,
    ) -> Conversation:
        conv = Conversation(
            user_id=user_id,
            cycle_id=cycle_id,
            role=role,
            content=content,
            intent=intent,
            plan=plan,
            provider_used=provider_used,
        )
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        return conv

    def get_recent(self, user_id: str, n: int = 20) -> List[Conversation]:
        return (
            self.db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(desc(Conversation.created_at))
            .limit(n)
            .all()
        )

    def get_by_cycle(self, cycle_id: str) -> List[Conversation]:
        return (
            self.db.query(Conversation)
            .filter(Conversation.cycle_id == cycle_id)
            .order_by(Conversation.created_at)
            .all()
        )


class AuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        user_id: str,
        action: str,
        action_type: str,
        details: Optional[Dict] = None,
        cycle_id: Optional[str] = None,
    ) -> AuditLog:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            action_type=action_type,
            details=details or {},
            cycle_id=cycle_id,
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def get_recent(self, user_id: str, limit: int = 50) -> List[AuditLog]:
        return (
            self.db.query(AuditLog)
            .filter(AuditLog.user_id == user_id)
            .order_by(desc(AuditLog.created_at))
            .limit(limit)
            .all()
        )
