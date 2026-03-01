"""Synchronous database engine for Celery workers (BYPASSRLS role, §19.5)."""

import os
from collections.abc import Generator
from contextlib import contextmanager

from celery.signals import worker_process_init
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
_POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
_POSTGRES_DB = os.getenv("POSTGRES_DB", "defi_vault")
_CELERY_DB_PASSWORD = os.getenv("CELERY_DB_PASSWORD", "celery_worker_password")

WORKER_DATABASE_URL = (
    f"postgresql+psycopg2://celery_worker:{_CELERY_DB_PASSWORD}"
    f"@{_POSTGRES_HOST}:{_POSTGRES_PORT}/{_POSTGRES_DB}"
)

engine = create_engine(
    os.getenv("WORKER_DATABASE_URL", WORKER_DATABASE_URL),
    pool_size=3,
    max_overflow=2,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@worker_process_init.connect
def _dispose_engine_on_fork(**kwargs: object) -> None:
    """Dispose the connection pool after Celery prefork to avoid stale connections."""
    engine.dispose()


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """Yield a sync DB session; rolls back on error, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
