"""Integration tests for profiles first-user admin bootstrap trigger (§19.3).

These tests require PostgreSQL and that migrations have been applied (alembic upgrade head).
Skip when TEST_DATABASE_URL is not set to a postgresql URL.
"""

import os
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.profile import Profile

# Only run when explicitly using Postgres (e.g. CI or local with TEST_DATABASE_URL=postgresql+asyncpg://...)
POSTGRES_TEST_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.mark.skipif(
    "postgresql" not in POSTGRES_TEST_URL,
    reason="Profiles bootstrap trigger test requires TEST_DATABASE_URL=postgresql+asyncpg://...",
)
@pytest.mark.asyncio
async def test_first_profile_gets_approved_and_admin_via_trigger() -> None:
    """First profile inserted into empty profiles table gets approved=true, is_admin=true, approved_at set."""
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with async_session_maker() as session:
        # Ensure profiles is empty (trigger logic depends on COUNT(*) = 0)
        await session.execute(delete(Profile))
        await session.commit()

    async with async_session_maker() as session:
        first_id = uuid.uuid4()
        first = Profile(
            id=first_id,
            email="first@example.com",
            approved=False,
            rejected=False,
            is_admin=False,
        )
        session.add(first)
        await session.commit()
        await session.refresh(first)

        assert first.approved is True
        assert first.is_admin is True
        assert first.approved_at is not None

    async with async_session_maker() as session:
        second_id = uuid.uuid4()
        second = Profile(
            id=second_id,
            email="second@example.com",
            approved=False,
            rejected=False,
            is_admin=False,
        )
        session.add(second)
        await session.commit()
        await session.refresh(second)

        assert second.approved is False
        assert second.is_admin is False
        assert second.approved_at is None

    await engine.dispose()
