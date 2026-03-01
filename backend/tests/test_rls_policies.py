"""Integration tests for RLS policies on user-owned and shared tables (§19.5).

Require PostgreSQL with migrations applied (alembic upgrade head).
Skip when TEST_DATABASE_URL is not set to a postgresql URL.
"""

import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.profile import Profile
from app.models.vault import Vault
from app.models.wallet import Wallet

POSTGRES_TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
CELERY_DB_PASSWORD = os.environ.get("CELERY_DB_PASSWORD", "celery_worker_password")


def _celery_database_url() -> str:
    """Build celery_worker connection URL from TEST_DATABASE_URL for bypass-RLS inserts."""
    parsed = urlparse(POSTGRES_TEST_URL)
    if parsed.scheme.startswith("postgresql"):
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        netloc = f"celery_worker:{CELERY_DB_PASSWORD}@{host}:{port}"
        new = parsed._replace(netloc=netloc)
        return urlunparse(new)
    return ""


@pytest.mark.skipif(
    "postgresql" not in POSTGRES_TEST_URL,
    reason="RLS tests require TEST_DATABASE_URL=postgresql+asyncpg://...",
)
@pytest.mark.asyncio
async def test_user_sees_only_own_data() -> None:
    """With app.current_user_id set to user A, SELECT from user-owned tables returns only A's rows."""
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_a_id)}
        )
        session.add(
            Profile(
                id=user_a_id,
                email="a@example.com",
                approved=True,
                rejected=False,
                is_admin=False,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_b_id)}
        )
        session.add(
            Profile(
                id=user_b_id,
                email="b@example.com",
                approved=True,
                rejected=False,
                is_admin=False,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_a_id)}
        )
        session.add(
            Wallet(
                user_id=user_a_id,
                address="0xaaa",
                chain="ethereum",
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_b_id)}
        )
        session.add(
            Wallet(
                user_id=user_b_id,
                address="0xbbb",
                chain="ethereum",
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_a_id)}
        )
        result = await session.execute(select(Wallet).where(Wallet.user_id == user_a_id))
        wallets = result.scalars().all()
        assert len(wallets) == 1
        assert wallets[0].address == "0xaaa"

    await engine.dispose()


@pytest.mark.skipif(
    "postgresql" not in POSTGRES_TEST_URL,
    reason="RLS tests require TEST_DATABASE_URL=postgresql+asyncpg://...",
)
@pytest.mark.asyncio
async def test_cross_user_access_blocked() -> None:
    """With app.current_user_id set to user A, SELECT for user B's rows returns empty."""
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()

    async with async_session_maker() as session:
        for uid, email in ((user_a_id, "a2@example.com"), (user_b_id, "b2@example.com")):
            await session.execute(
                text("SET LOCAL app.current_user_id = :uid"), {"uid": str(uid)}
            )
            session.add(
                Profile(
                    id=uid,
                    email=email,
                    approved=True,
                    rejected=False,
                    is_admin=False,
                )
            )
            await session.commit()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_b_id)}
        )
        session.add(
            Wallet(user_id=user_b_id, address="0xbb2", chain="ethereum")
        )
        await session.commit()

    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_a_id)}
        )
        result = await session.execute(select(Wallet).where(Wallet.user_id == user_b_id))
        wallets = result.scalars().all()
        assert len(wallets) == 0

    await engine.dispose()


@pytest.mark.skipif(
    "postgresql" not in POSTGRES_TEST_URL,
    reason="RLS tests require TEST_DATABASE_URL=postgresql+asyncpg://...",
)
@pytest.mark.asyncio
async def test_missing_session_variable_returns_no_rows() -> None:
    """Without setting app.current_user_id, SELECT from user-owned tables returns zero rows."""
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with async_session_maker() as session:
        result = await session.execute(select(Profile))
        profiles = result.scalars().all()
        assert len(profiles) == 0

    async with async_session_maker() as session:
        result = await session.execute(select(Wallet))
        wallets = result.scalars().all()
        assert len(wallets) == 0

    await engine.dispose()


@pytest.mark.skipif(
    "postgresql" not in POSTGRES_TEST_URL,
    reason="RLS tests require TEST_DATABASE_URL=postgresql+asyncpg://...",
)
@pytest.mark.asyncio
async def test_shared_tables_readable_when_authenticated() -> None:
    """With app.current_user_id set, SELECT from shared tables returns rows (e.g. vaults)."""
    celery_url = _celery_database_url()
    if not celery_url:
        pytest.skip("Cannot build celery_worker URL for shared table insert")

    engine_defi = create_async_engine(POSTGRES_TEST_URL, echo=False)
    engine_celery = create_async_engine(celery_url, echo=False)
    async_session_defi = async_sessionmaker(
        engine_defi,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async_session_celery = async_sessionmaker(
        engine_celery,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    try:
        async with async_session_celery() as session:
            session.add(
                Vault(
                    vault_id="test-vault",
                    chain="ethereum",
                    protocol="morpho",
                )
            )
            await session.commit()

        user_id = uuid.uuid4()
        async with async_session_defi() as session:
            await session.execute(
                text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_id)}
            )
            result = await session.execute(select(Vault).where(Vault.vault_id == "test-vault"))
            vaults = result.scalars().all()
            assert len(vaults) == 1
            assert vaults[0].vault_id == "test-vault"

        async with async_session_celery() as session:
            await session.execute(delete(Vault).where(Vault.vault_id == "test-vault"))
            await session.commit()
    finally:
        await engine_defi.dispose()
        await engine_celery.dispose()


@pytest.mark.skipif(
    "postgresql" not in POSTGRES_TEST_URL,
    reason="RLS tests require TEST_DATABASE_URL=postgresql+asyncpg://...",
)
@pytest.mark.asyncio
async def test_shared_tables_empty_without_session_variable() -> None:
    """Without setting app.current_user_id, SELECT from shared tables returns zero rows."""
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with async_session_maker() as session:
        result = await session.execute(select(Vault))
        vaults = result.scalars().all()
        assert len(vaults) == 0

    await engine.dispose()
