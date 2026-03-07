"""Wallet history reconstruction Celery task — full 4-phase pipeline (§12).

Triggered on wallet creation via ``_trigger_reconstruction`` in
``backend/app/services/wallet.py``.  Orchestrates:

1. Event scanning (HyperSync for EVM, Helius for Solana)
2. Price backfill (DeFiLlama historical)
3. Lot creation (transaction_lots records)
4. Position computation (FIFO + WAC cost basis)

Progress is written to a Redis key per wallet so the frontend can poll
during the onboarding screen.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import redis
import structlog
from sqlalchemy import text
from workers.celery_app import app
from workers.database import get_sync_session
from workers.services.event_scanner import scan_events
from workers.services.lot_builder import create_lots
from workers.services.position_computer import compute_positions
from workers.services.price_backfill import backfill_prices
from workers.services.progress_tracker import ProgressTracker
from workers.services.schemas import ReconstructionPhase
from workers.services.vault_discovery import discover_vaults_from_raw_events

logger = structlog.get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RECONSTRUCTION_LOCK_TTL = 3600  # 1 hour

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


@app.task(name="workers.tasks.reconstruction.run_reconstruction")
def run_reconstruction() -> None:
    """Placeholder: run full position reconstruction (Celery Beat)."""


@app.task(
    name="workers.tasks.reconstruction.reconstruct_wallet_history",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def reconstruct_wallet_history(self, wallet_id: str, user_id: str) -> None:  # type: ignore[no-untyped-def]
    """Reconstruct full history for a newly added wallet.

    Triggered by wallet creation.  Updates ``wallets.sync_status`` through
    the pipeline phases and writes progress to Redis for the frontend.
    """
    r = _get_redis()
    lock_key = f"lock:reconstruction:{wallet_id}"

    if not r.set(lock_key, "1", nx=True, ex=RECONSTRUCTION_LOCK_TTL):
        logger.info("reconstruction_skipped_locked", wallet_id=wallet_id)
        return

    tracker = ProgressTracker(wallet_id)

    try:
        wallet_uuid = uuid.UUID(wallet_id)
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        logger.error("reconstruction_invalid_ids", wallet_id=wallet_id, user_id=user_id)
        tracker.set_error("Invalid wallet or user ID")
        r.delete(lock_key)
        return

    try:
        wallet_info = _load_wallet(wallet_uuid)
        if wallet_info is None:
            logger.warning("reconstruction_wallet_not_found", wallet_id=wallet_id)
            tracker.set_error("Wallet not found")
            return

        address, chain, from_block = wallet_info
        _set_wallet_syncing(wallet_uuid)

        # Phase 1: Event Scanning
        tracker.update(ReconstructionPhase.SCANNING, 0)
        logger.info("reconstruction_phase1_start", wallet_id=wallet_id, chain=chain)

        raw_events, highest_block = scan_events(address, chain, from_block)
        tracker.update(
            ReconstructionPhase.SCANNING, 100,
            events_found=len(raw_events),
        )
        logger.info(
            "reconstruction_phase1_complete",
            wallet_id=wallet_id,
            events=len(raw_events),
            highest_block=highest_block,
        )

        # Vault discovery: register any vaults referenced in the events
        if raw_events:
            vaults_registered = discover_vaults_from_raw_events(raw_events)
            logger.info(
                "reconstruction_vaults_discovered",
                wallet_id=wallet_id,
                vaults=vaults_registered,
            )

        if not raw_events:
            _finalise_wallet(wallet_uuid, "synced", highest_block)
            tracker.update(ReconstructionPhase.COMPLETE, 100)
            logger.info("reconstruction_complete_no_events", wallet_id=wallet_id)
            return

        # Phase 2: Price Backfill
        tracker.update(ReconstructionPhase.BACKFILLING_PRICES, 0)
        logger.info("reconstruction_phase2_start", wallet_id=wallet_id, events=len(raw_events))

        enriched_events = backfill_prices(raw_events)
        tracker.update(ReconstructionPhase.BACKFILLING_PRICES, 100)
        logger.info("reconstruction_phase2_complete", wallet_id=wallet_id)

        # Phase 3: Lot Creation
        tracker.update(ReconstructionPhase.COMPUTING_LOTS, 0)
        logger.info("reconstruction_phase3_start", wallet_id=wallet_id)

        lots_created = create_lots(enriched_events, wallet_id, user_id)
        tracker.update(
            ReconstructionPhase.COMPUTING_LOTS, 100,
            transactions_found=lots_created,
        )
        logger.info("reconstruction_phase3_complete", wallet_id=wallet_id, lots=lots_created)

        # Phase 4: Position Computation
        tracker.update(ReconstructionPhase.COMPUTING_POSITIONS, 0)
        logger.info("reconstruction_phase4_start", wallet_id=wallet_id)

        positions_computed = compute_positions(wallet_id, user_id)
        tracker.update(ReconstructionPhase.COMPUTING_POSITIONS, 100)
        logger.info(
            "reconstruction_phase4_complete",
            wallet_id=wallet_id,
            positions=positions_computed,
        )

        # Finalise
        _finalise_wallet(wallet_uuid, "synced", highest_block)
        tracker.update(ReconstructionPhase.COMPLETE, 100)

        logger.info(
            "reconstruction_complete",
            wallet_id=wallet_id,
            events=len(raw_events),
            lots=lots_created,
            positions=positions_computed,
        )

    except Exception as exc:
        logger.error("reconstruction_failed", wallet_id=wallet_id, exc_info=True)
        tracker.set_error(str(exc)[:500])
        _finalise_wallet(wallet_uuid, "error", None)

        raise self.retry(exc=exc) from exc

    finally:
        r.delete(lock_key)


# ---------------------------------------------------------------------------
# Database helpers (sync, using celery_worker BYPASSRLS role)
# ---------------------------------------------------------------------------


def _load_wallet(wallet_uuid: uuid.UUID) -> tuple[str, str, int] | None:
    """Load wallet address, chain, and last_synced_block from DB.

    Returns None if the wallet does not exist.
    """
    with get_sync_session() as session:
        row = session.execute(
            text("""
                SELECT address, chain, COALESCE(last_synced_block, 0)
                FROM wallets
                WHERE id = :wallet_id AND is_active = true
            """),
            {"wallet_id": str(wallet_uuid)},
        ).fetchone()

    if row is None:
        return None
    return str(row[0]), str(row[1]), int(row[2])


def _set_wallet_syncing(wallet_uuid: uuid.UUID) -> None:
    """Mark wallet as syncing."""
    with get_sync_session() as session:
        session.execute(
            text("""
                UPDATE wallets
                SET sync_status = 'syncing',
                    sync_started_at = :now,
                    updated_at = :now
                WHERE id = :wallet_id
            """),
            {"wallet_id": str(wallet_uuid), "now": datetime.now(UTC)},
        )


def _finalise_wallet(
    wallet_uuid: uuid.UUID,
    status: str,
    highest_block: int | None,
) -> None:
    """Update wallet sync status and timestamps on completion or error."""
    now = datetime.now(UTC)
    params: dict = {
        "wallet_id": str(wallet_uuid),
        "status": status,
        "now": now,
    }

    if status == "synced":
        params["completed_at"] = now
        params["synced_at"] = now
        sql = """
            UPDATE wallets
            SET sync_status = :status,
                sync_completed_at = :completed_at,
                last_synced_at = :synced_at,
                {block_clause}
                updated_at = :now
            WHERE id = :wallet_id
        """
        if highest_block is not None:
            params["block"] = highest_block
            sql = sql.replace("{block_clause}", "last_synced_block = :block,")
        else:
            sql = sql.replace("{block_clause}", "")
    else:
        sql = """
            UPDATE wallets
            SET sync_status = :status,
                updated_at = :now
            WHERE id = :wallet_id
        """

    with get_sync_session() as session:
        session.execute(text(sql), params)
