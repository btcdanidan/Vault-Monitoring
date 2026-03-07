"""Tests for the refresh_vault_metrics Celery task.

Covers:
- net_apy computation (various fee combinations)
- Tracked-vault loading and grouping
- Full task orchestration (happy path, no vaults, adapter errors)
- Redis lock contention (concurrent invocation skipped)
- Parallel adapter execution
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.adapter import VaultMetricsData


# ---------------------------------------------------------------------------
# Fixtures: sample VaultMetricsData
# ---------------------------------------------------------------------------

def _make_metrics(
    *,
    vault_id: str = "0xVAULT",
    chain: str = "ethereum",
    protocol: str = "morpho",
    apy_gross: Decimal | None = Decimal("5.0000"),
    performance_fee_pct: Decimal | None = Decimal("10.00"),
    mgmt_fee_pct: Decimal | None = Decimal("0.5000"),
    net_apy: Decimal | None = None,
    tvl_usd: Decimal | None = Decimal("1000000.00"),
) -> VaultMetricsData:
    return VaultMetricsData(
        vault_id=vault_id,
        chain=chain,
        protocol=protocol,
        vault_name="Test Vault",
        asset_symbol="USDC",
        asset_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        timestamp=datetime(2026, 3, 7, 12, 0, 0, tzinfo=UTC),
        apy_gross=apy_gross,
        performance_fee_pct=performance_fee_pct,
        mgmt_fee_pct=mgmt_fee_pct,
        net_apy=net_apy,
        tvl_usd=tvl_usd,
    )


# ---------------------------------------------------------------------------
# net_apy computation
# ---------------------------------------------------------------------------


class TestEnsureNetApy:
    """_ensure_net_apy post-processing logic."""

    def test_computes_net_apy_when_missing(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        m = _make_metrics(
            apy_gross=Decimal("5.0000"),
            performance_fee_pct=Decimal("10.00"),
            mgmt_fee_pct=Decimal("0.5000"),
        )
        result = _ensure_net_apy([m])
        # net = 5.0 * (1 - 10/100) - 0.5 = 5.0 * 0.9 - 0.5 = 4.5 - 0.5 = 4.0
        assert result[0].net_apy == Decimal("4.0000")

    def test_preserves_adapter_set_net_apy(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        m = _make_metrics(net_apy=Decimal("3.5000"))
        result = _ensure_net_apy([m])
        assert result[0].net_apy == Decimal("3.5000")

    def test_only_performance_fee(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        m = _make_metrics(
            apy_gross=Decimal("10.0000"),
            performance_fee_pct=Decimal("20.00"),
            mgmt_fee_pct=None,
        )
        result = _ensure_net_apy([m])
        # net = 10.0 * (1 - 20/100) - 0 = 10.0 * 0.8 = 8.0
        assert result[0].net_apy == Decimal("8.0000")

    def test_only_mgmt_fee(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        m = _make_metrics(
            apy_gross=Decimal("6.0000"),
            performance_fee_pct=None,
            mgmt_fee_pct=Decimal("1.0000"),
        )
        result = _ensure_net_apy([m])
        # net = 6.0 * 1.0 - 1.0 = 5.0
        assert result[0].net_apy == Decimal("5.0000")

    def test_no_fees(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        m = _make_metrics(
            apy_gross=Decimal("7.0000"),
            performance_fee_pct=None,
            mgmt_fee_pct=None,
        )
        result = _ensure_net_apy([m])
        assert result[0].net_apy == Decimal("7.0000")

    def test_no_apy_gross_leaves_none(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        m = _make_metrics(apy_gross=None)
        result = _ensure_net_apy([m])
        assert result[0].net_apy is None

    def test_handles_empty_list(self) -> None:
        from workers.tasks.vault_metrics import _ensure_net_apy

        assert _ensure_net_apy([]) == []


# ---------------------------------------------------------------------------
# Tracked-vault loading
# ---------------------------------------------------------------------------


class TestLoadTrackedVaults:
    @patch("workers.tasks.vault_metrics.get_sync_session")
    def test_groups_by_protocol_chain(self, mock_session_ctx: MagicMock) -> None:
        from workers.tasks.vault_metrics import _load_tracked_vaults

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_session.execute.return_value.fetchall.return_value = [
            ("morpho", "ethereum", "0xV1"),
            ("morpho", "ethereum", "0xV2"),
            ("aave_v3", "ethereum", "0xV3"),
            ("morpho", "base", "0xV4"),
        ]

        result = _load_tracked_vaults()

        assert ("morpho", "ethereum") in result
        assert len(result[("morpho", "ethereum")]) == 2
        assert ("aave_v3", "ethereum") in result
        assert ("morpho", "base") in result

    @patch("workers.tasks.vault_metrics.get_sync_session")
    def test_returns_empty_when_no_vaults(self, mock_session_ctx: MagicMock) -> None:
        from workers.tasks.vault_metrics import _load_tracked_vaults

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.fetchall.return_value = []

        result = _load_tracked_vaults()
        assert result == {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistMetrics:
    @patch("workers.tasks.vault_metrics.get_sync_session")
    def test_calls_execute_with_params(self, mock_session_ctx: MagicMock) -> None:
        from workers.tasks.vault_metrics import _persist_metrics

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)

        m = _make_metrics(net_apy=Decimal("4.0000"))
        _persist_metrics([m])

        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert len(params) == 1
        assert params[0]["vault_id"] == "0xVAULT"
        assert params[0]["net_apy"] == "4.0000"

    @patch("workers.tasks.vault_metrics.get_sync_session")
    def test_skips_empty_list(self, mock_session_ctx: MagicMock) -> None:
        from workers.tasks.vault_metrics import _persist_metrics

        _persist_metrics([])
        mock_session_ctx.assert_not_called()


# ---------------------------------------------------------------------------
# Discover vaults
# ---------------------------------------------------------------------------


class TestDiscoverVaults:
    @patch("workers.tasks.vault_metrics.get_sync_session")
    def test_upserts_vaults(self, mock_session_ctx: MagicMock) -> None:
        from workers.tasks.vault_metrics import _discover_vaults

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)

        m = _make_metrics(tvl_usd=Decimal("500000.00"))
        _discover_vaults([m])

        mock_session.execute.assert_called_once()
        params = mock_session.execute.call_args[0][1]
        assert params[0]["vault_id"] == "0xVAULT"
        assert params[0]["is_tracked"] is True

    @patch("workers.tasks.vault_metrics.get_sync_session")
    def test_low_tvl_still_tracked_if_not_in_map(self, mock_session_ctx: MagicMock) -> None:
        from workers.tasks.vault_metrics import _discover_vaults

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)

        m = _make_metrics(tvl_usd=None)
        _discover_vaults([m])

        params = mock_session.execute.call_args[0][1]
        assert params[0]["is_tracked"] is True


# ---------------------------------------------------------------------------
# Task orchestrator
# ---------------------------------------------------------------------------


class TestRefreshVaultMetricsTask:
    """Full task orchestrator tests with mocked dependencies."""

    @pytest.fixture(autouse=True)
    def _skip_without_psycopg2(self) -> None:
        pytest.importorskip("psycopg2", reason="psycopg2 required for task tests")

    @patch("workers.tasks.vault_metrics._get_redis")
    @patch("workers.tasks.vault_metrics._load_tracked_vaults")
    def test_skips_when_no_tracked_vaults(
        self,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis
        mock_load.return_value = {}

        from workers.tasks.vault_metrics import refresh_vault_metrics
        refresh_vault_metrics()

        mock_load.assert_called_once()

    @patch("workers.tasks.vault_metrics._get_redis")
    def test_skips_when_lock_held(self, mock_get_redis: MagicMock) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = False
        mock_get_redis.return_value = mock_redis

        from workers.tasks.vault_metrics import refresh_vault_metrics
        refresh_vault_metrics()

    @patch("workers.tasks.vault_metrics._discover_vaults")
    @patch("workers.tasks.vault_metrics._persist_metrics")
    @patch("workers.tasks.vault_metrics._run_adapter_for_chain")
    @patch("workers.tasks.vault_metrics._load_tracked_vaults")
    @patch("workers.tasks.vault_metrics._get_redis")
    @patch("app.adapters.registry.get_adapter")
    def test_happy_path(
        self,
        mock_get_adapter: MagicMock,
        mock_get_redis: MagicMock,
        mock_load: MagicMock,
        mock_run_adapter: MagicMock,
        mock_persist: MagicMock,
        mock_discover: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_load.return_value = {
            ("morpho", "ethereum"): ["0xV1", "0xV2"],
        }

        metrics = [_make_metrics(vault_id="0xV1"), _make_metrics(vault_id="0xV2")]
        mock_run_adapter.return_value = metrics
        mock_get_adapter.return_value = MagicMock()

        from workers.tasks.vault_metrics import refresh_vault_metrics
        refresh_vault_metrics()

        mock_run_adapter.assert_called_once()
        mock_persist.assert_called_once()
        mock_discover.assert_called_once()
        mock_redis.delete.assert_called_once_with("lock:refresh_vault_metrics")

    @patch("workers.tasks.vault_metrics._discover_vaults")
    @patch("workers.tasks.vault_metrics._persist_metrics")
    @patch("workers.tasks.vault_metrics._run_adapter_for_chain")
    @patch("workers.tasks.vault_metrics._load_tracked_vaults")
    @patch("workers.tasks.vault_metrics._get_redis")
    @patch("app.adapters.registry.get_adapter")
    def test_adapter_failure_partial_results(
        self,
        mock_get_adapter: MagicMock,
        mock_get_redis: MagicMock,
        mock_load: MagicMock,
        mock_run_adapter: MagicMock,
        mock_persist: MagicMock,
        mock_discover: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_load.return_value = {
            ("morpho", "ethereum"): ["0xV1"],
            ("aave_v3", "ethereum"): ["0xV2"],
        }

        good_metrics = [_make_metrics(vault_id="0xV1")]

        def side_effect(adapter: MagicMock, vault_ids: list, chain: str) -> list:
            if vault_ids == ["0xV1"]:
                return good_metrics
            raise ConnectionError("adapter down")

        mock_run_adapter.side_effect = side_effect
        mock_get_adapter.return_value = MagicMock()

        from workers.tasks.vault_metrics import refresh_vault_metrics
        refresh_vault_metrics()

        mock_persist.assert_called_once()
        written_metrics = mock_persist.call_args[0][0]
        assert len(written_metrics) >= 1

    @patch("workers.tasks.vault_metrics._discover_vaults")
    @patch("workers.tasks.vault_metrics._persist_metrics")
    @patch("workers.tasks.vault_metrics._run_adapter_for_chain")
    @patch("workers.tasks.vault_metrics._load_tracked_vaults")
    @patch("workers.tasks.vault_metrics._get_redis")
    @patch("app.adapters.registry.get_adapter")
    def test_multiple_adapters_called(
        self,
        mock_get_adapter: MagicMock,
        mock_get_redis: MagicMock,
        mock_load: MagicMock,
        mock_run_adapter: MagicMock,
        mock_persist: MagicMock,
        mock_discover: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_load.return_value = {
            ("morpho", "ethereum"): ["0xV1"],
            ("aave_v3", "ethereum"): ["0xV2"],
            ("morpho", "base"): ["0xV3"],
        }

        mock_run_adapter.return_value = [_make_metrics()]
        mock_get_adapter.return_value = MagicMock()

        from workers.tasks.vault_metrics import refresh_vault_metrics
        refresh_vault_metrics()

        assert mock_run_adapter.call_count == 3

    @patch("workers.tasks.vault_metrics._load_tracked_vaults")
    @patch("workers.tasks.vault_metrics._get_redis")
    @patch("app.adapters.registry.get_adapter")
    def test_unknown_protocol_skipped(
        self,
        mock_get_adapter: MagicMock,
        mock_get_redis: MagicMock,
        mock_load: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_load.return_value = {
            ("unknown_protocol", "ethereum"): ["0xV1"],
        }

        mock_get_adapter.return_value = None

        from workers.tasks.vault_metrics import refresh_vault_metrics
        refresh_vault_metrics()


# ---------------------------------------------------------------------------
# API usage tracking
# ---------------------------------------------------------------------------


class TestTrackApiUsage:
    def test_increments_counter(self) -> None:
        from workers.tasks.vault_metrics import _track_api_usage

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        _track_api_usage(mock_redis, "morpho", 1)

        mock_pipe.incrby.assert_called_once()
        mock_pipe.expire.assert_called_once()
        mock_pipe.execute.assert_called_once()

    def test_zero_calls_noop(self) -> None:
        from workers.tasks.vault_metrics import _track_api_usage

        mock_redis = MagicMock()
        _track_api_usage(mock_redis, "morpho", 0)
        mock_redis.pipeline.assert_not_called()
