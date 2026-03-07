"""Compute positions from transaction lots with FIFO + WAC cost basis (§5, §12).

Groups transaction_lots by (chain, protocol, vault_or_market_id) to form
positions.  Processes lots in chronological order: deposits create/grow
positions, withdrawals consume lots via FIFO and update WAC.

FIFO is scoped per-position per §5: lots from other positions are never mixed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)

D_ZERO = Decimal("0")
D_TWO = Decimal("0.01")

DEPOSIT_LIKE = frozenset({"deposit", "transfer_in", "claim"})
WITHDRAWAL_LIKE = frozenset({"withdraw", "transfer_out"})
BORROW_LIKE = frozenset({"borrow"})
REPAY_LIKE = frozenset({"repay"})

ACTION_TO_POSITION_TYPE: dict[str, str] = {
    "deposit": "supply",
    "withdraw": "supply",
    "claim": "supply",
    "transfer_in": "supply",
    "transfer_out": "supply",
    "borrow": "borrow",
    "repay": "borrow",
}


@dataclass(slots=True)
class LotRecord:
    """In-memory representation of a transaction_lot row for FIFO processing."""

    lot_id: str
    action: str
    amount: Decimal
    remaining_amount: Decimal
    price_usd: Decimal | None
    amount_usd: Decimal | None
    timestamp: datetime
    source: str


@dataclass(slots=True)
class PositionAccumulator:
    """Accumulates lot data to compute position-level metrics."""

    position_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    chain: str = ""
    protocol: str = ""
    vault_or_market_id: str = ""
    position_type: str = "supply"
    asset_symbol: str | None = None
    asset_address: str | None = None
    lots: list[LotRecord] = field(default_factory=list)
    realised_pnl_fifo: Decimal = D_ZERO
    realised_pnl_wac: Decimal = D_ZERO
    total_borrow_cost: Decimal = D_ZERO

    def current_amount(self) -> Decimal:
        """Sum of remaining_amount across all open deposit-like lots."""
        return sum(
            (lot.remaining_amount for lot in self.lots if lot.action in DEPOSIT_LIKE and lot.remaining_amount > D_ZERO),
            D_ZERO,
        )

    def current_borrow_amount(self) -> Decimal:
        """Net outstanding borrow (borrows minus repays)."""
        borrowed = sum(
            (lot.amount for lot in self.lots if lot.action in BORROW_LIKE),
            D_ZERO,
        )
        repaid = sum(
            (lot.amount for lot in self.lots if lot.action in REPAY_LIKE),
            D_ZERO,
        )
        return max(borrowed - repaid, D_ZERO)

    def cost_basis_fifo(self) -> Decimal:
        """FIFO cost basis = sum(remaining_amount * lot_price) for open lots."""
        total = D_ZERO
        for lot in self.lots:
            if lot.action in DEPOSIT_LIKE and lot.remaining_amount > D_ZERO and lot.price_usd is not None:
                total += lot.remaining_amount * lot.price_usd
        return total.quantize(D_TWO, rounding=ROUND_HALF_UP)

    def cost_basis_wac(self) -> Decimal:
        """WAC cost basis = current_amount * weighted_average_price.

        WAC price = sum(remaining_amount * lot_price) / sum(remaining_amount)
        across all open deposit-like lots.
        """
        total_cost = D_ZERO
        total_amount = D_ZERO
        for lot in self.lots:
            if lot.action in DEPOSIT_LIKE and lot.remaining_amount > D_ZERO and lot.price_usd is not None:
                total_cost += lot.remaining_amount * lot.price_usd
                total_amount += lot.remaining_amount
        if total_amount == D_ZERO:
            return D_ZERO
        wac_price = total_cost / total_amount
        current = self.current_amount()
        return (current * wac_price).quantize(D_TWO, rounding=ROUND_HALF_UP)

    def process_withdrawal_fifo(self, withdraw_amount: Decimal, withdraw_price: Decimal | None) -> None:
        """Consume lots in chronological order (FIFO) for a withdrawal."""
        remaining = withdraw_amount
        open_lots = sorted(
            [l for l in self.lots if l.action in DEPOSIT_LIKE and l.remaining_amount > D_ZERO],
            key=lambda l: l.timestamp,
        )

        for lot in open_lots:
            if remaining <= D_ZERO:
                break
            consumed = min(lot.remaining_amount, remaining)
            if withdraw_price is not None and lot.price_usd is not None:
                gain = (withdraw_price - lot.price_usd) * consumed
                self.realised_pnl_fifo += gain
            lot.remaining_amount -= consumed
            remaining -= consumed

    def process_withdrawal_wac(self, withdraw_amount: Decimal, withdraw_price: Decimal | None) -> None:
        """Track WAC realised P&L for a withdrawal."""
        total_cost = D_ZERO
        total_amount = D_ZERO
        for lot in self.lots:
            if lot.action in DEPOSIT_LIKE and lot.remaining_amount > D_ZERO and lot.price_usd is not None:
                total_cost += lot.remaining_amount * lot.price_usd
                total_amount += lot.remaining_amount
        if total_amount == D_ZERO or withdraw_price is None:
            return
        wac_price = total_cost / total_amount
        gain = (withdraw_price - wac_price) * withdraw_amount
        self.realised_pnl_wac += gain

    def reconstruction_status(self) -> str:
        """Determine reconstruction quality from lot sources."""
        sources = {lot.source for lot in self.lots}
        if sources == {"auto"}:
            return "complete"
        if sources == {"manual"}:
            return "manual"
        return "partial"

    def is_closed(self) -> bool:
        if self.position_type == "borrow":
            return self.current_borrow_amount() == D_ZERO and any(
                lot.action in BORROW_LIKE for lot in self.lots
            )
        return self.current_amount() == D_ZERO and any(
            lot.action in DEPOSIT_LIKE for lot in self.lots
        )


def compute_positions(wallet_id: str, user_id: str) -> int:
    """Compute positions from all transaction_lots for a wallet.

    1. Queries all lots for the wallet (ordered chronologically).
    2. Groups by (chain, protocol, vault_or_market_id, position_type).
    3. Processes deposits and withdrawals through FIFO/WAC accumulators.
    4. Upserts position records and updates lot → position linkage.

    Returns the number of positions created/updated.
    """
    from workers.database import get_sync_session

    with get_sync_session() as session:
        lot_rows = session.execute(
            text("""
                SELECT id, chain, protocol, vault_or_market_id, action,
                       asset_symbol, asset_address, amount, amount_usd,
                       price_per_unit_usd, timestamp, source
                FROM transaction_lots
                WHERE user_id = :user_id
                  AND wallet_address IN (
                      SELECT address FROM wallets WHERE id = :wallet_id
                  )
                  AND action != 'swap'
                ORDER BY timestamp, COALESCE(block_number, 0)
            """),
            {"user_id": user_id, "wallet_id": wallet_id},
        ).fetchall()

        # Query existing position IDs to reuse on upsert (prevents orphans)
        existing_positions = session.execute(
            text("""
                SELECT id, chain, protocol, vault_or_market_id, position_type
                FROM positions
                WHERE user_id = :user_id AND wallet_id = :wallet_id
            """),
            {"user_id": user_id, "wallet_id": wallet_id},
        ).fetchall()

    existing_pos_map: dict[tuple[str, str, str, str], str] = {
        (row[1], row[2], row[3], row[4]): str(row[0])
        for row in existing_positions
    }

    if not lot_rows:
        logger.info("compute_positions_no_lots", wallet_id=wallet_id)
        return 0

    groups: dict[tuple[str, str, str, str], PositionAccumulator] = {}

    for row in lot_rows:
        lot_id, chain, protocol, vault_or_market_id, action = row[:5]
        asset_symbol, asset_address = row[5], row[6]
        amount = Decimal(str(row[7]))
        amount_usd = Decimal(str(row[8])) if row[8] is not None else None
        price_usd = Decimal(str(row[9])) if row[9] is not None else None
        timestamp = row[10]
        source = row[11]

        position_type = ACTION_TO_POSITION_TYPE.get(action, "supply")
        group_key = (chain, protocol, vault_or_market_id, position_type)

        if group_key not in groups:
            # Reuse existing position ID if available, otherwise generate new
            existing_id = existing_pos_map.get(group_key)
            acc = PositionAccumulator(
                position_id=existing_id or str(uuid.uuid4()),
                chain=chain,
                protocol=protocol,
                vault_or_market_id=vault_or_market_id,
                position_type=position_type,
                asset_symbol=asset_symbol,
                asset_address=asset_address,
            )
            groups[group_key] = acc

        acc = groups[group_key]
        lot = LotRecord(
            lot_id=lot_id,
            action=action,
            amount=amount,
            remaining_amount=amount,
            price_usd=price_usd,
            amount_usd=amount_usd,
            timestamp=timestamp,
            source=source,
        )
        acc.lots.append(lot)

        if action in WITHDRAWAL_LIKE:
            acc.process_withdrawal_wac(amount, price_usd)
            acc.process_withdrawal_fifo(amount, price_usd)

    with get_sync_session() as session:  # noqa: F811
        for acc in groups.values():
            _upsert_position(session, acc, wallet_id, user_id)
            _update_lot_remaining_amounts(session, acc)
            _link_lots_to_position(session, acc)

    logger.info(
        "compute_positions_complete",
        wallet_id=wallet_id,
        positions=len(groups),
    )
    return len(groups)


def _upsert_position(
    session: object,
    acc: PositionAccumulator,
    wallet_id: str,
    user_id: str,
) -> None:
    """Insert or update a position record."""
    now = datetime.now(UTC)
    current_amount = acc.current_amount() if acc.position_type != "borrow" else acc.current_borrow_amount()
    status = "closed" if acc.is_closed() else "active"
    closed_at = now if status == "closed" else None

    fifo_basis = acc.cost_basis_fifo()
    wac_basis = acc.cost_basis_wac()
    recon_status = acc.reconstruction_status()

    session.execute(  # type: ignore[union-attr]
        text("""
            INSERT INTO positions (
                id, user_id, wallet_id, chain, protocol, vault_or_market_id,
                position_type, asset_symbol, asset_address,
                current_shares_or_amount,
                cost_basis_fifo_usd, cost_basis_wac_usd,
                realised_pnl_fifo_usd, realised_pnl_wac_usd,
                total_borrow_cost_usd, status, closed_at,
                reconstruction_status, adapter_type,
                created_at, last_updated
            ) VALUES (
                :id, :user_id, :wallet_id, :chain, :protocol, :vault_or_market_id,
                :position_type, :asset_symbol, :asset_address,
                :current_shares_or_amount,
                :cost_basis_fifo_usd, :cost_basis_wac_usd,
                :realised_pnl_fifo_usd, :realised_pnl_wac_usd,
                :total_borrow_cost_usd, :status, :closed_at,
                :reconstruction_status, :adapter_type,
                :created_at, :last_updated
            )
            ON CONFLICT (user_id, wallet_id, chain, protocol, vault_or_market_id, position_type)
            DO UPDATE SET
                current_shares_or_amount = EXCLUDED.current_shares_or_amount,
                cost_basis_fifo_usd = EXCLUDED.cost_basis_fifo_usd,
                cost_basis_wac_usd = EXCLUDED.cost_basis_wac_usd,
                realised_pnl_fifo_usd = EXCLUDED.realised_pnl_fifo_usd,
                realised_pnl_wac_usd = EXCLUDED.realised_pnl_wac_usd,
                total_borrow_cost_usd = EXCLUDED.total_borrow_cost_usd,
                status = EXCLUDED.status,
                closed_at = EXCLUDED.closed_at,
                reconstruction_status = EXCLUDED.reconstruction_status,
                last_updated = EXCLUDED.last_updated
        """),
        {
            "id": acc.position_id,
            "user_id": user_id,
            "wallet_id": wallet_id,
            "chain": acc.chain,
            "protocol": acc.protocol,
            "vault_or_market_id": acc.vault_or_market_id,
            "position_type": acc.position_type,
            "asset_symbol": acc.asset_symbol,
            "asset_address": acc.asset_address,
            "current_shares_or_amount": str(current_amount),
            "cost_basis_fifo_usd": str(fifo_basis),
            "cost_basis_wac_usd": str(wac_basis),
            "realised_pnl_fifo_usd": str(acc.realised_pnl_fifo.quantize(D_TWO, rounding=ROUND_HALF_UP)),
            "realised_pnl_wac_usd": str(acc.realised_pnl_wac.quantize(D_TWO, rounding=ROUND_HALF_UP)),
            "total_borrow_cost_usd": str(acc.total_borrow_cost.quantize(D_TWO, rounding=ROUND_HALF_UP)),
            "status": status,
            "closed_at": closed_at,
            "reconstruction_status": recon_status,
            "adapter_type": "adapter",
            "created_at": now,
            "last_updated": now,
        },
    )


def _update_lot_remaining_amounts(session: object, acc: PositionAccumulator) -> None:
    """Batch-update remaining_amount and lot_status on consumed lots."""
    updates: list[dict] = []
    for lot in acc.lots:
        if lot.action not in DEPOSIT_LIKE:
            continue
        if lot.remaining_amount == lot.amount:
            continue

        if lot.remaining_amount <= D_ZERO:
            status = "closed"
        elif lot.remaining_amount < lot.amount:
            status = "partially_closed"
        else:
            status = "open"

        updates.append({
            "lot_id": lot.lot_id,
            "remaining_amount": str(lot.remaining_amount),
            "lot_status": status,
        })

    if not updates:
        return

    session.execute(  # type: ignore[union-attr]
        text("""
            UPDATE transaction_lots
            SET remaining_amount = :remaining_amount,
                lot_status = :lot_status
            WHERE id = :lot_id
        """),
        updates,
    )


def _link_lots_to_position(session: object, acc: PositionAccumulator) -> None:
    """Set position_id on all lots belonging to this position.

    Uses batch UPDATE. Allows re-linking on recomputation (no IS NULL guard)
    so that lots are always associated with the correct position.
    """
    lot_ids = [lot.lot_id for lot in acc.lots]
    if not lot_ids:
        return

    # Batch update all lots at once instead of N+1 individual queries
    session.execute(  # type: ignore[union-attr]
        text("""
            UPDATE transaction_lots
            SET position_id = :position_id
            WHERE id = ANY(:lot_ids)
        """),
        {"position_id": acc.position_id, "lot_ids": lot_ids},
    )
