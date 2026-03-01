"""Tests for wallet CRUD API endpoints (§19.8, §18.7)."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models.profile import Profile
from app.models.wallet import Wallet
from tests.conftest import make_token

WALLET_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
CHECKSUMMED_ADDR = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
LOWER_ADDR = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
SOLANA_ADDR = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


@pytest.fixture
async def wallet_db_engine():
    """Engine with profiles + wallets tables."""
    engine = create_async_engine(WALLET_TEST_DB_URL, echo=False)

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
async def wallet_db_session(
    wallet_db_engine,
) -> AsyncGenerator[AsyncSession, None]:
    """Session for wallet tests."""
    session_maker = async_sessionmaker(
        wallet_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def approved_user(wallet_db_session: AsyncSession) -> uuid.UUID:
    """Create and return an approved user."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    wallet_db_session.add(
        Profile(
            id=user_id,
            email="wallet-user@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    await wallet_db_session.flush()
    return user_id


@pytest.fixture
async def client(wallet_db_session: AsyncSession):
    """Async HTTP client with DB override and Celery mock."""
    async def override_get_db():
        yield wallet_db_session

    app.dependency_overrides[get_db] = override_get_db
    with patch("app.services.wallet._trigger_reconstruction"):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=True,
        ) as ac:
            yield ac
    app.dependency_overrides.clear()


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = make_token(user_id, email="wallet-user@example.com")
    return {"Authorization": f"Bearer {token}"}


# --- POST /api/wallets ---


@pytest.mark.asyncio
async def test_create_wallet_evm_checksummed(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with valid checksummed EVM address returns 201."""
    response = await client.post(
        "/api/wallets/",
        json={"address": CHECKSUMMED_ADDR, "chain": "ethereum", "label": "Main"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["address"] == CHECKSUMMED_ADDR
    assert data["chain"] == "ethereum"
    assert data["label"] == "Main"
    assert data["is_active"] is True
    assert data["sync_status"] == "pending"


@pytest.mark.asyncio
async def test_create_wallet_evm_lowercase_normalised(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with all-lowercase EVM address normalises to checksum form."""
    response = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 201
    assert response.json()["address"] == CHECKSUMMED_ADDR


@pytest.mark.asyncio
async def test_create_wallet_solana_auto_detect(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with Solana address and no chain auto-detects solana."""
    response = await client.post(
        "/api/wallets/",
        json={"address": SOLANA_ADDR},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["chain"] == "solana"
    assert data["address"] == SOLANA_ADDR


@pytest.mark.asyncio
async def test_create_wallet_evm_auto_detect(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with EVM address and no chain auto-detects ethereum."""
    response = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 201
    assert response.json()["chain"] == "ethereum"


@pytest.mark.asyncio
async def test_create_wallet_invalid_address_returns_400(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with garbage address returns 400."""
    response = await client.post(
        "/api/wallets/",
        json={"address": "not-a-real-address", "chain": "ethereum"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_wallet_invalid_chain_returns_400(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with unsupported chain returns 400."""
    response = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "polygon"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_wallet_duplicate_returns_409(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with same address+chain twice returns 409."""
    body = {"address": LOWER_ADDR, "chain": "ethereum"}
    headers = _auth_headers(approved_user)
    r1 = await client.post("/api/wallets/", json=body, headers=headers)
    assert r1.status_code == 201

    r2 = await client.post("/api/wallets/", json=body, headers=headers)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_create_wallet_max_limit_returns_400(
    client: AsyncClient,
    approved_user: uuid.UUID,
    wallet_db_session: AsyncSession,
) -> None:
    """POST when user already has 20 wallets returns 400."""
    now = datetime.now(UTC)
    for i in range(20):
        hex_part = f"{i:040x}"
        addr = f"0x{hex_part}"
        wallet_db_session.add(
            Wallet(
                id=uuid.uuid4(),
                user_id=approved_user,
                address=addr,
                chain="ethereum",
                is_active=True,
                sync_status="synced",
                created_at=now,
                updated_at=now,
            )
        )
    await wallet_db_session.flush()

    response = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 400
    assert "20" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_wallet_base_chain(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with EVM address on Base chain works correctly."""
    response = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "base"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 201
    assert response.json()["chain"] == "base"


# --- GET /api/wallets ---


@pytest.mark.asyncio
async def test_list_wallets_empty(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """GET with no wallets returns empty list."""
    response = await client.get(
        "/api/wallets/",
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wallets"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_wallets_returns_users_wallets(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """GET returns only the current user's wallets."""
    headers = _auth_headers(approved_user)
    await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum", "label": "ETH Wallet"},
        headers=headers,
    )
    response = await client.get("/api/wallets/", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["wallets"][0]["label"] == "ETH Wallet"


@pytest.mark.asyncio
async def test_list_wallets_excludes_other_users(
    client: AsyncClient,
    approved_user: uuid.UUID,
    wallet_db_session: AsyncSession,
) -> None:
    """GET does not return wallets belonging to another user."""
    other_user_id = uuid.uuid4()
    now = datetime.now(UTC)
    wallet_db_session.add(
        Profile(
            id=other_user_id,
            email="other@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    wallet_db_session.add(
        Wallet(
            id=uuid.uuid4(),
            user_id=other_user_id,
            address="0x" + "ab" * 20,
            chain="ethereum",
            is_active=True,
            sync_status="synced",
            created_at=now,
            updated_at=now,
        )
    )
    await wallet_db_session.flush()

    response = await client.get(
        "/api/wallets/",
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


# --- GET /api/wallets/{id} ---


@pytest.mark.asyncio
async def test_get_wallet_by_id(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """GET /{id} returns the specific wallet."""
    headers = _auth_headers(approved_user)
    create_resp = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum"},
        headers=headers,
    )
    wallet_id = create_resp.json()["id"]

    response = await client.get(f"/api/wallets/{wallet_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == wallet_id


@pytest.mark.asyncio
async def test_get_wallet_not_found(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """GET /{id} with non-existent ID returns 404."""
    fake_id = uuid.uuid4()
    response = await client.get(
        f"/api/wallets/{fake_id}",
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_wallet_other_users_wallet_returns_404(
    client: AsyncClient,
    approved_user: uuid.UUID,
    wallet_db_session: AsyncSession,
) -> None:
    """GET /{id} with another user's wallet ID returns 404."""
    other_user_id = uuid.uuid4()
    wallet_id = uuid.uuid4()
    now = datetime.now(UTC)
    wallet_db_session.add(
        Profile(
            id=other_user_id,
            email="other2@example.com",
            approved=True,
            rejected=False,
            is_admin=False,
            created_at=now,
            updated_at=now,
        )
    )
    wallet_db_session.add(
        Wallet(
            id=wallet_id,
            user_id=other_user_id,
            address="0x" + "cd" * 20,
            chain="ethereum",
            is_active=True,
            sync_status="synced",
            created_at=now,
            updated_at=now,
        )
    )
    await wallet_db_session.flush()

    response = await client.get(
        f"/api/wallets/{wallet_id}",
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 404


# --- PATCH /api/wallets/{id} ---


@pytest.mark.asyncio
async def test_update_wallet_label(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """PATCH /{id} updates the wallet label."""
    headers = _auth_headers(approved_user)
    create_resp = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum", "label": "Old"},
        headers=headers,
    )
    wallet_id = create_resp.json()["id"]

    response = await client.patch(
        f"/api/wallets/{wallet_id}",
        json={"label": "New Label"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["label"] == "New Label"


@pytest.mark.asyncio
async def test_update_wallet_not_found(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """PATCH /{id} on non-existent wallet returns 404."""
    fake_id = uuid.uuid4()
    response = await client.patch(
        f"/api/wallets/{fake_id}",
        json={"label": "Nope"},
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 404


# --- DELETE /api/wallets/{id} ---


@pytest.mark.asyncio
async def test_delete_wallet_soft_deletes(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """DELETE /{id} sets is_active=False."""
    headers = _auth_headers(approved_user)
    create_resp = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum"},
        headers=headers,
    )
    wallet_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/wallets/{wallet_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["is_active"] is False

    list_resp = await client.get("/api/wallets/", headers=headers)
    assert list_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_delete_wallet_not_found(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """DELETE /{id} on non-existent wallet returns 404."""
    fake_id = uuid.uuid4()
    response = await client.delete(
        f"/api/wallets/{fake_id}",
        headers=_auth_headers(approved_user),
    )
    assert response.status_code == 404


# --- Reactivation of soft-deleted wallet ---


@pytest.mark.asyncio
async def test_create_reactivates_soft_deleted_wallet(
    client: AsyncClient, approved_user: uuid.UUID
) -> None:
    """POST with address+chain of a soft-deleted wallet reactivates it."""
    headers = _auth_headers(approved_user)
    create_resp = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum", "label": "V1"},
        headers=headers,
    )
    wallet_id = create_resp.json()["id"]

    await client.delete(f"/api/wallets/{wallet_id}", headers=headers)

    reactivate_resp = await client.post(
        "/api/wallets/",
        json={"address": LOWER_ADDR, "chain": "ethereum", "label": "V2"},
        headers=headers,
    )
    assert reactivate_resp.status_code == 201
    data = reactivate_resp.json()
    assert data["id"] == wallet_id
    assert data["is_active"] is True
    assert data["label"] == "V2"


# --- Auth required ---


@pytest.mark.asyncio
async def test_endpoints_require_auth(client: AsyncClient) -> None:
    """All wallet endpoints return 401 without auth."""
    for method, path in [
        ("GET", "/api/wallets/"),
        ("POST", "/api/wallets/"),
        ("GET", f"/api/wallets/{uuid.uuid4()}"),
        ("PATCH", f"/api/wallets/{uuid.uuid4()}"),
        ("DELETE", f"/api/wallets/{uuid.uuid4()}"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 401, f"{method} {path} should require auth"
