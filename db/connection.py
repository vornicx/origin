from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from typing import Generator

from core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,            # seconds to wait for a connection from the pool
    pool_recycle=1800,          # recycle connections after 30 min to avoid stale sockets
    connect_args={
        "connect_timeout": 10,  # seconds before TCP connect is abandoned
        "options": "-c statement_timeout=30000",  # 30s per statement
    },
    echo=settings.debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator:
    """Retorna sesión de BD. Usar como dependency en FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Crea todas las tablas. Solo para desarrollo/testing."""
    Base.metadata.create_all(bind=engine)
