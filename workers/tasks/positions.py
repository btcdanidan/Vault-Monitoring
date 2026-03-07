"""Position snapshot and sync tasks (§10, §12).

``sync_new_events`` performs incremental on-chain event scanning every 15 min.
``snapshot_positions`` and ``refresh_pendle_positions`` remain placeholders.
"""

from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import NamedTuple

import redis
import structlog
from sqlalchemy import text

from workers.celery_app import app
from workers.database import get_sync_session
from workers.services.event_scanner import EVM_CHAINS, scan_events
from workers.services.lot_builder import create_lots
from workers.services.position_computer import compute_positions
from workers.services.price_backfill import backfill_prices

logger = structlog.get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SYNC_LOCK_KEY = "lock:sync_new_events"
SYNC_LOCK_TTL = 840  # 14 minutes (< 15 min beat interval)

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


class WalletRow(NamedTuple):
    id: str
    user_id: str
    address: str
    chain: str
    last_synced_block: int


def _load_synced_wallets() -> list[WalletRow]:
    """Load all active, fully-synced wallets for incremental scanning."""
    with get_sync_session() as session:
        rows = session.execute(
            text("""
                SELECT id, user_id, address, chain,
                       COALESCE(last_synced_block, 0)
                FROM wallets
                WHERE is_active = true
                  AND sync_status = 'synced'
                ORDER BY chain, address
            """),
        ).fetchall()

    return [
        WalletRow(
            id=str(r[0]),
            user_id=str(r[1]),
            address=str(r[2]),
            chain=str(r[3]),
            last_synced_block=int(r[4]),
        )
        for r in rows
    ]


def _update_wallet_after_sync(
    wallet_id: str,
    highest_block: int | None,
) -> None:
    """Stamp ``last_synced_block`` and ``last_synced_at`` after a successful sync."""
    now = datetime.now(UTC)
    params: dict = {"wallet_id": wallet_id, "now": now}

    if highest_block is not None:
        params["block"] = highest_block
        sql = """
            UPDATE wallets
            SET last_synced_block = :block,
                last_synced_at = :now,
                updated_at = :now
            WHERE id = :wallet_id
        """
    else:
        sql = """
            UPDATE wallets
            SET last_synced_at = :now,
                updated_at = :now
            WHERE id = :wallet_id
        """

    with get_sync_session() as session:
        session.execute(text(sql), params)


def _sync_wallet(wallet: WalletRow) -> dict:
    """Run the full incremental pipeline for a single wallet.

    Returns a summary dict for structured logging.
    """
    from_block = wallet.last_synced_block + 1 if wallet.chain in EVM_CHAINS else 0

    raw_events, highest_block = scan_events(
        wallet.address, wallet.chain, from_block,
    )

    if not raw_events:
        _update_wallet_after_sync(wallet.id, highest_block)
        return {
            "wallet_id": wallet.id,
            "chain": wallet.chain,
            "events": 0,
            "lots": 0,
            "positions": 0,
        }

    enriched_events = backfill_prices(raw_events)
    lots_created = create_lots(enriched_events, wallet.id, wallet.user_id)

    positions_updated = 0
    if lots_created > 0:
        positions_updated = compute_positions(wallet.id, wallet.user_id)

    _update_wallet_after_sync(wallet.id, highest_block)

    return {
        "wallet_id": wallet.id,
        "chain": wallet.chain,
        "events": len(raw_events),
        "lots": lots_created,
        "positions": positions_updated,
    }


def _sync_chain_wallets(chain: str, wallets: list[WalletRow]) -> list[dict]:
    """Process all wallets on a single chain sequentially.

    Per-wallet errors are caught and logged so one failure does not block
    the remaining wallets on the same chain.
    """
    results: list[dict] = []
    for wallet in wallets:
        try:
            summary = _sync_wallet(wallet)
            results.append(summary)
            if summary["events"] > 0:
                logger.info(
                    "sync_wallet_complete",
                    wallet_id=wallet.id,
                    chain=chain,
                    events=summary["events"],
                    lots=summary["lots"],
                    positions=summary["positions"],
                )
        except Exception:
            logger.error(
                "sync_wallet_failed",
                wallet_id=wallet.id,
                chain=chain,
                exc_info=True,
            )
            results.append({
                "wallet_id": wallet.id,
                "chain": chain,
                "error": True,
            })
    return results


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@app.task(name="workers.tasks.positions.snapshot_positions")
def snapshot_positions() -> None:
    """Placeholder: snapshot current positions for historical tracking."""
    pass


@app.task(name="workers.tasks.positions.refresh_pendle_positions")
def refresh_pendle_positions() -> None:
    """Placeholder: refresh Pendle PT/YT/LP positions."""
    pass


@app.task(name="workers.tasks.positions.sync_new_events")
def sync_new_events() -> None:
    """Incremental sync of on-chain events since last checkpoint per wallet.

    Runs every 15 min via Celery Beat.  Groups wallets by chain and processes
    each chain in a separate thread for parallelism (§10: "1 per chain").
    """
    r = _get_redis()
    if not r.set(SYNC_LOCK_KEY, "1", nx=True, ex=SYNC_LOCK_TTL):
        logger.info("sync_new_events_skipped_locked")
        return

    try:
        wallets = _load_synced_wallets()
        if not wallets:
            logger.info("sync_new_events_no_wallets")
            return

        wallets_by_chain: dict[str, list[WalletRow]] = defaultdict(list)
        for w in wallets:
            wallets_by_chain[w.chain].append(w)

        logger.info(
            "sync_new_events_start",
            chains=list(wallets_by_chain.keys()),
            wallet_count=len(wallets),
        )

        all_results: list[dict] = []
        max_workers = min(len(wallets_by_chain), 4)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_sync_chain_wallets, chain, chain_wallets): chain
                for chain, chain_wallets in wallets_by_chain.items()
            }
            for future in as_completed(futures):
                chain = futures[future]
                try:
                    chain_results = future.result()
                    all_results.extend(chain_results)
                except Exception:
                    logger.error(
                        "sync_chain_failed",
                        chain=chain,
                        exc_info=True,
                    )

        total_events = sum(r.get("events", 0) for r in all_results)
        total_lots = sum(r.get("lots", 0) for r in all_results)
        errors = sum(1 for r in all_results if r.get("error"))

        logger.info(
            "sync_new_events_complete",
            wallets_processed=len(all_results),
            total_events=total_events,
            total_lots=total_lots,
            errors=errors,
        )

    finally:
        r.delete(SYNC_LOCK_KEY)
