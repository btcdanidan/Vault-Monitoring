"""Tests for the sync_new_events incremental sync task (§12).

Covers:
- No active wallets → early return
- Wallets with no new events → updates last_synced_at only
- Wallets with new events → full pipeline (scan → backfill → lots → positions)
- Per-wallet error isolation
- Redis lock prevents overlapping runs
- Solana wallet handling (no block-based incremental)
- Chain-parallel dispatch
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from workers.services.schemas import EnrichedEvent, RawEvent


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _wallet_row(
    chain: str = "ethereum",
    last_synced_block: int = 1000,
) -> tuple:
    """Build a raw DB row tuple matching the _load_synced_wallets query."""
    return (
        str(uuid.uuid4()),  # id
        str(uuid.uuid4()),  # user_id
        "0xABC123",         # address
        chain,
        last_synced_block,
    )


def _make_raw_event(
    chain: str = "ethereum",
    block: int = 2000,
) -> RawEvent:
    return RawEvent(
        tx_hash="0xfeed",
        block_number=block,
        timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        chain=chain,
        protocol="morpho",
        vault_or_market_id="0xVAULT",
        action="deposit",
        wallet_address="0xABC123",
        asset_address="0xTOKEN",
        asset_symbol="WETH",
        amount=Decimal("5"),
    )


def _make_enriched_event(raw: RawEvent) -> EnrichedEvent:
    return EnrichedEvent.from_raw(raw, Decimal("2000"))


# ---------------------------------------------------------------------------
# sync_new_events task tests
# ---------------------------------------------------------------------------


class TestSyncNewEvents:
    """Mocked end-to-end tests for the sync_new_events Celery task."""

    @pytest.fixture(autouse=True)
    def _skip_without_psycopg2(self) -> None:
        pytest.importorskip("psycopg2", reason="psycopg2 required for task tests")

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    def test_no_wallets_returns_early(
        self,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_load.return_value = []

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_load.assert_called_once()
        mock_redis.delete.assert_called_once_with("lock:sync_new_events")

    @patch("workers.tasks.positions._get_redis")
    def test_lock_prevents_overlapping_runs(
        self,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # lock already held
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_redis.delete.assert_not_called()

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    @patch("workers.tasks.positions.scan_events")
    @patch("workers.tasks.positions._update_wallet_after_sync")
    def test_no_new_events_updates_timestamp(
        self,
        mock_update: MagicMock,
        mock_scan: MagicMock,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import WalletRow
        wallet = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xABC",
            chain="ethereum",
            last_synced_block=5000,
        )
        mock_load.return_value = [wallet]
        mock_scan.return_value = ([], 6000)

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_scan.assert_called_once_with("0xABC", "ethereum", 5001)
        mock_update.assert_called_once_with(wallet.id, 6000)

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    @patch("workers.tasks.positions.scan_events")
    @patch("workers.tasks.positions.backfill_prices")
    @patch("workers.tasks.positions.create_lots")
    @patch("workers.tasks.positions.compute_positions")
    @patch("workers.tasks.positions._update_wallet_after_sync")
    def test_new_events_triggers_full_pipeline(
        self,
        mock_update: MagicMock,
        mock_compute: MagicMock,
        mock_create_lots: MagicMock,
        mock_backfill: MagicMock,
        mock_scan: MagicMock,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import WalletRow
        wallet = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xABC",
            chain="ethereum",
            last_synced_block=1000,
        )
        mock_load.return_value = [wallet]

        raw = _make_raw_event()
        enriched = _make_enriched_event(raw)
        mock_scan.return_value = ([raw], 2000)
        mock_backfill.return_value = [enriched]
        mock_create_lots.return_value = 1
        mock_compute.return_value = 1

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_scan.assert_called_once_with("0xABC", "ethereum", 1001)
        mock_backfill.assert_called_once_with([raw])
        mock_create_lots.assert_called_once_with(
            [enriched], wallet.id, wallet.user_id,
        )
        mock_compute.assert_called_once_with(wallet.id, wallet.user_id)
        mock_update.assert_called_once_with(wallet.id, 2000)

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    @patch("workers.tasks.positions.scan_events")
    @patch("workers.tasks.positions.backfill_prices")
    @patch("workers.tasks.positions.create_lots")
    @patch("workers.tasks.positions.compute_positions")
    @patch("workers.tasks.positions._update_wallet_after_sync")
    def test_skips_compute_when_no_lots_created(
        self,
        mock_update: MagicMock,
        mock_compute: MagicMock,
        mock_create_lots: MagicMock,
        mock_backfill: MagicMock,
        mock_scan: MagicMock,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import WalletRow
        wallet = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xABC",
            chain="ethereum",
            last_synced_block=1000,
        )
        mock_load.return_value = [wallet]

        raw = _make_raw_event()
        enriched = _make_enriched_event(raw)
        mock_scan.return_value = ([raw], 2000)
        mock_backfill.return_value = [enriched]
        mock_create_lots.return_value = 0  # all duplicates

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_compute.assert_not_called()
        mock_update.assert_called_once()

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    @patch("workers.tasks.positions.scan_events")
    @patch("workers.tasks.positions._update_wallet_after_sync")
    def test_per_wallet_error_isolation(
        self,
        mock_update: MagicMock,
        mock_scan: MagicMock,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        """One wallet failing must not prevent the other from syncing."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import WalletRow
        wallet_ok = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xGOOD",
            chain="ethereum",
            last_synced_block=100,
        )
        wallet_bad = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xBAD",
            chain="ethereum",
            last_synced_block=100,
        )
        mock_load.return_value = [wallet_bad, wallet_ok]

        def _scan_side_effect(address: str, chain: str, from_block: int):  # type: ignore[no-untyped-def]
            if address == "0xBAD":
                raise RuntimeError("RPC error")
            return ([], 200)

        mock_scan.side_effect = _scan_side_effect

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_update.assert_called_once_with(wallet_ok.id, 200)

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    @patch("workers.tasks.positions.scan_events")
    @patch("workers.tasks.positions._update_wallet_after_sync")
    def test_solana_wallet_scans_from_zero(
        self,
        mock_update: MagicMock,
        mock_scan: MagicMock,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import WalletRow
        wallet = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="So1anaWa11etAddr",
            chain="solana",
            last_synced_block=0,
        )
        mock_load.return_value = [wallet]
        mock_scan.return_value = ([], None)

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        mock_scan.assert_called_once_with("So1anaWa11etAddr", "solana", 0)
        mock_update.assert_called_once_with(wallet.id, None)

    @patch("workers.tasks.positions._get_redis")
    @patch("workers.tasks.positions._load_synced_wallets")
    @patch("workers.tasks.positions.scan_events")
    @patch("workers.tasks.positions._update_wallet_after_sync")
    def test_multiple_chains_processed(
        self,
        mock_update: MagicMock,
        mock_scan: MagicMock,
        mock_load: MagicMock,
        mock_get_redis: MagicMock,
    ) -> None:
        """Wallets on different chains should all be processed."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        from workers.tasks.positions import WalletRow
        w_eth = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xETH",
            chain="ethereum",
            last_synced_block=500,
        )
        w_base = WalletRow(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            address="0xBASE",
            chain="base",
            last_synced_block=300,
        )
        mock_load.return_value = [w_eth, w_base]
        mock_scan.return_value = ([], 1000)

        from workers.tasks.positions import sync_new_events
        sync_new_events()

        assert mock_scan.call_count == 2
        assert mock_update.call_count == 2


# ---------------------------------------------------------------------------
# _sync_wallet unit tests
# ---------------------------------------------------------------------------


class TestSyncWallet:
    @pytest.fixture(autouse=True)
    def _skip_without_psycopg2(self) -> None:
        pytest.importorskip("psycopg2", reason="psycopg2 required for task tests")

    @patch("workers.tasks.positions._update_wallet_after_sync")
    @patch("workers.tasks.positions.scan_events")
    def test_returns_summary_no_events(
        self,
        mock_scan: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        from workers.tasks.positions import WalletRow, _sync_wallet

        wallet = WalletRow(
            id="w1", user_id="u1", address="0xA",
            chain="ethereum", last_synced_block=100,
        )
        mock_scan.return_value = ([], 200)

        result = _sync_wallet(wallet)

        assert result["events"] == 0
        assert result["lots"] == 0
        assert result["positions"] == 0
        mock_update.assert_called_once_with("w1", 200)

    @patch("workers.tasks.positions._update_wallet_after_sync")
    @patch("workers.tasks.positions.compute_positions")
    @patch("workers.tasks.positions.create_lots")
    @patch("workers.tasks.positions.backfill_prices")
    @patch("workers.tasks.positions.scan_events")
    def test_returns_summary_with_events(
        self,
        mock_scan: MagicMock,
        mock_backfill: MagicMock,
        mock_lots: MagicMock,
        mock_compute: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        from workers.tasks.positions import WalletRow, _sync_wallet

        wallet = WalletRow(
            id="w1", user_id="u1", address="0xA",
            chain="base", last_synced_block=50,
        )
        raw = _make_raw_event(chain="base")
        enriched = _make_enriched_event(raw)

        mock_scan.return_value = ([raw], 100)
        mock_backfill.return_value = [enriched]
        mock_lots.return_value = 1
        mock_compute.return_value = 1

        result = _sync_wallet(wallet)

        assert result["events"] == 1
        assert result["lots"] == 1
        assert result["positions"] == 1
        mock_scan.assert_called_once_with("0xA", "base", 51)


# ---------------------------------------------------------------------------
# _sync_chain_wallets unit tests
# ---------------------------------------------------------------------------


class TestSyncChainWallets:
    @pytest.fixture(autouse=True)
    def _skip_without_psycopg2(self) -> None:
        pytest.importorskip("psycopg2", reason="psycopg2 required for task tests")

    @patch("workers.tasks.positions._sync_wallet")
    def test_collects_results_for_all_wallets(
        self,
        mock_sync: MagicMock,
    ) -> None:
        from workers.tasks.positions import WalletRow, _sync_chain_wallets

        wallets = [
            WalletRow(id="w1", user_id="u1", address="0xA",
                      chain="ethereum", last_synced_block=10),
            WalletRow(id="w2", user_id="u2", address="0xB",
                      chain="ethereum", last_synced_block=20),
        ]
        mock_sync.return_value = {"wallet_id": "x", "chain": "ethereum",
                                  "events": 0, "lots": 0, "positions": 0}

        results = _sync_chain_wallets("ethereum", wallets)

        assert len(results) == 2
        assert mock_sync.call_count == 2

    @patch("workers.tasks.positions._sync_wallet")
    def test_continues_after_wallet_error(
        self,
        mock_sync: MagicMock,
    ) -> None:
        from workers.tasks.positions import WalletRow, _sync_chain_wallets

        wallets = [
            WalletRow(id="w1", user_id="u1", address="0xA",
                      chain="ethereum", last_synced_block=10),
            WalletRow(id="w2", user_id="u2", address="0xB",
                      chain="ethereum", last_synced_block=20),
        ]
        mock_sync.side_effect = [
            RuntimeError("boom"),
            {"wallet_id": "w2", "chain": "ethereum",
             "events": 3, "lots": 3, "positions": 1},
        ]

        results = _sync_chain_wallets("ethereum", wallets)

        assert len(results) == 2
        assert results[0].get("error") is True
        assert results[1]["events"] == 3
