"""Sync vault discovery for Celery workers (§10).

Thin wrapper around SQL that registers vaults found during wallet history
reconstruction.  Uses the sync ``get_sync_session`` from ``workers.database``.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session
from workers.services.schemas import RawEvent

logger = structlog.get_logger(__name__)

ZERO_TVL_DETRACK_DAYS = 7


def discover_vaults_from_raw_events(events: Sequence[RawEvent]) -> int:
    """Extract unique vaults from raw events and upsert into the vaults table.

    New vaults are marked ``is_tracked=true`` since the user has a position
    there.  Existing vaults get ``is_tracked`` forced to true (user-referenced
    vaults should always be tracked).

    Returns the number of vaults upserted.
    """
    seen: dict[tuple[str, str], dict[str, str | None]] = {}
    for ev in events:
        if not ev.vault_or_market_id or not ev.chain:
            continue
        key = (ev.vault_or_market_id, ev.chain)
        if key not in seen:
            seen[key] = {
                "vault_id": ev.vault_or_market_id,
                "chain": ev.chain,
                "protocol": ev.protocol,
                "asset_symbol": ev.asset_symbol,
                "asset_address": ev.asset_address,
            }

    if not seen:
        return 0

    from workers.database import get_sync_session

    with get_sync_session() as session:
        _upsert_vaults(session, list(seen.values()))

    logger.info("vaults_discovered_from_events", count=len(seen))
    return len(seen)


def get_tracked_vault_ids_sync(
    *,
    chain: str | None = None,
    protocol: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return (vault_id, chain, protocol) for all tracked vaults (sync).

    Used by Celery tasks to determine which vaults need metric refresh.
    """
    conditions = ["is_tracked = true"]
    params: dict[str, str] = {}

    if chain is not None:
        conditions.append("chain = :chain")
        params["chain"] = chain
    if protocol is not None:
        conditions.append("protocol = :protocol")
        params["protocol"] = protocol

    where = " AND ".join(conditions)
    sql = f"SELECT vault_id, chain, protocol FROM vaults WHERE {where}"  # noqa: S608

    from workers.database import get_sync_session

    with get_sync_session() as session:
        rows = session.execute(text(sql), params).fetchall()

    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def detrack_stale_vaults_sync() -> int:
    """Set is_tracked=false on vaults with zero TVL for 7 consecutive days.

    Sync version for use in Celery tasks.
    """
    from workers.database import get_sync_session

    stmt = text(f"""
        UPDATE vaults
        SET is_tracked = false, updated_at = now()
        WHERE is_tracked = true
          AND (vault_id, chain) NOT IN (
              SELECT DISTINCT vault_or_market_id, chain
              FROM positions
              WHERE status = 'active'
          )
          AND NOT EXISTS (
              SELECT 1 FROM vault_metrics vm
              WHERE vm.vault_id = vaults.vault_id
                AND vm.chain = vaults.chain
                AND vm.timestamp >= now() - INTERVAL '{ZERO_TVL_DETRACK_DAYS} days'
                AND vm.tvl_usd > 0
          )
          AND EXISTS (
              SELECT 1 FROM vault_metrics vm
              WHERE vm.vault_id = vaults.vault_id
                AND vm.chain = vaults.chain
          )
    """)

    with get_sync_session() as session:
        result = session.execute(stmt)
        count = result.rowcount

    if count > 0:
        logger.info("vaults_detracked", count=count, days=ZERO_TVL_DETRACK_DAYS)
    return count


def _upsert_vaults(session: Session, vault_params: list[dict[str, str | None]]) -> None:
    """Bulk upsert vault rows."""
    stmt = text("""
        INSERT INTO vaults (
            vault_id, chain, protocol,
            asset_symbol, asset_address,
            is_tracked, discovered_at, updated_at
        ) VALUES (
            :vault_id, :chain, :protocol,
            :asset_symbol, :asset_address,
            true, now(), now()
        )
        ON CONFLICT (vault_id, chain) DO UPDATE SET
            protocol     = EXCLUDED.protocol,
            asset_symbol = COALESCE(EXCLUDED.asset_symbol, vaults.asset_symbol),
            asset_address = COALESCE(EXCLUDED.asset_address, vaults.asset_address),
            is_tracked   = true,
            updated_at   = now()
    """)
    session.execute(stmt, vault_params)
