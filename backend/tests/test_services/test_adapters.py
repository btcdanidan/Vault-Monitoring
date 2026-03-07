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
        fn = AsyncMock(side_effect=[httpx.ConnectError("fail"), httpx.ConnectError("fail"), "ok"])
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

        result = await adapter.fetch_and_store_metrics(mock_db, mock_redis, ["0xabc"], "ethereum")

        assert result == metrics
        adapter.fetch_live_metrics.assert_awaited_once_with(["0xabc"], "ethereum")
        adapter.write_vault_metrics.assert_awaited_once_with(mock_db, metrics)
        adapter.discover_vaults.assert_awaited_once()
        adapter._track_rate_limit.assert_awaited_once_with(mock_redis, "fetch_live_metrics")


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

            async def fetch_positions(self, wallet: str, chain: str) -> list[RawPosition]:
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
    """Verify Morpho adapter properties."""

    def test_protocol_name(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        assert adapter.protocol_name == "morpho"

    def test_supported_chains(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        assert adapter.supported_chains == ["ethereum", "base"]

    def test_chain_id_mapping(self) -> None:
        from app.adapters.morpho import CHAIN_ID_MAP

        assert CHAIN_ID_MAP["ethereum"] == 1
        assert CHAIN_ID_MAP["base"] == 8453


class TestMorphoGraphQLQuery:
    """Test the internal _graphql_query helper."""

    async def test_successful_query(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": {"vaults": {"items": []}},
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_http
        adapter._owns_http = False

        result = await adapter._graphql_query(
            "query { vaults { items { address } } }",
        )
        assert result == {"vaults": {"items": []}}
        mock_http.post.assert_awaited_once()

    async def test_graphql_errors_raise(self) -> None:
        from app.adapters.morpho import MorphoAdapter, MorphoGraphQLError

        adapter = MorphoAdapter()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "errors": [{"message": "Query too complex"}],
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_http
        adapter._owns_http = False

        with pytest.raises(MorphoGraphQLError, match="Query too complex"):
            await adapter._graphql_query("query { bad }")

    async def test_http_error_retries(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        adapter._http_client = mock_http
        adapter._owns_http = False

        with (
            patch(
                "app.adapters.base.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            pytest.raises(httpx.ConnectError),
        ):
            await adapter._graphql_query("query { test }")

        assert mock_http.post.await_count == 4


class TestMorphoFetchLiveMetrics:
    """Test fetch_live_metrics with mocked GraphQL responses."""

    @staticmethod
    def _vault_response() -> dict:
        return {
            "data": {
                "vaults": {
                    "items": [
                        {
                            "address": "0xAbC123",
                            "name": "Steakhouse USDC",
                            "symbol": "steakUSDC",
                            "asset": {
                                "address": "0xa0b869",
                                "symbol": "USDC",
                                "decimals": 6,
                            },
                            "chain": {"id": 1},
                            "state": {
                                "totalAssetsUsd": 50000000,
                                "totalAssets": "50000000000",
                                "fee": 0.1,
                                "apy": 0.085,
                                "netApy": 0.0765,
                                "curator": "0xcurator",
                            },
                        },
                        {
                            "address": "0xDef456",
                            "name": "Gauntlet WETH",
                            "symbol": "gtWETH",
                            "asset": {
                                "address": "0xc02aaa",
                                "symbol": "WETH",
                                "decimals": 18,
                            },
                            "chain": {"id": 1},
                            "state": {
                                "totalAssetsUsd": 30000000,
                                "totalAssets": "8500000000000000000000",
                                "fee": 0.15,
                                "apy": 0.032,
                                "netApy": 0.0272,
                                "curator": None,
                            },
                        },
                    ],
                },
            },
        }

    @staticmethod
    def _market_response() -> dict:
        return {
            "data": {
                "markets": {
                    "items": [
                        {
                            "uniqueKey": "0xmarket1",
                            "loanAsset": {
                                "address": "0xusdc",
                                "symbol": "USDC",
                                "decimals": 6,
                            },
                            "collateralAsset": {
                                "address": "0xweth",
                                "symbol": "WETH",
                            },
                            "state": {
                                "supplyAssetsUsd": 10000000,
                                "borrowAssetsUsd": 7000000,
                                "supplyApy": 0.045,
                                "borrowApy": 0.06,
                                "fee": 0,
                                "utilization": 0.7,
                            },
                        },
                    ],
                },
            },
        }

    async def test_fetches_vaults_and_markets(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        vault_resp = MagicMock(spec=httpx.Response)
        vault_resp.status_code = 200
        vault_resp.raise_for_status = MagicMock()
        vault_resp.json.return_value = self._vault_response()

        market_resp = MagicMock(spec=httpx.Response)
        market_resp.status_code = 200
        market_resp.raise_for_status = MagicMock()
        market_resp.json.return_value = self._market_response()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            side_effect=[vault_resp, market_resp],
        )
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_live_metrics([], "ethereum")
        assert len(results) == 3

        market_results = [r for r in results if r.vault_id == "0xmarket1"]
        assert len(market_results) == 1

        first = next(r for r in results if r.vault_id == "0xabc123")
        assert first.vault_name == "Steakhouse USDC"
        assert first.asset_symbol == "USDC"
        assert first.protocol == "morpho"
        assert first.chain == "ethereum"
        assert first.net_apy == Decimal("0.0765")
        assert first.apy_gross == Decimal("0.085")
        assert first.tvl_usd == Decimal("50000000")
        assert first.performance_fee_pct == Decimal("0.1")
        assert first.redemption_type == "instant"

    async def test_filters_by_vault_addresses(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        vault_resp = MagicMock(spec=httpx.Response)
        vault_resp.status_code = 200
        vault_resp.raise_for_status = MagicMock()
        vault_resp.json.return_value = self._vault_response()

        market_resp = MagicMock(spec=httpx.Response)
        market_resp.status_code = 200
        market_resp.raise_for_status = MagicMock()
        market_resp.json.return_value = self._market_response()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            side_effect=[vault_resp, market_resp],
        )
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_live_metrics(
            ["0xAbC123"],
            "ethereum",
        )
        assert len(results) == 1
        assert results[0].vault_id == "0xabc123"

    async def test_empty_response(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        empty_resp = MagicMock(spec=httpx.Response)
        empty_resp.status_code = 200
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = {
            "data": {"vaults": {"items": []}},
        }

        empty_m = MagicMock(spec=httpx.Response)
        empty_m.status_code = 200
        empty_m.raise_for_status = MagicMock()
        empty_m.json.return_value = {
            "data": {"markets": {"items": []}},
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            side_effect=[empty_resp, empty_m],
        )
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_live_metrics([], "ethereum")
        assert results == []

    async def test_unsupported_chain_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        results = await adapter.fetch_live_metrics([], "solana")
        assert results == []

    async def test_graphql_error_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 200
        error_resp.raise_for_status = MagicMock()
        error_resp.json.return_value = {
            "errors": [{"message": "rate limited"}],
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=error_resp)
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_live_metrics([], "ethereum")
        assert results == []

    async def test_market_metrics_mapping(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        vault_resp = MagicMock(spec=httpx.Response)
        vault_resp.status_code = 200
        vault_resp.raise_for_status = MagicMock()
        vault_resp.json.return_value = {
            "data": {"vaults": {"items": []}},
        }

        market_resp = MagicMock(spec=httpx.Response)
        market_resp.status_code = 200
        market_resp.raise_for_status = MagicMock()
        market_resp.json.return_value = self._market_response()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            side_effect=[vault_resp, market_resp],
        )
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_live_metrics([], "ethereum")
        assert len(results) == 1
        m = results[0]
        assert m.vault_id == "0xmarket1"
        assert m.supply_rate == Decimal("0.045")
        assert m.borrow_rate == Decimal("0.06")
        assert m.utilisation_rate == Decimal("0.7")
        assert m.tvl_usd == Decimal("10000000")
        assert m.vault_name == "Morpho Blue USDC/WETH"


class TestMorphoFetchPositions:
    """Test fetch_positions with mocked GraphQL responses."""

    @staticmethod
    def _positions_response() -> dict:
        return {
            "data": {
                "userByAddress": {
                    "address": "0xuser123",
                    "vaultPositions": [
                        {
                            "vault": {
                                "address": "0xVault1",
                                "name": "Steakhouse USDC",
                            },
                            "assets": "1000000000",
                            "assetsUsd": "1000",
                            "shares": "950000000",
                        },
                    ],
                    "marketPositions": [
                        {
                            "market": {
                                "uniqueKey": "0xMarketKey1",
                                "loanAsset": {
                                    "address": "0xUSDC",
                                    "symbol": "USDC",
                                },
                                "collateralAsset": {
                                    "address": "0xWETH",
                                    "symbol": "WETH",
                                },
                            },
                            "supplyAssets": "5000000",
                            "supplyAssetsUsd": "5000",
                            "borrowAssets": "2000000",
                            "borrowAssetsUsd": "2000",
                        },
                    ],
                },
            },
        }

    async def test_fetches_vault_and_market_positions(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = self._positions_response()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_positions("0xuser123", "ethereum")
        assert len(results) == 3

        vault_pos = results[0]
        assert vault_pos.position_type == "supply"
        assert vault_pos.vault_or_market_id == "0xvault1"
        assert vault_pos.current_shares_or_amount == Decimal("950000000")
        assert vault_pos.protocol == "morpho"
        assert vault_pos.chain == "ethereum"

        supply_pos = results[1]
        assert supply_pos.position_type == "supply"
        assert supply_pos.vault_or_market_id == "0xmarketkey1"
        assert supply_pos.current_shares_or_amount == Decimal("5000000")
        assert supply_pos.asset_symbol == "USDC"

        borrow_pos = results[2]
        assert borrow_pos.position_type == "borrow"
        assert borrow_pos.vault_or_market_id == "0xmarketkey1"
        assert borrow_pos.current_shares_or_amount == Decimal("2000000")

    async def test_no_user_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": {"userByAddress": None},
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_positions("0xnoone", "ethereum")
        assert results == []

    async def test_zero_shares_skipped(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "userByAddress": {
                    "address": "0xuser",
                    "vaultPositions": [
                        {
                            "vault": {"address": "0xV1", "name": "V1"},
                            "assets": "0",
                            "assetsUsd": "0",
                            "shares": "0",
                        },
                    ],
                    "marketPositions": [
                        {
                            "market": {
                                "uniqueKey": "0xM1",
                                "loanAsset": {
                                    "address": "0xU",
                                    "symbol": "USDC",
                                },
                                "collateralAsset": {
                                    "address": "0xW",
                                    "symbol": "WETH",
                                },
                            },
                            "supplyAssets": "0",
                            "supplyAssetsUsd": "0",
                            "borrowAssets": "0",
                            "borrowAssetsUsd": "0",
                        },
                    ],
                },
            },
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_positions("0xuser", "ethereum")
        assert results == []

    async def test_unsupported_chain_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        results = await adapter.fetch_positions("0xwallet", "solana")
        assert results == []

    async def test_graphql_error_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 200
        error_resp.raise_for_status = MagicMock()
        error_resp.json.return_value = {
            "errors": [{"message": "internal error"}],
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=error_resp)
        adapter._http_client = mock_http
        adapter._owns_http = False

        results = await adapter.fetch_positions("0xwallet", "ethereum")
        assert results == []


class TestMorphoFetchHistoricalEvents:
    """Test fetch_historical_events with mocked HyperSync."""

    async def test_unsupported_chain_returns_empty(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()
        result = await adapter.fetch_historical_events(
            "0xwallet",
            "solana",
            0,
            100,
        )
        assert result == []

    async def test_delegates_to_hypersync(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()

        mock_log = MagicMock()
        mock_log.topics = [
            "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d711e0d3a0082022",
            "0x" + "0" * 24 + "abcdef1234567890abcdef1234567890abcdef12",
        ]
        mock_log.address = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
        mock_log.block_number = 20_000_001
        mock_log.transaction_hash = "0xdeadbeef"
        mock_log.data = "0x" + "0" * 49 + "de0b6b3a7640000"
        mock_log.log_index = 0

        mock_block = MagicMock()
        mock_block.number = 20_000_001
        mock_block.timestamp = 1700000000

        mock_data = MagicMock()
        mock_data.logs = [mock_log]
        mock_data.blocks = [mock_block]

        mock_result = MagicMock()
        mock_result.data = mock_data
        mock_result.next_block = 20_000_100
        mock_result.archive_height = 20_000_100

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_result)

        with (
            patch(
                "app.adapters.morpho.get_hypersync_client",
                return_value=mock_client,
            ),
            patch(
                "app.adapters.morpho.get_chain_height",
                new_callable=AsyncMock,
                return_value=20_000_100,
            ),
        ):
            results = await adapter.fetch_historical_events(
                "0xabcdef1234567890abcdef1234567890abcdef12",
                "ethereum",
                20_000_000,
                20_000_100,
            )

        assert len(results) == 1
        event = results[0]
        assert event.protocol == "morpho"
        assert event.action == "deposit"
        assert event.chain == "ethereum"
        assert event.block_number == 20_000_001
        assert event.tx_hash == "0xdeadbeef"

    async def test_auto_resolves_to_block(self) -> None:
        from app.adapters.morpho import MorphoAdapter

        adapter = MorphoAdapter()

        mock_data = MagicMock()
        mock_data.logs = []
        mock_data.blocks = []

        mock_result = MagicMock()
        mock_result.data = mock_data
        mock_result.next_block = 20_000_100
        mock_result.archive_height = 20_000_100

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_result)

        mock_get_height = AsyncMock(return_value=20_000_100)

        with (
            patch(
                "app.adapters.morpho.get_hypersync_client",
                return_value=mock_client,
            ),
            patch(
                "app.adapters.morpho.get_chain_height",
                mock_get_height,
            ),
        ):
            results = await adapter.fetch_historical_events(
                "0xwallet",
                "ethereum",
                20_000_000,
                0,
            )

        mock_get_height.assert_awaited_once_with(mock_client)
        assert results == []


class TestMorphoHelpers:
    """Test Morpho adapter helper functions."""

    def test_safe_decimal_valid(self) -> None:
        from app.adapters.morpho import _safe_decimal

        assert _safe_decimal(0.5) == Decimal("0.5")
        assert _safe_decimal("123.45") == Decimal("123.45")
        assert _safe_decimal(100) == Decimal("100")

    def test_safe_decimal_none(self) -> None:
        from app.adapters.morpho import _safe_decimal

        assert _safe_decimal(None) is None

    def test_safe_decimal_invalid(self) -> None:
        from app.adapters.morpho import _safe_decimal

        assert _safe_decimal("not_a_number") is None

    def test_pad_address(self) -> None:
        from app.adapters.morpho import _pad_address

        result = _pad_address(
            "0xAbCdEf1234567890AbCdEf1234567890AbCdEf12",
        )
        assert len(result) == 66
        assert result.startswith("0x000000000000000000000000abcdef")

    def test_unpad_address(self) -> None:
        from app.adapters.morpho import _unpad_address

        padded = "0x000000000000000000000000abcdef1234567890abcdef1234567890abcdef12"
        result = _unpad_address(padded)
        assert result == "0xabcdef1234567890abcdef1234567890abcdef12"

    def test_decode_uint256(self) -> None:
        from app.adapters.morpho import _decode_uint256

        hex_data = "0x" + "0" * 49 + "de0b6b3a7640000"
        result = _decode_uint256(hex_data, 0)
        assert result == 10**18

    def test_amount_to_decimal(self) -> None:
        from app.adapters.morpho import _amount_to_decimal

        result = _amount_to_decimal(10**18, 18)
        assert result == Decimal("1")

        result = _amount_to_decimal(10**6, 6)
        assert result == Decimal("1")


class TestMorphoGraphQLError:
    """Test MorphoGraphQLError exception."""

    def test_error_message_formatting(self) -> None:
        from app.adapters.morpho import MorphoGraphQLError

        err = MorphoGraphQLError(
            [
                {"message": "first error"},
                {"message": "second error"},
            ]
        )
        assert "first error" in str(err)
        assert "second error" in str(err)
        assert len(err.errors) == 2

    def test_error_without_message_key(self) -> None:
        from app.adapters.morpho import MorphoGraphQLError

        err = MorphoGraphQLError([{"code": "RATE_LIMITED"}])
        assert "RATE_LIMITED" in str(err)


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
            "deposit",
            "withdraw",
            "borrow",
            "repay",
            "claim",
            "transfer_in",
            "transfer_out",
            "swap",
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
