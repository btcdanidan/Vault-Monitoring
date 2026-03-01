"""Tests for account deletion endpoints (§19.9)."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models.profile import Profile
from app.models.wallet import Wallet
from tests.conftest import make_token

ADMIN_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def deletion_db_engine():
    """Engine with profiles and wallets for cascade tests."""
    engine = create_async_engine(ADMIN_TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Profile.__table__.create)
        await conn.run_sync(Wallet.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture
async def deletion_db_session(
    deletion_db_engine,
) -> AsyncGenerator[AsyncSession, None]:
    """Session for account deletion tests."""
    async_session_maker = async_sessionmaker(
        deletion_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(deletion_db_session: AsyncSession):
    """Async client with get_db overridden."""
    async def override_get_db():
        yield deletion_db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_delete_account_success(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """Admin can delete a non-admin user; returns 200 and profile is gone."""
    admin_id = uuid.uuid4()
    target_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=admin_id,
            email="admin@example.com",
            approved=True,
            rejected=False,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
    )
    deletion_db_session.add(
        Profile(
            id=target_id,
            email="target@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.delete(
        f"/api/admin/accounts/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["user_id"] == str(target_id)

    await deletion_db_session.commit()
    result = await deletion_db_session.execute(select(Profile).where(Profile.id == target_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_admin_cannot_delete_self(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """Admin cannot delete their own account -> 403."""
    admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=admin_id,
            email="admin@example.com",
            approved=True,
            rejected=False,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.delete(
        f"/api/admin/accounts/{admin_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "own" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_admin_cannot_delete_other_admin(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """Admin cannot delete another admin -> 403."""
    admin_id = uuid.uuid4()
    other_admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=admin_id,
            email="admin@example.com",
            approved=True,
            rejected=False,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
    )
    deletion_db_session.add(
        Profile(
            id=other_admin_id,
            email="otheradmin@example.com",
            approved=True,
            rejected=False,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.delete(
        f"/api/admin/accounts/{other_admin_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "admin" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_admin_delete_nonexistent_returns_404(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """Deleting non-existent user returns 404."""
    admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=admin_id,
            email="admin@example.com",
            approved=True,
            rejected=False,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    fake_id = uuid.uuid4()
    response = await client.delete(
        f"/api/admin/accounts/{fake_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_use_admin_delete(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """Non-admin gets 403 when calling admin delete endpoint."""
    user_id = uuid.uuid4()
    target_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=user_id,
            email="user@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    deletion_db_session.add(
        Profile(
            id=target_id,
            email="target@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(user_id, email="user@example.com")
    response = await client.delete(
        f"/api/admin/accounts/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json().get("reason") == "not_admin"


@pytest.mark.asyncio
async def test_self_delete_with_correct_email_success(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """User can self-delete with correct email confirmation -> 200."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=user_id,
            email="user@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(user_id, email="user@example.com")
    response = await client.request(
        "DELETE",
        "/api/auth/account",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm_email": "user@example.com"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["user_id"] == str(user_id)


@pytest.mark.asyncio
async def test_self_delete_with_wrong_email_returns_400(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """User self-delete with wrong email -> 400."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=user_id,
            email="user@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(user_id, email="user@example.com")
    response = await client.request(
        "DELETE",
        "/api/auth/account",
        headers={"Authorization": f"Bearer {token}"},
        json={"confirm_email": "wrong@example.com"},
    )
    assert response.status_code == 400
    assert "confirmation" in response.json().get("detail", "").lower() or "match" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_delete_account_cascades_to_wallets(
    client: AsyncClient,
    deletion_db_session: AsyncSession,
) -> None:
    """Deleting profile removes associated wallets (cascade)."""
    admin_id = uuid.uuid4()
    target_id = uuid.uuid4()
    wallet_id = uuid.uuid4()
    now = datetime.now(UTC)
    deletion_db_session.add(
        Profile(
            id=admin_id,
            email="admin@example.com",
            approved=True,
            rejected=False,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
    )
    deletion_db_session.add(
        Profile(
            id=target_id,
            email="target@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    deletion_db_session.add(
        Wallet(
            id=wallet_id,
            user_id=target_id,
            address="0x123",
            chain="ethereum",
            created_at=now,
            updated_at=now,
        )
    )
    await deletion_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.delete(
        f"/api/admin/accounts/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    await deletion_db_session.commit()
    result = await deletion_db_session.execute(select(Profile).where(Profile.id == target_id))
    assert result.scalar_one_or_none() is None
    result_w = await deletion_db_session.execute(select(Wallet).where(Wallet.id == wallet_id))
    assert result_w.scalar_one_or_none() is None
