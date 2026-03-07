"""Tests for protocol adapter base class, registry, and concrete stubs (§9, §10)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.base import (
    MIN_TVL_FOR_TRACKING_USD,
    BaseProtocolAdapter,
    ProtocolAdapter,
)
from app.adapters.registry import (
    _REGISTRY,
    get_adapter,
    get_adapters_for_chain,
    get_all_adapters,
    list_registered_protocols,
    register_adapter,
)
from app.schemas.adapter import (
    DiscoveredVault,
    RawEvent,
    RawPosition,
    VaultMetricsData,
)

# ---------------------------------------------------------------------------
# Helpers — concrete test adapter
# ---------------------------------------------------------------------------


class _TestAdapter(BaseProtocolAdapter):
    """Minimal concrete adapter for testing the base class."""

    @property
    def protocol_name(self) -> str:
        return "test_protocol"

    @property
    def supported_chains(self) -> list[str]:
        return ["ethereum", "base"]

    async def fetch_live_metrics(
        self, vault_addresses: list[str], chain: str
    ) -> list[VaultMetricsData]:
        return []

    async def fetch_positions(self, wallet: str, chain: str) -> list[RawPosition]:
        return []

    async def fetch_historical_events(
        self, wallet: str, chain: str, from_block: int, to_block: int
    ) -> list[RawEvent]:
        return []


# ---------------------------------------------------------------------------
# ProtocolAdapter ABC enforcement
# ---------------------------------------------------------------------------


class TestProtocolAdapterABC:
    """Verify that ProtocolAdapter cannot be instantiated without implementations."""

    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            ProtocolAdapter()  # type: ignore[abstract]

    def test_incomplete_subclass_raises(self) -> None:
        class Incomplete(ProtocolAdapter):
            @property
            def protocol_name(self) -> str:
                return "incomplete"

            @property
            def supported_chains(self) -> list[str]:
                return []

        with pytest.raises(TypeError, match="abstract"):
            Incomplete()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# BaseProtocolAdapter — instantiation and properties
# ---------------------------------------------------------------------------


class TestBaseProtocolAdapterInit:
    """Test adapter creation and lifecycle."""

    def test_creates_with_defaults(self) -> None:
        adapter = _TestAdapter()
        assert adapter.protocol_name == "test_protocol"
        assert adapter.supported_chains == ["ethereum", "base"]

    def test_creates_with_custom_http_client(self) -> None:
        client = httpx.AsyncClient()
        adapter = _TestAdapter(http_client=client)
        assert adapter._http_client is client
        assert adapter._owns_http is False

    async def test_close_owned_client(self) -> None:
        adapter = _TestAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        adapter._http_client = mock_client
        adapter._owns_http = True
        await adapter.close()
        mock_client.aclose.assert_awaited_once()
        assert adapter._http_client is None

    async def test_close_unowned_client_noop(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        adapter = _TestAdapter(http_client=mock_client)
        await adapter.close()
        mock_client.aclose.assert_not_awaited()

    async def test_context_manager(self) -> None:
        async with _TestAdapter() as adapter:
            assert adapter.protocol_name == "test_protocol"

    async def test_get_http_creates_client(self) -> None:
        adapter = _TestAdapter()
        client = await adapter._get_http()
        assert isinstance(client, httpx.AsyncClient)
        await adapter.close()


# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    """Test exponential backoff retry logic."""

    async def test_success_first_try(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(return_value="ok")
        result = await adapter._retry_with_backoff(fn)
        assert result == "ok"
        fn.assert_awaited_once()

    async def test_retries_on_http_error(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(
            side_effect=[httpx.ConnectError("fail"), httpx.ConnectError("fail"), "ok"]
        )
        with patch("app.adapters.base.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter._retry_with_backoff(fn, base_delay=0.01)
        assert result == "ok"
        assert fn.await_count == 3

    async def test_retries_on_connection_error(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(side_effect=[ConnectionError("down"), "ok"])
        with patch("app.adapters.base.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter._retry_with_backoff(fn, base_delay=0.01)
        assert result == "ok"
        assert fn.await_count == 2

    async def test_retries_on_timeout_error(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(side_effect=[TimeoutError("slow"), "ok"])
        with patch("app.adapters.base.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter._retry_with_backoff(fn, base_delay=0.01)
        assert result == "ok"

    async def test_raises_after_max_retries(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(side_effect=httpx.ConnectError("persistent failure"))
        with (
            patch("app.adapters.base.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(httpx.ConnectError, match="persistent failure"),
        ):
            await adapter._retry_with_backoff(fn, max_retries=2, base_delay=0.01)
        assert fn.await_count == 3  # initial + 2 retries

    async def test_non_retryable_error_propagates_immediately(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError, match="bad input"):
            await adapter._retry_with_backoff(fn)
        fn.assert_awaited_once()

    async def test_backoff_delays_increase(self) -> None:
        adapter = _TestAdapter()
        fn = AsyncMock(
            side_effect=[
                httpx.ConnectError("1"),
                httpx.ConnectError("2"),
                httpx.ConnectError("3"),
                httpx.ConnectError("4"),
            ]
        )
        sleep_mock = AsyncMock()
        with (
            patch("app.adapters.base.asyncio.sleep", sleep_mock),
            pytest.raises(httpx.ConnectError),
        ):
            await adapter._retry_with_backoff(fn, max_retries=3, base_delay=1.0)

        delays = [call.args[0] for call in sleep_mock.await_args_list]
        assert delays == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# Rate-limit tracking
# ---------------------------------------------------------------------------


class TestTrackRateLimit:
    """Test Redis rate-limit counter integration."""

    async def test_increments_redis_counter(self) -> None:
        adapter = _TestAdapter()
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_redis.pipeline.return_value = mock_pipe

        await adapter._track_rate_limit(mock_redis, "fetch_live_metrics")

        mock_redis.pipeline.assert_called_once()
        assert mock_pipe.incr.call_count == 1
        assert mock_pipe.expire.call_count == 1

        incr_key = mock_pipe.incr.call_args[0][0]
        assert "test_protocol" in incr_key
        assert "fetch_live_metrics" in incr_key

        expire_key = mock_pipe.expire.call_args[0][0]
        expire_ttl = mock_pipe.expire.call_args[0][1]
        assert expire_key == incr_key
        assert expire_ttl == 172_800


# ---------------------------------------------------------------------------
# Write vault metrics
# ---------------------------------------------------------------------------


class TestWriteVaultMetrics:
    """Test bulk upsert into vault_metrics."""

    async def test_empty_list_is_noop(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()
        await adapter.write_vault_metrics(mock_db, [])
        mock_db.execute.assert_not_awaited()

    async def test_executes_insert_statement(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()

        metrics = [
            VaultMetricsData(
                vault_id="vault-1",
                chain="ethereum",
                protocol="test_protocol",
                vault_name="Test Vault",
                asset_symbol="USDC",
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                net_apy=Decimal("5.25"),
                tvl_usd=Decimal("1000000.00"),
            ),
        ]
        await adapter.write_vault_metrics(mock_db, metrics)

        mock_db.execute.assert_awaited_once()
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert len(params) == 1
        assert params[0]["vault_id"] == "vault-1"
        assert params[0]["net_apy"] == "5.25"

    async def test_handles_multiple_metrics(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()

        metrics = [
            VaultMetricsData(
                vault_id=f"vault-{i}",
                chain="ethereum",
                protocol="test_protocol",
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            )
            for i in range(5)
        ]
        await adapter.write_vault_metrics(mock_db, metrics)

        params = mock_db.execute.call_args[0][1]
        assert len(params) == 5


# ---------------------------------------------------------------------------
# Vault discovery
# ---------------------------------------------------------------------------


class TestDiscoverVaults:
    """Test vault upsert logic."""

    async def test_empty_list_is_noop(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()
        await adapter.discover_vaults(mock_db, [])
        mock_db.execute.assert_not_awaited()

    async def test_executes_upsert(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()

        vaults = [
            DiscoveredVault(
                vault_id="vault-1",
                chain="ethereum",
                protocol="test_protocol",
                vault_name="My Vault",
            ),
        ]
        await adapter.discover_vaults(mock_db, vaults)

        mock_db.execute.assert_awaited_once()
        params = mock_db.execute.call_args[0][1]
        assert len(params) == 1
        assert params[0]["vault_id"] == "vault-1"
        assert params[0]["is_tracked"] is True  # default for new vaults

    async def test_tvl_below_threshold_not_tracked(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()

        vaults = [
            DiscoveredVault(
                vault_id="small-vault",
                chain="ethereum",
                protocol="test_protocol",
            ),
        ]
        tvl = {"small-vault": 50_000.0}
        await adapter.discover_vaults(mock_db, vaults, tvl_by_vault=tvl)

        params = mock_db.execute.call_args[0][1]
        assert params[0]["is_tracked"] is False

    async def test_tvl_above_threshold_tracked(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()

        vaults = [
            DiscoveredVault(
                vault_id="big-vault",
                chain="ethereum",
                protocol="test_protocol",
            ),
        ]
        tvl = {"big-vault": float(MIN_TVL_FOR_TRACKING_USD) + 1}
        await adapter.discover_vaults(mock_db, vaults, tvl_by_vault=tvl)

        params = mock_db.execute.call_args[0][1]
        assert params[0]["is_tracked"] is True

    async def test_tvl_at_threshold_tracked(self) -> None:
        adapter = _TestAdapter()
        mock_db = AsyncMock()

        vaults = [
            DiscoveredVault(
                vault_id="edge-vault",
                chain="ethereum",
                protocol="test_protocol",
            ),
        ]
        tvl = {"edge-vault": float(MIN_TVL_FOR_TRACKING_USD)}
        await adapter.discover_vaults(mock_db, vaults, tvl_by_vault=tvl)

        params = mock_db.execute.call_args[0][1]
        assert params[0]["is_tracked"] is True


# ---------------------------------------------------------------------------
# Fetch-and-store orchestration
# ---------------------------------------------------------------------------


class TestFetchAndStoreMetrics:
    """Test the high-level orchestration method."""

    async def test_orchestration_calls_all_steps(self) -> None:
        adapter = _TestAdapter()

        metrics = [
            VaultMetricsData(
                vault_id="vault-1",
                chain="ethereum",
                protocol="test_protocol",
                vault_name="Test",
                asset_symbol="USDC",
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                tvl_usd=Decimal("500000"),
            ),
        ]

        adapter.fetch_live_metrics = AsyncMock(return_value=metrics)  # type: ignore[method-assign]
        adapter.write_vault_metrics = AsyncMock()  # type: ignore[method-assign]
        adapter.discover_vaults = AsyncMock()  # type: ignore[method-assign]
        adapter._track_rate_limit = AsyncMock()  # type: ignore[method-assign]

        mock_db = AsyncMock()
        mock_redis = MagicMock()

        result = await adapter.fetch_and_store_metrics(
            mock_db, mock_redis, ["0xabc"], "ethereum"
        )

        assert result == metrics
        adapter.fetch_live_metrics.assert_awaited_once_with(["0xabc"], "ethereum")
        adapter.write_vault_metrics.assert_awaited_once_with(mock_db, metrics)
        adapter.discover_vaults.assert_awaited_once()
        adapter._track_rate_limit.assert_awaited_once_with(
            mock_redis, "fetch_live_metrics"
        )


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """Test adapter registration and lookup."""

    def test_morpho_registered(self) -> None:
        import app.adapters.morpho  # noqa: F401

        assert "morpho" in _REGISTRY

    def test_aave_registered(self) -> None:
        import app.adapters.aave  # noqa: F401

        assert "aave_v3" in _REGISTRY

    def test_pendle_registered(self) -> None:
        import app.adapters.pendle  # noqa: F401

        assert "pendle" in _REGISTRY

    def test_euler_registered(self) -> None:
        import app.adapters.euler  # noqa: F401

        assert "euler" in _REGISTRY

    def test_get_adapter_known(self) -> None:
        adapter = get_adapter("morpho")
        assert adapter is not None
        assert adapter.protocol_name == "morpho"

    def test_get_adapter_unknown_returns_none(self) -> None:
        assert get_adapter("unknown_protocol") is None

    def test_get_adapters_for_chain_ethereum(self) -> None:
        adapters = get_adapters_for_chain("ethereum")
        names = {a.protocol_name for a in adapters}
        assert "morpho" in names
        assert "aave_v3" in names
        assert "pendle" in names
        assert "euler" in names

    def test_get_adapters_for_chain_base(self) -> None:
        adapters = get_adapters_for_chain("base")
        names = {a.protocol_name for a in adapters}
        assert "morpho" in names
        assert "aave_v3" in names
        assert "pendle" not in names
        assert "euler" not in names

    def test_get_adapters_for_unsupported_chain(self) -> None:
        adapters = get_adapters_for_chain("solana")
        assert adapters == []

    def test_get_all_adapters(self) -> None:
        adapters = get_all_adapters()
        assert len(adapters) >= 4
        names = {a.protocol_name for a in adapters}
        assert names >= {"morpho", "aave_v3", "pendle", "euler"}

    def test_list_registered_protocols(self) -> None:
        protocols = list_registered_protocols()
        assert "morpho" in protocols
        assert "aave_v3" in protocols

    def test_register_adapter_decorator(self) -> None:
        @register_adapter
        class DummyAdapter(BaseProtocolAdapter):
            @property
            def protocol_name(self) -> str:
                return "_test_dummy_"

            @property
            def supported_chains(self) -> list[str]:
                return ["ethereum"]

            async def fetch_live_metrics(
                self, vault_addresses: list[str], chain: str
            ) -> list[VaultMetricsData]:
                return []

            async def fetch_positions(
                self, wallet: str, chain: str
            ) -> list[RawPosition]:
                return []

            async def fetch_historical_events(
                self, wallet: str, chain: str, from_block: int, to_block: int
            ) -> list[RawEvent]:
                return []

        assert "_test_dummy_" in _REGISTRY
        adapter = get_adapter("_test_dummy_")
        assert adapter is not None
        assert adapter.protocol_name == "_test_dummy_"

        # Cleanup
        del _REGISTRY["_test_dummy_"]


# ---------------------------------------------------------------------------
# Concrete adapter stubs — property correctness
# ---------------------------------------------------------------------------


class TestMorphoAdapter:
    """Verify Morpho adapter properties and stub behaviour."""

    def test_protocol_name(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        assert adapter.protocol_name == "morpho"

    def test_supported_chains(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        assert adapter.supported_chains == ["ethereum", "base"]

    async def test_fetch_live_metrics_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        result = await adapter.fetch_live_metrics(["0x1"], "ethereum")
        assert result == []

    async def test_fetch_positions_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        result = await adapter.fetch_positions("0xwallet", "ethereum")
        assert result == []

    async def test_fetch_historical_events_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        result = await adapter.fetch_historical_events("0xwallet", "ethereum", 0, 100)
        assert result == []


class TestAaveAdapter:
    """Verify Aave adapter properties."""

    def test_protocol_name(self) -> None:
        from app.adapters.aave import AaveAdapter

        assert AaveAdapter().protocol_name == "aave_v3"

    def test_supported_chains(self) -> None:
        from app.adapters.aave import AaveAdapter

        assert AaveAdapter().supported_chains == ["ethereum", "base"]


class TestPendleAdapter:
    """Verify Pendle adapter properties."""

    def test_protocol_name(self) -> None:
        from app.adapters.pendle import PendleAdapter

        assert PendleAdapter().protocol_name == "pendle"

    def test_supported_chains(self) -> None:
        from app.adapters.pendle import PendleAdapter

        assert PendleAdapter().supported_chains == ["ethereum"]


class TestEulerAdapter:
    """Verify Euler adapter properties."""

    def test_protocol_name(self) -> None:
        from app.adapters.euler import EulerAdapter

        assert EulerAdapter().protocol_name == "euler"

    def test_supported_chains(self) -> None:
        from app.adapters.euler import EulerAdapter

        assert EulerAdapter().supported_chains == ["ethereum"]


# ---------------------------------------------------------------------------
# Pydantic schema validation
# ---------------------------------------------------------------------------


class TestVaultMetricsDataSchema:
    """Validate VaultMetricsData schema constraints."""

    def test_minimal_construction(self) -> None:
        m = VaultMetricsData(
            vault_id="v1",
            chain="ethereum",
            protocol="morpho",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert m.vault_id == "v1"
        assert m.net_apy is None

    def test_full_construction(self) -> None:
        m = VaultMetricsData(
            vault_id="v1",
            chain="ethereum",
            protocol="morpho",
            vault_name="My Vault",
            asset_symbol="USDC",
            asset_address="0xA0b8",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            apy_gross=Decimal("10.0000"),
            net_apy=Decimal("8.5000"),
            tvl_usd=Decimal("1000000.00"),
        )
        assert m.net_apy == Decimal("8.5000")


class TestRawPositionSchema:
    """Validate RawPosition schema."""

    def test_construction(self) -> None:
        p = RawPosition(
            wallet_address="0xabc",
            chain="ethereum",
            protocol="aave_v3",
            vault_or_market_id="market-1",
            position_type="supply",
            current_shares_or_amount=Decimal("100.5"),
        )
        assert p.position_type == "supply"


class TestRawEventSchema:
    """Validate RawEvent schema."""

    def test_construction(self) -> None:
        e = RawEvent(
            wallet_address="0xabc",
            chain="ethereum",
            protocol="morpho",
            vault_or_market_id="vault-1",
            action="deposit",
            amount=Decimal("10.0"),
            timestamp=datetime(2025, 6, 1, tzinfo=UTC),
            tx_hash="0xdeadbeef",
            block_number=20_000_000,
        )
        assert e.action == "deposit"

    def test_valid_actions(self) -> None:
        for action in (
            "deposit", "withdraw", "borrow", "repay",
            "claim", "transfer_in", "transfer_out", "swap",
        ):
            e = RawEvent(
                wallet_address="0x1",
                chain="ethereum",
                protocol="test",
                vault_or_market_id="v1",
                action=action,
                amount=Decimal("1"),
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            )
            assert e.action == action


class TestDiscoveredVaultSchema:
    """Validate DiscoveredVault schema."""

    def test_minimal(self) -> None:
        v = DiscoveredVault(
            vault_id="v1",
            chain="ethereum",
            protocol="morpho",
        )
        assert v.vault_name is None

    def test_with_vault_type(self) -> None:
        v = DiscoveredVault(
            vault_id="v1",
            chain="ethereum",
            protocol="morpho",
            vault_type="lending",
        )
        assert v.vault_type == "lending"
