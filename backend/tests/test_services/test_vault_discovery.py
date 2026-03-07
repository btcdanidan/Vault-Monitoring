"""Tests for vault discovery service and is_tracked lifecycle (§10)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vault_discovery import (
    ZERO_TVL_DETRACK_DAYS,
    VaultRef,
    _extract_vault_refs,
    detrack_stale_vaults,
    discover_vaults_from_events,
    get_tracked_vault_ids,
)

# ---------------------------------------------------------------------------
# _extract_vault_refs — helper tests
# ---------------------------------------------------------------------------


class TestExtractVaultRefs:
    """Test vault reference extraction from heterogeneous event lists."""

    def test_extracts_unique_vaults(self) -> None:
        events = [
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol="USDC",
                asset_address="0xusdc",
            ),
            MagicMock(
                vault_or_market_id="vault-2",
                chain="base",
                protocol="aave_v3",
                asset_symbol="WETH",
                asset_address="0xweth",
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 2
        vault_ids = {r.vault_id for r in refs}
        assert vault_ids == {"vault-1", "vault-2"}

    def test_deduplicates_by_vault_and_chain(self) -> None:
        events = [
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol="USDC",
                asset_address="0xusdc",
            ),
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol="WETH",
                asset_address="0xweth",
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 1
        assert refs[0].vault_id == "vault-1"
        assert refs[0].asset_symbol == "USDC"  # first occurrence wins

    def test_same_vault_different_chains(self) -> None:
        events = [
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol=None,
                asset_address=None,
            ),
            MagicMock(
                vault_or_market_id="vault-1",
                chain="base",
                protocol="morpho",
                asset_symbol=None,
                asset_address=None,
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 2

    def test_skips_empty_vault_id(self) -> None:
        events = [
            MagicMock(
                vault_or_market_id="",
                chain="ethereum",
                protocol="morpho",
                asset_symbol=None,
                asset_address=None,
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 0

    def test_skips_empty_chain(self) -> None:
        events = [
            MagicMock(
                vault_or_market_id="vault-1",
                chain="",
                protocol="morpho",
                asset_symbol=None,
                asset_address=None,
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 0

    def test_empty_events_list(self) -> None:
        assert _extract_vault_refs([]) == []

    def test_works_with_pydantic_raw_events(self) -> None:
        from app.schemas.adapter import RawEvent

        events = [
            RawEvent(
                wallet_address="0xuser",
                chain="ethereum",
                protocol="morpho",
                vault_or_market_id="0xvault123",
                action="deposit",
                amount=Decimal("100"),
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 1
        assert refs[0].vault_id == "0xvault123"
        assert refs[0].protocol == "morpho"

    def test_works_with_dataclass_raw_events(self) -> None:
        from workers.services.schemas import RawEvent

        events = [
            RawEvent(
                tx_hash="0xabc",
                block_number=100,
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                chain="ethereum",
                protocol="morpho",
                vault_or_market_id="0xvault456",
                action="deposit",
                wallet_address="0xuser",
                asset_address="0xtoken",
                asset_symbol="USDC",
                amount=Decimal("50"),
            ),
        ]
        refs = _extract_vault_refs(events)
        assert len(refs) == 1
        assert refs[0].vault_id == "0xvault456"

    def test_vault_ref_is_frozen(self) -> None:
        ref = VaultRef(
            vault_id="v1",
            chain="ethereum",
            protocol="morpho",
        )
        with pytest.raises(AttributeError):
            ref.vault_id = "v2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# discover_vaults_from_events — async service tests
# ---------------------------------------------------------------------------


class TestDiscoverVaultsFromEvents:
    """Test async vault discovery from event data."""

    async def test_empty_events_returns_zero(self) -> None:
        mock_db = AsyncMock()
        result = await discover_vaults_from_events(mock_db, [])
        assert result == 0
        mock_db.execute.assert_not_awaited()

    async def test_upserts_vaults_from_events(self) -> None:
        mock_db = AsyncMock()
        events = [
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol="USDC",
                asset_address="0xusdc",
            ),
            MagicMock(
                vault_or_market_id="vault-2",
                chain="base",
                protocol="aave_v3",
                asset_symbol="WETH",
                asset_address="0xweth",
            ),
        ]
        result = await discover_vaults_from_events(mock_db, events)

        assert result == 2
        mock_db.execute.assert_awaited_once()
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert len(params) == 2
        assert params[0]["vault_id"] == "vault-1"
        assert params[0]["chain"] == "ethereum"
        assert params[1]["vault_id"] == "vault-2"
        assert params[1]["chain"] == "base"

    async def test_deduplicates_before_upsert(self) -> None:
        mock_db = AsyncMock()
        events = [
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol="USDC",
                asset_address="0xusdc",
            ),
            MagicMock(
                vault_or_market_id="vault-1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol="WETH",
                asset_address="0xweth",
            ),
        ]
        result = await discover_vaults_from_events(mock_db, events)

        assert result == 1
        params = mock_db.execute.call_args[0][1]
        assert len(params) == 1

    async def test_upsert_sql_contains_on_conflict(self) -> None:
        mock_db = AsyncMock()
        events = [
            MagicMock(
                vault_or_market_id="v1",
                chain="ethereum",
                protocol="morpho",
                asset_symbol=None,
                asset_address=None,
            ),
        ]
        await discover_vaults_from_events(mock_db, events)

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "ON CONFLICT" in sql_text
        assert "is_tracked" in sql_text


# ---------------------------------------------------------------------------
# get_tracked_vault_ids — async service tests
# ---------------------------------------------------------------------------


class TestGetTrackedVaultIds:
    """Test querying tracked vault IDs with filters."""

    async def test_returns_all_tracked(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("vault-1", "ethereum", "morpho"),
            ("vault-2", "base", "aave_v3"),
        ]
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        result = await get_tracked_vault_ids(mock_db)
        assert len(result) == 2
        assert result[0] == ("vault-1", "ethereum", "morpho")
        assert result[1] == ("vault-2", "base", "aave_v3")

    async def test_filters_by_chain(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("vault-1", "ethereum", "morpho")]
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        result = await get_tracked_vault_ids(mock_db, chain="ethereum")
        assert len(result) == 1

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "chain = :chain" in sql_text
        params = call_args[0][1]
        assert params["chain"] == "ethereum"

    async def test_filters_by_protocol(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        await get_tracked_vault_ids(mock_db, protocol="morpho")

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "protocol = :protocol" in sql_text
        params = call_args[0][1]
        assert params["protocol"] == "morpho"

    async def test_filters_by_both(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        await get_tracked_vault_ids(mock_db, chain="ethereum", protocol="morpho")

        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "chain = :chain" in sql_text
        assert "protocol = :protocol" in sql_text

    async def test_empty_result(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        result = await get_tracked_vault_ids(mock_db)
        assert result == []


# ---------------------------------------------------------------------------
# detrack_stale_vaults — async service tests
# ---------------------------------------------------------------------------


class TestDetrackStaleVaults:
    """Test zero-TVL lifecycle management."""

    async def test_returns_detrack_count(self) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        count = await detrack_stale_vaults(mock_db)
        assert count == 3

    async def test_returns_zero_when_none_detracked(self) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        count = await detrack_stale_vaults(mock_db)
        assert count == 0

    async def test_sql_excludes_active_positions(self) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        await detrack_stale_vaults(mock_db)

        sql_text = str(mock_db.execute.call_args[0][0])
        assert "positions" in sql_text
        assert "active" in sql_text

    async def test_sql_checks_7_day_window(self) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        await detrack_stale_vaults(mock_db)

        sql_text = str(mock_db.execute.call_args[0][0])
        assert str(ZERO_TVL_DETRACK_DAYS) in sql_text
        assert "tvl_usd > 0" in sql_text

    async def test_sql_requires_metrics_history(self) -> None:
        """Only detrack vaults that have been scanned (have metrics rows)."""
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        await detrack_stale_vaults(mock_db)

        sql_text = str(mock_db.execute.call_args[0][0])
        assert "EXISTS" in sql_text


# ---------------------------------------------------------------------------
# Sync wrappers (workers/services/vault_discovery.py)
# ---------------------------------------------------------------------------


class TestSyncVaultDiscovery:
    """Test sync wrappers for Celery worker use."""

    def test_discover_vaults_from_raw_events_empty(self) -> None:
        from workers.services.vault_discovery import discover_vaults_from_raw_events

        result = discover_vaults_from_raw_events([])
        assert result == 0

    def test_discover_vaults_calls_db(self) -> None:
        from workers.services.schemas import RawEvent as WorkerRawEvent
        from workers.services.vault_discovery import discover_vaults_from_raw_events

        events = [
            WorkerRawEvent(
                tx_hash="0xabc",
                block_number=100,
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                chain="ethereum",
                protocol="morpho",
                vault_or_market_id="0xvault",
                action="deposit",
                wallet_address="0xuser",
                asset_address="0xtoken",
                asset_symbol="USDC",
                amount=Decimal("100"),
            ),
        ]

        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_session)
        mock_cm.__exit__ = MagicMock(return_value=False)

        with patch(
            "workers.database.get_sync_session",
            return_value=mock_cm,
        ):
            result = discover_vaults_from_raw_events(events)

        assert result == 1
        mock_session.execute.assert_called_once()
        params = mock_session.execute.call_args[0][1]
        assert len(params) == 1
        assert params[0]["vault_id"] == "0xvault"

    def test_get_tracked_vault_ids_sync(self) -> None:
        from workers.services.vault_discovery import get_tracked_vault_ids_sync

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("v1", "ethereum", "morpho"),
        ]
        mock_session = MagicMock()
        mock_session.execute.return_value = mock_result
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_session)
        mock_cm.__exit__ = MagicMock(return_value=False)

        with patch(
            "workers.database.get_sync_session",
            return_value=mock_cm,
        ):
            result = get_tracked_vault_ids_sync(chain="ethereum")

        assert result == [("v1", "ethereum", "morpho")]

    def test_detrack_stale_vaults_sync(self) -> None:
        from workers.services.vault_discovery import detrack_stale_vaults_sync

        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_session = MagicMock()
        mock_session.execute.return_value = mock_result
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_session)
        mock_cm.__exit__ = MagicMock(return_value=False)

        with patch(
            "workers.database.get_sync_session",
            return_value=mock_cm,
        ):
            count = detrack_stale_vaults_sync()

        assert count == 2


# ---------------------------------------------------------------------------
# Integration: reconstruction pipeline calls vault discovery
# ---------------------------------------------------------------------------


class TestReconstructionIntegration:
    """Verify vault discovery is wired into the reconstruction pipeline."""

    def test_reconstruction_imports_vault_discovery(self) -> None:
        import workers.tasks.reconstruction as recon

        assert hasattr(recon, "discover_vaults_from_raw_events")

    def test_reconstruction_calls_discover_after_scan(self) -> None:
        from workers.services.schemas import RawEvent

        mock_events = [
            RawEvent(
                tx_hash="0x1",
                block_number=1,
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                chain="ethereum",
                protocol="morpho",
                vault_or_market_id="0xvault",
                action="deposit",
                wallet_address="0xuser",
                asset_address="0xtoken",
                asset_symbol="USDC",
                amount=Decimal("100"),
            ),
        ]

        mock_tracker = MagicMock()

        with (
            patch(
                "workers.tasks.reconstruction.scan_events",
                return_value=(mock_events, 100),
            ),
            patch(
                "workers.tasks.reconstruction.discover_vaults_from_raw_events",
                return_value=1,
            ) as mock_discover,
            patch("workers.tasks.reconstruction.backfill_prices", return_value=[]),
            patch("workers.tasks.reconstruction.create_lots", return_value=0),
            patch("workers.tasks.reconstruction.compute_positions", return_value=0),
            patch(
                "workers.tasks.reconstruction._load_wallet",
                return_value=("0xuser", "ethereum", 0),
            ),
            patch("workers.tasks.reconstruction._set_wallet_syncing"),
            patch("workers.tasks.reconstruction._finalise_wallet"),
            patch("workers.tasks.reconstruction.ProgressTracker", return_value=mock_tracker),
            patch("workers.tasks.reconstruction._get_redis") as mock_redis_fn,
        ):
            mock_redis = MagicMock()
            mock_redis.set.return_value = True
            mock_redis_fn.return_value = mock_redis

            from workers.tasks.reconstruction import reconstruct_wallet_history

            reconstruct_wallet_history(
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            )

            mock_discover.assert_called_once_with(mock_events)


# ---------------------------------------------------------------------------
# Celery task wiring
# ---------------------------------------------------------------------------


class TestCeleryTaskWiring:
    """Verify task names and registration."""

    def test_refresh_vault_metrics_task_name(self) -> None:
        from workers.tasks.vault_metrics import refresh_vault_metrics

        assert refresh_vault_metrics.name == "workers.tasks.vault_metrics.refresh_vault_metrics"

    def test_check_vault_lifecycle_task_name(self) -> None:
        from workers.tasks.vault_metrics import check_vault_lifecycle

        assert check_vault_lifecycle.name == "workers.tasks.vault_metrics.check_vault_lifecycle"

    def test_refresh_vault_metrics_enabled_in_beat(self) -> None:
        from workers.celeryconfig import beat_schedule

        assert "refresh-vault-metrics" in beat_schedule
        entry = beat_schedule["refresh-vault-metrics"]
        assert entry["schedule"] == 300.0

    def test_check_vault_lifecycle_in_beat(self) -> None:
        from workers.celeryconfig import beat_schedule

        assert "check-vault-lifecycle" in beat_schedule

    def test_check_vault_lifecycle_calls_detrack(self) -> None:
        with patch(
            "workers.services.vault_discovery.detrack_stale_vaults_sync",
            return_value=0,
        ) as mock_detrack:
            from workers.tasks.vault_metrics import check_vault_lifecycle

            check_vault_lifecycle()
            mock_detrack.assert_called_once()
