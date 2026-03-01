"""Shared FastAPI dependencies."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

__all__ = ["get_db_session"]


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency alias for database session."""
    async for session in get_db():
        yield session
