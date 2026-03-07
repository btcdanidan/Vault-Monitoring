"""Create transaction_lot records from enriched events (§5, §12).

Inserts immutable TransactionLot rows in chronological order.  Detects
swap-then-deposit patterns within the same tx_hash per §5 matching algorithm.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

import structlog
from sqlalchemy import text

from workers.services.schemas import EnrichedEvent

logger = structlog.get_logger(__name__)

ACTION_TO_POSITION_TYPE: dict[str, str] = {
    "deposit": "supply",
    "withdraw": "supply",
    "borrow": "borrow",
    "repay": "borrow",
    "claim": "supply",
    "transfer_in": "supply",
    "transfer_out": "supply",
}

DEPOSIT_LIKE = frozenset({"deposit", "transfer_in", "claim"})
WITHDRAWAL_LIKE = frozenset({"withdraw", "transfer_out", "repay"})


def create_lots(
    events: list[EnrichedEvent],
    wallet_id: str,
    user_id: str,
) -> int:
    """Insert transaction_lot rows from enriched events.

    - Events are inserted in chronological order.
    - Deposit-like actions get ``lot_status='open'`` and ``remaining_amount=amount``.
    - Withdrawal-like actions get ``lot_status='open'`` and ``remaining_amount=amount``
      (FIFO consumption happens in the position computer phase).
    - Swap events get ``position_id=NULL`` per §5.
    - Swap → deposit matching: if a swap and deposit share a ``tx_hash``, the
      deposit's cost basis inherits the swap output value.

    Returns the number of lots created.
    """
    if not events:
        return 0

    sorted_events = sorted(events, key=lambda e: (e.timestamp, e.log_index or 0))

    events_by_tx: dict[str, list[EnrichedEvent]] = defaultdict(list)
    for ev in sorted_events:
        events_by_tx[ev.tx_hash].append(ev)

    swap_outputs: dict[str, EnrichedEvent] = {}
    for tx_hash, tx_events in events_by_tx.items():
        swaps = [e for e in tx_events if e.action == "swap"]
        if swaps:
            swap_outputs[tx_hash] = swaps[-1]

    rows: list[dict] = []
    for ev in sorted_events:
        if ev.action == "swap":
            row = _build_lot_row(ev, wallet_id, user_id, position_id=None)
            rows.append(row)
            continue

        price = ev.price_per_unit_usd
        amount_usd = ev.amount_usd

        if ev.action in DEPOSIT_LIKE and ev.tx_hash in swap_outputs:
            swap = swap_outputs[ev.tx_hash]
            if swap.price_per_unit_usd is not None:
                price = swap.price_per_unit_usd
                amount_usd = ev.amount * swap.price_per_unit_usd

        row = _build_lot_row(
            ev,
            wallet_id,
            user_id,
            price_override=price,
            amount_usd_override=amount_usd,
        )
        rows.append(row)

    if not rows:
        return 0

    from workers.database import get_sync_session

    with get_sync_session() as session:
        stmt = text("""
            INSERT INTO transaction_lots (
                id, user_id, position_id, wallet_address, chain, protocol,
                vault_or_market_id, action, asset_symbol, asset_address,
                amount, amount_usd, price_per_unit_usd, timestamp,
                tx_hash, block_number, lot_status, remaining_amount, source
            ) VALUES (
                :id, :user_id, :position_id, :wallet_address, :chain, :protocol,
                :vault_or_market_id, :action, :asset_symbol, :asset_address,
                :amount, :amount_usd, :price_per_unit_usd, :timestamp,
                :tx_hash, :block_number, :lot_status, :remaining_amount, :source
            )
            ON CONFLICT (id) DO NOTHING
        """)
        session.execute(stmt, rows)

    logger.info(
        "lots_created",
        wallet_id=wallet_id,
        lots=len(rows),
        deposits=sum(1 for r in rows if r["action"] in DEPOSIT_LIKE),
        withdrawals=sum(1 for r in rows if r["action"] in WITHDRAWAL_LIKE),
        swaps=sum(1 for r in rows if r["action"] == "swap"),
    )
    return len(rows)


def _build_lot_row(
    ev: EnrichedEvent,
    wallet_id: str,
    user_id: str,
    *,
    position_id: str | None = "deferred",
    price_override: object | None = None,
    amount_usd_override: object | None = None,
) -> dict:
    """Build a parameter dict for a single transaction_lot INSERT."""
    price = price_override if price_override is not None else ev.price_per_unit_usd
    amount_usd = amount_usd_override if amount_usd_override is not None else ev.amount_usd

    lot_id = str(uuid.uuid4())

    return {
        "id": lot_id,
        "user_id": user_id,
        "position_id": None,
        "wallet_address": ev.wallet_address,
        "chain": ev.chain,
        "protocol": ev.protocol,
        "vault_or_market_id": ev.vault_or_market_id,
        "action": ev.action,
        "asset_symbol": ev.asset_symbol,
        "asset_address": ev.asset_address,
        "amount": str(ev.amount),
        "amount_usd": str(amount_usd) if amount_usd is not None else None,
        "price_per_unit_usd": str(price) if price is not None else None,
        "timestamp": ev.timestamp,
        "tx_hash": ev.tx_hash,
        "block_number": ev.block_number,
        "lot_status": "open",
        "remaining_amount": str(ev.amount),
        "source": "auto",
    }
