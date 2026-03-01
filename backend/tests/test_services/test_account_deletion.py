"""Tests for account deletion service (§19.9)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.profile import Profile
from app.services import account_deletion as account_deletion_module

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def db_engine():
    """Async engine with Profile table."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Profile.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncSession:
    """Session for service tests."""
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


@pytest.mark.asyncio
async def test_delete_user_account_removes_profile(
    db_session: AsyncSession,
) -> None:
    """delete_user_account deletes the profile from DB."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email="user@example.com",
        approved=True,
        rejected=False,
        is_admin=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(profile)
    await db_session.flush()

    with (
        patch.object(
            account_deletion_module,
            "_delete_supabase_auth_user",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            account_deletion_module,
            "queue_failed_supabase_deletion",
            new_callable=AsyncMock,
        ),
    ):
        await account_deletion_module.delete_user_account(db_session, user_id)
    await db_session.commit()

    result = await db_session.execute(select(Profile).where(Profile.id == user_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_user_account_raises_for_nonexistent(
    db_session: AsyncSession,
) -> None:
    """delete_user_account raises ValueError for non-existent user."""
    fake_id = uuid.uuid4()

    with pytest.raises(ValueError, match="not found"):
        await account_deletion_module.delete_user_account(db_session, fake_id)


@pytest.mark.asyncio
async def test_delete_user_account_raises_for_admin(
    db_session: AsyncSession,
) -> None:
    """delete_user_account raises ValueError for admin user."""
    admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=admin_id,
        email="admin@example.com",
        approved=True,
        rejected=False,
        is_admin=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(profile)
    await db_session.flush()

    with pytest.raises(ValueError, match="admin"):
        await account_deletion_module.delete_user_account(db_session, admin_id)


@pytest.mark.asyncio
async def test_delete_user_account_queues_on_supabase_failure(
    db_session: AsyncSession,
) -> None:
    """When Supabase delete fails, queue_failed_supabase_deletion is called."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email="user@example.com",
        approved=True,
        rejected=False,
        is_admin=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(profile)
    await db_session.flush()

    settings_with_supabase = type("Settings", (), {"supabase_url": "https://test.supabase.co", "supabase_service_role_key": "key"})()
    queue_mock = AsyncMock()
    with (
        patch.object(account_deletion_module, "get_settings", return_value=settings_with_supabase),
        patch.object(
            account_deletion_module,
            "_delete_supabase_auth_user",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch.object(
            account_deletion_module,
            "queue_failed_supabase_deletion",
            queue_mock,
        ),
    ):
        deleted_ok, auth_pending = await account_deletion_module.delete_user_account(db_session, user_id)

    assert deleted_ok is True
    assert auth_pending is True
    queue_mock.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_delete_user_account_returns_auth_cleanup_false_on_supabase_success(
    db_session: AsyncSession,
) -> None:
    """When Supabase delete succeeds, auth_cleanup_pending is False."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email="user@example.com",
        approved=True,
        rejected=False,
        is_admin=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(profile)
    await db_session.flush()

    settings_with_supabase = type("Settings", (), {"supabase_url": "https://test.supabase.co", "supabase_service_role_key": "key"})()
    with (
        patch.object(account_deletion_module, "get_settings", return_value=settings_with_supabase),
        patch.object(
            account_deletion_module,
            "_delete_supabase_auth_user",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        deleted_ok, auth_pending = await account_deletion_module.delete_user_account(db_session, user_id)

    assert deleted_ok is True
    assert auth_pending is False
