"""Tests for admin account management endpoints."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models.profile import Profile
from tests.conftest import make_token

ADMIN_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def admin_db_engine():
    """Engine with only profiles table for admin tests."""
    engine = create_async_engine(ADMIN_TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Profile.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture
async def admin_db_session(
    admin_db_engine,
) -> AsyncGenerator[AsyncSession, None]:
    """Session for admin tests."""
    async_session_maker = async_sessionmaker(
        admin_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(admin_db_session: AsyncSession):
    """Async client with get_db overridden to use admin test session."""
    async def override_get_db():
        yield admin_db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_accounts_requires_admin(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Non-admin user gets 403 when listing accounts."""
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
    admin_db_session.add(profile)
    await admin_db_session.flush()

    token = make_token(user_id, email="user@example.com")
    response = await client.get(
        "/api/admin/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data.get("reason") == "not_admin"


@pytest.mark.asyncio
async def test_list_accounts_returns_profiles(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Admin sees all accounts."""
    admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_profile = Profile(
        id=admin_id,
        email="admin@example.com",
        approved=True,
        rejected=False,
        is_admin=True,
        created_at=now,
        updated_at=now,
    )
    admin_db_session.add(admin_profile)
    await admin_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.get(
        "/api/admin/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "accounts" in data
    assert data["total"] >= 1
    assert any(a["email"] == "admin@example.com" for a in data["accounts"])


@pytest.mark.asyncio
async def test_list_accounts_filter_pending(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Filter status=pending returns only pending accounts."""
    admin_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_db_session.add(
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
    admin_db_session.add(
        Profile(
            id=pending_id,
            email="pending@example.com",
            approved=False,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await admin_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.get(
        "/api/admin/accounts?status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["accounts"][0]["email"] == "pending@example.com"
    assert data["accounts"][0]["approved"] is False


@pytest.mark.asyncio
async def test_approve_account_success(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Approve sets approved=True, approved_at, approved_by."""
    admin_id = uuid.uuid4()
    target_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_db_session.add(
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
    admin_db_session.add(
        Profile(
            id=target_id,
            email="target@example.com",
            approved=False,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await admin_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.post(
        f"/api/admin/accounts/{target_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "target@example.com"
    assert data["approved"] is True
    assert data["rejected"] is False
    assert data["approved_at"] is not None
    assert data["approved_by"] == str(admin_id)


@pytest.mark.asyncio
async def test_approve_nonexistent_returns_404(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Approving non-existent user returns 404."""
    admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_db_session.add(
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
    await admin_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    fake_id = uuid.uuid4()
    response = await client.post(
        f"/api/admin/accounts/{fake_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reject_account_success(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Reject sets rejected=True, approved=False."""
    admin_id = uuid.uuid4()
    target_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_db_session.add(
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
    admin_db_session.add(
        Profile(
            id=target_id,
            email="target@example.com",
            approved=False,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await admin_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.post(
        f"/api/admin/accounts/{target_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "target@example.com"
    assert data["rejected"] is True
    assert data["approved"] is False


@pytest.mark.asyncio
async def test_reject_admin_forbidden(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Cannot reject another admin."""
    admin_id = uuid.uuid4()
    other_admin_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_db_session.add(
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
    admin_db_session.add(
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
    await admin_db_session.flush()

    token = make_token(admin_id, email="admin@example.com")
    response = await client.post(
        f"/api/admin/accounts/{other_admin_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 403
    assert "admin" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_non_admin_cannot_approve(
    client: AsyncClient,
    admin_db_session: AsyncSession,
) -> None:
    """Non-admin gets 403 when calling approve."""
    user_id = uuid.uuid4()
    target_id = uuid.uuid4()
    now = datetime.now(UTC)
    admin_db_session.add(
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
    admin_db_session.add(
        Profile(
            id=target_id,
            email="target@example.com",
            approved=False,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await admin_db_session.flush()

    token = make_token(user_id, email="user@example.com")
    response = await client.post(
        f"/api/admin/accounts/{target_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 403
