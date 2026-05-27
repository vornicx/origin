"""Initial schema: user_profiles, memories, conversations, audit_log

Revision ID: 001
Revises:
Create Date: 2026-05-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Habilita extensión pgvector si está disponible (no falla si no existe)
    op.execute("""
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector not available, using JSON fallback for embeddings';
        END $$;
    """)

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("estilos", sa.JSON(), nullable=False),
        sa.Column("preferencias_tecnicas", sa.JSON(), nullable=False),
        sa.Column("prioridades_actuales", sa.JSON(), nullable=False),
        sa.Column("decisiones_previas", sa.JSON(), nullable=False),
        sa.Column("humor_contexto", sa.String(), nullable=True),
        sa.Column("interrupt_mode", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_profiles_user_id", "user_profiles", ["user_id"], unique=True)

    op.create_table(
        "memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "memory_type",
            sa.Enum("conversation", "decision", "learning", "profile", "skill_result", name="memorytype"),
            nullable=False,
        ),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),  # JSON fallback
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_index("ix_memories_created_at", "memories", ["created_at"])

    # Agrega columna vector si pgvector está disponible
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE memories ADD COLUMN embedding_vec vector(384);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector column not added (extension not available)';
        END $$;
    """)

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("cycle_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("provider_used", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_cycle_id", "conversations", ["cycle_id"])
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("cycle_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    # Seed: perfil inicial de Vadim
    op.execute("""
        INSERT INTO user_profiles (
            id, user_id, nombre, estilos, preferencias_tecnicas,
            prioridades_actuales, decisiones_previas, humor_contexto,
            interrupt_mode, created_at, updated_at
        ) VALUES (
            gen_random_uuid()::text,
            'vadim_vornic',
            'Vadim Vornic',
            '{"comunicacion": "directo, técnico, sin relleno", "trabajo": "iterativo, testing constante", "toma_decisiones": "data-driven pero pragmático", "humor": "aprecia ironía ligera y referencias frikis"}',  # noqa: E501
            '{"lenguajes_preferidos": ["Python", "TypeScript"], "local_first": true, "modelos_libres": true}',
            '["Construir Origin multiagente funcional", "Maximizar uso de modelos gratuitos"]',
            '["Python como núcleo", "TypeScript/React para UI", "PostgreSQL como DB principal"]',
            'enfocado/trabajo',
            'focus',
            NOW(),
            NOW()
        )
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("conversations")
    op.drop_table("memories")
    op.drop_table("user_profiles")
    op.execute("DROP TYPE IF EXISTS memorytype")
