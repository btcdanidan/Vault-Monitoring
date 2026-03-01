"""Tests for JWT validation and /api/auth/me."""

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

# In-memory SQLite with only Profile table (avoids JSONB/Postgres-only types in other models)
AUTH_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def auth_db_engine():
    """Engine that only creates the profiles table for auth tests."""
    engine = create_async_engine(AUTH_TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Profile.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture
async def auth_db_session(
    auth_db_engine,
) -> AsyncGenerator[AsyncSession, None]:
    """Session for auth tests; uses auth_db_engine with Profile only."""
    async_session_maker = async_sessionmaker(
        auth_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(auth_db_session: AsyncSession):
    """Async client with get_db overridden to use auth test session."""
    async def override_get_db():
        yield auth_db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_missing_authorization_returns_401(client: AsyncClient) -> None:
    """Missing Authorization header returns 401."""
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_me_invalid_bearer_returns_401(client: AsyncClient) -> None:
    """Invalid or non-Bearer Authorization returns 401."""
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Basic foo"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_expired_token_returns_401(client: AsyncClient) -> None:
    """Expired JWT returns 401."""
    user_id = uuid.uuid4()
    token = make_token(user_id, expired=True)
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_invalid_token_returns_401(client: AsyncClient) -> None:
    """Token signed with wrong secret returns 401."""
    user_id = uuid.uuid4()
    token = make_token(user_id, secret="wrong-secret")
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_valid_token_unapproved_profile_returns_403(
    client: AsyncClient,
    auth_db_session: AsyncSession,
) -> None:
    """Valid JWT but unapproved profile returns 403 with pending_approval."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email="u@example.com",
        approved=False,
        rejected=False,
        created_at=now,
        updated_at=now,
    )
    auth_db_session.add(profile)
    await auth_db_session.flush()

    token = make_token(user_id, email="u@example.com")
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data.get("reason") == "pending_approval"


@pytest.mark.asyncio
async def test_me_valid_token_rejected_profile_returns_403(
    client: AsyncClient,
    auth_db_session: AsyncSession,
) -> None:
    """Valid JWT but rejected profile returns 403 with reason rejected."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email="r@example.com",
        approved=False,
        rejected=True,
        created_at=now,
        updated_at=now,
    )
    auth_db_session.add(profile)
    await auth_db_session.flush()

    token = make_token(user_id, email="r@example.com")
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data.get("reason") == "rejected"


@pytest.mark.asyncio
async def test_me_valid_token_approved_profile_returns_200(
    client: AsyncClient,
    auth_db_session: AsyncSession,
) -> None:
    """Valid JWT and approved profile returns 200 with user_id."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email="a@example.com",
        approved=True,
        rejected=False,
        created_at=now,
        updated_at=now,
    )
    auth_db_session.add(profile)
    await auth_db_session.flush()

    token = make_token(user_id, email="a@example.com")
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == str(user_id)


@pytest.mark.asyncio
async def test_me_valid_token_no_profile_returns_403_pending_approval(
    client: AsyncClient,
) -> None:
    """Valid JWT with no profile triggers auto-create path and returns 403 pending_approval."""
    user_id = uuid.uuid4()
    token = make_token(user_id, email="new@example.com")

    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data.get("reason") == "pending_approval"
