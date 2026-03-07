"""Tests for the wallet history reconstruction pipeline (§12).

Covers:
- Schema validation (RawEvent, EnrichedEvent)
- Progress tracker (Redis-backed)
- Event scanner helpers (address padding, topic parsing)
- Lot builder (deposit/withdrawal lot creation)
- Position computer (FIFO + WAC cost basis with CLAUDE.md hand-calc example)
- Task orchestrator (mocked end-to-end)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from workers.services.schemas import (
    EnrichedEvent,
    ProgressStatus,
    RawEvent,
    ReconstructionPhase,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestRawEvent:
    def test_valid_actions(self) -> None:
        for action in ("deposit", "withdraw", "borrow", "repay", "claim",
                        "transfer_in", "transfer_out", "swap"):
            ev = RawEvent(
                tx_hash="0xabc",
                block_number=100,
                timestamp=datetime.now(UTC),
                chain="ethereum",
                protocol="morpho",
                vault_or_market_id="0x123",
                action=action,
                wallet_address="0xWALLET",
                asset_address="0xASSET",
                asset_symbol="ETH",
                amount=Decimal("1.5"),
            )
            assert ev.action == action

    def test_invalid_action_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid action"):
            RawEvent(
                tx_hash="0xabc",
                block_number=100,
                timestamp=datetime.now(UTC),
                chain="ethereum",
                protocol="morpho",
                vault_or_market_id="0x123",
                action="invalid_action",
                wallet_address="0xWALLET",
                asset_address="0xASSET",
                asset_symbol=None,
                amount=Decimal("1"),
            )


class TestEnrichedEvent:
    def test_from_raw_with_price(self) -> None:
        raw = RawEvent(
            tx_hash="0xabc",
            block_number=100,
            timestamp=datetime.now(UTC),
            chain="ethereum",
            protocol="aave_v3",
            vault_or_market_id="0x123",
            action="deposit",
            wallet_address="0xWALLET",
            asset_address="0xASSET",
            asset_symbol="WETH",
            amount=Decimal("10"),
        )
        enriched = EnrichedEvent.from_raw(raw, Decimal("2000"))
        assert enriched.price_per_unit_usd == Decimal("2000")
        assert enriched.amount_usd == Decimal("20000")

    def test_from_raw_without_price(self) -> None:
        raw = RawEvent(
            tx_hash="0xabc",
            block_number=100,
            timestamp=datetime.now(UTC),
            chain="ethereum",
            protocol="morpho",
            vault_or_market_id="0x123",
            action="deposit",
            wallet_address="0xWALLET",
            asset_address="0xASSET",
            asset_symbol=None,
            amount=Decimal("5"),
        )
        enriched = EnrichedEvent.from_raw(raw, None)
        assert enriched.price_per_unit_usd is None
        assert enriched.amount_usd is None


# ---------------------------------------------------------------------------
# Progress tracker tests
# ---------------------------------------------------------------------------


class TestProgressTracker:
    """Test ProgressTracker using a mock Redis."""

    def _make_tracker(self) -> tuple:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        with patch("workers.services.progress_tracker._get_redis", return_value=mock_redis):
            from workers.services.progress_tracker import ProgressTracker
            tracker = ProgressTracker("test-wallet-id")
            tracker._redis = mock_redis
        return tracker, mock_redis

    def test_update_sets_redis_key(self) -> None:
        tracker, mock_redis = self._make_tracker()
        tracker.update(ReconstructionPhase.SCANNING, 50, events_found=42)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        key = call_args[0][0]
        value = json.loads(call_args[0][1])

        assert key == "reconstruction:test-wallet-id"
        assert value["phase"] == "scanning"
        assert value["progress_pct"] == 50
        assert value["events_found"] == 42
        assert value["error_message"] is None

    def test_set_error(self) -> None:
        tracker, mock_redis = self._make_tracker()
        tracker.set_error("Something failed")

        call_args = mock_redis.set.call_args
        value = json.loads(call_args[0][1])

        assert value["phase"] == "error"
        assert value["error_message"] == "Something failed"

    def test_get_status_returns_none_when_missing(self) -> None:
        tracker, mock_redis = self._make_tracker()
        mock_redis.get.return_value = None

        status = tracker.get_status()
        assert status is None

    def test_get_status_parses_json(self) -> None:
        tracker, mock_redis = self._make_tracker()
        mock_redis.get.return_value = json.dumps({
            "phase": "computing_lots",
            "progress_pct": 75,
            "events_found": 100,
            "transactions_found": 50,
            "error_message": None,
            "last_updated": "2026-03-07T10:00:00+00:00",
        })

        status = tracker.get_status()
        assert isinstance(status, ProgressStatus)
        assert status.phase == ReconstructionPhase.COMPUTING_LOTS
        assert status.progress_pct == 75
        assert status.events_found == 100

    def test_clear_deletes_key(self) -> None:
        tracker, mock_redis = self._make_tracker()
        tracker.clear()
        mock_redis.delete.assert_called_once_with("reconstruction:test-wallet-id")

    def test_progress_clamps_to_0_100(self) -> None:
        tracker, mock_redis = self._make_tracker()
        tracker.update(ReconstructionPhase.SCANNING, -10)
        value = json.loads(mock_redis.set.call_args[0][1])
        assert value["progress_pct"] == 0

        tracker.update(ReconstructionPhase.SCANNING, 150)
        value = json.loads(mock_redis.set.call_args[0][1])
        assert value["progress_pct"] == 100


# ---------------------------------------------------------------------------
# Event scanner helper tests
# ---------------------------------------------------------------------------


class TestEventScannerHelpers:
    def test_pad_address(self) -> None:
        from workers.services.event_scanner import _pad_address
        result = _pad_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
        assert result == "0x000000000000000000000000d8da6bf26964af9d7eed9e03e53415d37aa96045"
        assert len(result) == 66

    def test_unpad_address(self) -> None:
        from workers.services.event_scanner import _unpad_address
        result = _unpad_address("0x000000000000000000000000d8da6bf26964af9d7eed9e03e53415d37aa96045")
        assert result == "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"

    def test_parse_topics_list(self) -> None:
        from workers.services.event_scanner import _parse_topics
        topics = ["0xabc", "0xdef"]
        assert _parse_topics(topics) == ["0xabc", "0xdef"]

    def test_parse_topics_json_string(self) -> None:
        from workers.services.event_scanner import _parse_topics
        result = _parse_topics('["0xaaa", "0xbbb"]')
        assert result == ["0xaaa", "0xbbb"]

    def test_parse_topics_none(self) -> None:
        from workers.services.event_scanner import _parse_topics
        assert _parse_topics(None) == []

    def test_decode_uint256(self) -> None:
        from workers.services.event_scanner import _decode_uint256
        data = "0x" + "0" * 56 + "0000000a"
        assert _decode_uint256(data, 0) == 10

    def test_amount_to_decimal(self) -> None:
        from workers.services.event_scanner import _amount_to_decimal
        result = _amount_to_decimal(1_000_000_000_000_000_000, 18)
        assert result == Decimal("1")


# ---------------------------------------------------------------------------
# Position computer: FIFO + WAC with hand-calculated example from CLAUDE.md
# ---------------------------------------------------------------------------


class TestPositionAccumulator:
    """Test FIFO + WAC cost basis using the canonical example:

    - Deposit 10 ETH @ $2000 -> cost basis = $20,000
    - Deposit 5 ETH @ $2500  -> FIFO basis = $32,500, WAC = $2,166.67/ETH
    - Withdraw 8 ETH @ $3000 -> FIFO realised = $8,000
      Remaining FIFO basis = 2*$2000 + 5*$2500 = $16,500
    """

    def _make_lot(
        self, action: str, amount: str, price: str, ts_offset: int = 0,
    ) -> "LotRecord":
        from workers.services.position_computer import LotRecord
        return LotRecord(
            lot_id=str(uuid.uuid4()),
            action=action,
            amount=Decimal(amount),
            remaining_amount=Decimal(amount),
            price_usd=Decimal(price),
            amount_usd=Decimal(amount) * Decimal(price),
            timestamp=datetime(2026, 1, 1, 0, 0, ts_offset, tzinfo=UTC),
            source="auto",
        )

    def test_fifo_cost_basis_after_two_deposits(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        lot2 = self._make_lot("deposit", "5", "2500", ts_offset=1)
        acc.lots = [lot1, lot2]

        assert acc.current_amount() == Decimal("15")
        assert acc.cost_basis_fifo() == Decimal("32500.00")

    def test_wac_cost_basis_after_two_deposits(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        lot2 = self._make_lot("deposit", "5", "2500", ts_offset=1)
        acc.lots = [lot1, lot2]

        wac_price = (Decimal("10") * Decimal("2000") + Decimal("5") * Decimal("2500")) / Decimal("15")
        expected_wac_basis = Decimal("15") * wac_price
        assert acc.cost_basis_wac() == expected_wac_basis.quantize(Decimal("0.01"))

    def test_fifo_withdrawal_consumes_oldest_lots_first(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        lot2 = self._make_lot("deposit", "5", "2500", ts_offset=1)
        acc.lots = [lot1, lot2]

        acc.process_withdrawal_fifo(Decimal("8"), Decimal("3000"))

        assert lot1.remaining_amount == Decimal("2")
        assert lot2.remaining_amount == Decimal("5")

        expected_gain = (Decimal("3000") - Decimal("2000")) * Decimal("8")
        assert acc.realised_pnl_fifo == expected_gain
        assert expected_gain == Decimal("8000")

    def test_fifo_remaining_basis_after_withdrawal(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        lot2 = self._make_lot("deposit", "5", "2500", ts_offset=1)
        acc.lots = [lot1, lot2]

        acc.process_withdrawal_fifo(Decimal("8"), Decimal("3000"))

        remaining_fifo = acc.cost_basis_fifo()
        expected = Decimal("2") * Decimal("2000") + Decimal("5") * Decimal("2500")
        assert remaining_fifo == expected.quantize(Decimal("0.01"))
        assert remaining_fifo == Decimal("16500.00")

    def test_wac_realised_gain(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        lot2 = self._make_lot("deposit", "5", "2500", ts_offset=1)
        acc.lots = [lot1, lot2]

        wac_price = (Decimal("10") * Decimal("2000") + Decimal("5") * Decimal("2500")) / Decimal("15")

        acc.process_withdrawal_wac(Decimal("8"), Decimal("3000"))

        expected_gain = (Decimal("3000") - wac_price) * Decimal("8")
        assert acc.realised_pnl_wac == expected_gain

    def test_full_withdrawal_closes_position(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        acc.lots = [lot1]

        acc.process_withdrawal_fifo(Decimal("10"), Decimal("2500"))

        assert acc.current_amount() == Decimal("0")
        assert acc.is_closed() is True

    def test_partial_withdrawal_keeps_position_open(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000", ts_offset=0)
        acc.lots = [lot1]

        acc.process_withdrawal_fifo(Decimal("5"), Decimal("2500"))

        assert acc.current_amount() == Decimal("5")
        assert acc.is_closed() is False

    def test_reconstruction_status_all_auto(self) -> None:
        from workers.services.position_computer import PositionAccumulator

        acc = PositionAccumulator()
        acc.lots = [self._make_lot("deposit", "10", "2000")]
        assert acc.reconstruction_status() == "complete"

    def test_reconstruction_status_mixed(self) -> None:
        from workers.services.position_computer import LotRecord, PositionAccumulator

        acc = PositionAccumulator()
        lot1 = self._make_lot("deposit", "10", "2000")
        lot2 = LotRecord(
            lot_id=str(uuid.uuid4()),
            action="deposit",
            amount=Decimal("5"),
            remaining_amount=Decimal("5"),
            price_usd=Decimal("2500"),
            amount_usd=Decimal("12500"),
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            source="manual",
        )
        acc.lots = [lot1, lot2]
        assert acc.reconstruction_status() == "partial"


# ---------------------------------------------------------------------------
# Task orchestrator tests (mocked dependencies)
# ---------------------------------------------------------------------------


class TestReconstructWalletHistoryTask:
    """Task orchestrator tests.

    These tests require the Celery app and workers.database which depend
    on psycopg2.  They are skipped when psycopg2 is not installed
    (e.g. in CI without a running PostgreSQL).
    """

    @pytest.fixture(autouse=True)
    def _skip_without_psycopg2(self) -> None:
        pytest.importorskip("psycopg2", reason="psycopg2 required for task tests")

    @patch("workers.tasks.reconstruction._get_redis")
    @patch("workers.tasks.reconstruction._load_wallet")
    @patch("workers.tasks.reconstruction._set_wallet_syncing")
    @patch("workers.tasks.reconstruction._finalise_wallet")
    @patch("workers.tasks.reconstruction.scan_events")
    @patch("workers.tasks.reconstruction.backfill_prices")
    @patch("workers.tasks.reconstruction.create_lots")
    @patch("workers.tasks.reconstruction.compute_positions")
    @patch("workers.tasks.reconstruction.ProgressTracker")
    def test_full_pipeline_no_events(
        self,
        mock_tracker_cls,
        mock_compute,
        mock_create_lots,
        mock_backfill,
        mock_scan,
        mock_finalise,
        mock_syncing,
        mock_load_wallet,
        mock_get_redis,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        wallet_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_load_wallet.return_value = ("0xABC", "ethereum", 0)
        mock_scan.return_value = ([], 1000)

        from workers.tasks.reconstruction import reconstruct_wallet_history
        reconstruct_wallet_history(wallet_id, user_id)

        mock_syncing.assert_called_once()
        mock_scan.assert_called_once_with("0xABC", "ethereum", 0)
        mock_backfill.assert_not_called()
        mock_create_lots.assert_not_called()
        mock_compute.assert_not_called()
        mock_finalise.assert_called_once_with(
            uuid.UUID(wallet_id), "synced", 1000,
        )

    @patch("workers.tasks.reconstruction._get_redis")
    @patch("workers.tasks.reconstruction._load_wallet")
    @patch("workers.tasks.reconstruction._set_wallet_syncing")
    @patch("workers.tasks.reconstruction._finalise_wallet")
    @patch("workers.tasks.reconstruction.scan_events")
    @patch("workers.tasks.reconstruction.backfill_prices")
    @patch("workers.tasks.reconstruction.create_lots")
    @patch("workers.tasks.reconstruction.compute_positions")
    @patch("workers.tasks.reconstruction.ProgressTracker")
    def test_full_pipeline_with_events(
        self,
        mock_tracker_cls,
        mock_compute,
        mock_create_lots,
        mock_backfill,
        mock_scan,
        mock_finalise,
        mock_syncing,
        mock_load_wallet,
        mock_get_redis,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        wallet_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        fake_events = [
            RawEvent(
                tx_hash="0x1", block_number=100,
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                chain="ethereum", protocol="morpho",
                vault_or_market_id="0xVAULT", action="deposit",
                wallet_address="0xABC", asset_address="0xTOKEN",
                asset_symbol="WETH", amount=Decimal("10"),
            )
        ]
        fake_enriched = [
            EnrichedEvent.from_raw(fake_events[0], Decimal("2000"))
        ]

        mock_load_wallet.return_value = ("0xABC", "ethereum", 0)
        mock_scan.return_value = (fake_events, 500)
        mock_backfill.return_value = fake_enriched
        mock_create_lots.return_value = 1
        mock_compute.return_value = 1

        from workers.tasks.reconstruction import reconstruct_wallet_history
        reconstruct_wallet_history(wallet_id, user_id)

        mock_scan.assert_called_once()
        mock_backfill.assert_called_once_with(fake_events)
        mock_create_lots.assert_called_once_with(fake_enriched, wallet_id, user_id)
        mock_compute.assert_called_once_with(wallet_id, user_id)
        mock_finalise.assert_called_once_with(
            uuid.UUID(wallet_id), "synced", 500,
        )

    @patch("workers.tasks.reconstruction._get_redis")
    @patch("workers.tasks.reconstruction._load_wallet")
    @patch("workers.tasks.reconstruction.ProgressTracker")
    def test_wallet_not_found(
        self,
        mock_tracker_cls,
        mock_load_wallet,
        mock_get_redis,
    ) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        mock_load_wallet.return_value = None

        from workers.tasks.reconstruction import reconstruct_wallet_history

        wallet_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        reconstruct_wallet_history(wallet_id, user_id)

        mock_tracker.set_error.assert_called_once_with("Wallet not found")

    @patch("workers.tasks.reconstruction._get_redis")
    def test_concurrent_reconstruction_skipped(self, mock_get_redis) -> None:
        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # Lock already held
        mock_get_redis.return_value = mock_redis

        from workers.tasks.reconstruction import reconstruct_wallet_history

        wallet_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        reconstruct_wallet_history(wallet_id, user_id)

        # Should return early without calling anything else


# ---------------------------------------------------------------------------
# Lot builder tests
# ---------------------------------------------------------------------------


class TestLotBuilder:
    def test_create_lots_empty_events(self) -> None:
        from workers.services.lot_builder import create_lots
        result = create_lots([], "wallet-id", "user-id")
        assert result == 0

    def test_build_lot_row_deposit(self) -> None:
        from workers.services.lot_builder import _build_lot_row

        ev = EnrichedEvent(
            tx_hash="0x1",
            block_number=100,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            chain="ethereum",
            protocol="morpho",
            vault_or_market_id="0xVAULT",
            action="deposit",
            wallet_address="0xWALLET",
            asset_address="0xTOKEN",
            asset_symbol="WETH",
            amount=Decimal("10"),
            log_index=0,
            price_per_unit_usd=Decimal("2000"),
            amount_usd=Decimal("20000"),
        )

        row = _build_lot_row(ev, "wallet-id", "user-id")

        assert row["action"] == "deposit"
        assert row["amount"] == "10"
        assert row["price_per_unit_usd"] == "2000"
        assert row["amount_usd"] == "20000"
        assert row["lot_status"] == "open"
        assert row["remaining_amount"] == "10"
        assert row["source"] == "auto"
        assert row["position_id"] is None

    def test_build_lot_row_swap_has_no_position(self) -> None:
        from workers.services.lot_builder import _build_lot_row

        ev = EnrichedEvent(
            tx_hash="0x1",
            block_number=100,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            chain="ethereum",
            protocol="uniswap_v3",
            vault_or_market_id="0xPOOL",
            action="swap",
            wallet_address="0xWALLET",
            asset_address="0xTOKEN",
            asset_symbol="WETH",
            amount=Decimal("5"),
            log_index=0,
            price_per_unit_usd=Decimal("2000"),
            amount_usd=Decimal("10000"),
        )

        row = _build_lot_row(ev, "wallet-id", "user-id", position_id=None)
        assert row["position_id"] is None
