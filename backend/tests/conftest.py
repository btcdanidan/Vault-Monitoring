"""Shared pytest fixtures."""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import jwt
import pytest

# Set before any app import so get_settings() sees test secret
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-for-tests")
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


def make_token(
    user_id: uuid.UUID,
    *,
    secret: str = "test-jwt-secret-for-tests",
    email: str | None = "test@example.com",
    expired: bool = False,
) -> str:
    """Build a JWT with sub=user_id for tests."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "aud": "authenticated",
        "exp": now - timedelta(hours=1) if expired else now + timedelta(hours=1),
        "iat": now,
    }
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, secret, algorithm="HS256")

# Use in-memory SQLite for tests, or override with TEST_DATABASE_URL
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Session-scoped event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_engine():
    """Create async engine for tests."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(
    db_engine,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session; truncate tables after each test."""
    async_session_maker = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with async_session_maker() as session:
        yield session
        await session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()
