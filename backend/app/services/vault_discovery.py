"""Vault discovery and is_tracked lifecycle management (§10).

Handles two discovery triggers:
1. User adds wallet → reconstruction finds positions in undiscovered vaults
2. Protocol adapter scan finds vault with TVL > $100K

is_tracked lifecycle:
- true: adapter actively refreshes vault_metrics every 5 min
- false: vault exists in reference table but not refreshed
- Auto-toggle off: TVL drops to zero for 7 consecutive days
- Manual toggle: admin via direct DB update
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

ZERO_TVL_DETRACK_DAYS = 7


@dataclass(frozen=True, slots=True)
class VaultRef:
    """Minimal vault reference extracted from event data."""

    vault_id: str
    chain: str
    protocol: str
    asset_symbol: str | None = None
    asset_address: str | None = None


def _extract_vault_refs(
    events: list[object],
) -> list[VaultRef]:
    """Deduplicate vault references from a list of event-like objects.

    Accepts both ``app.schemas.adapter.RawEvent`` (Pydantic) and
    ``workers.services.schemas.RawEvent`` (dataclass) — any object with
    ``vault_or_market_id``, ``chain``, ``protocol``, ``asset_symbol``, and
    ``asset_address`` attributes.
    """
    seen: dict[tuple[str, str], VaultRef] = {}
    for ev in events:
        vault_id: str = getattr(ev, "vault_or_market_id", "")
        chain: str = getattr(ev, "chain", "")
        if not vault_id or not chain:
            continue
        key = (vault_id, chain)
        if key not in seen:
            seen[key] = VaultRef(
                vault_id=vault_id,
                chain=chain,
                protocol=getattr(ev, "protocol", "unknown"),
                asset_symbol=getattr(ev, "asset_symbol", None),
                asset_address=getattr(ev, "asset_address", None),
            )
    return list(seen.values())


async def discover_vaults_from_events(
    db: AsyncSession,
    events: list[object],
) -> int:
    """Extract unique vaults from raw events and upsert into the vaults table.

    New vaults are marked ``is_tracked=true`` (the user has a position there).
    Existing vaults are updated with COALESCE to avoid overwriting richer data
    from adapter scans.

    Returns the number of vaults upserted.
    """
    refs = _extract_vault_refs(events)
    if not refs:
        return 0

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

    params = [
        {
            "vault_id": ref.vault_id,
            "chain": ref.chain,
            "protocol": ref.protocol,
            "asset_symbol": ref.asset_symbol,
            "asset_address": ref.asset_address,
        }
        for ref in refs
    ]
    await db.execute(stmt, params)
    logger.info("vaults_discovered_from_events", count=len(refs))
    return len(refs)


async def get_tracked_vault_ids(
    db: AsyncSession,
    *,
    chain: str | None = None,
    protocol: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return (vault_id, chain, protocol) tuples for all tracked vaults.

    Optionally filtered by *chain* and/or *protocol*.
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
    stmt = text(f"SELECT vault_id, chain, protocol FROM vaults WHERE {where}")  # noqa: S608

    result = await db.execute(stmt, params)
    rows = result.fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


async def detrack_stale_vaults(db: AsyncSession) -> int:
    """Set is_tracked=false on vaults with zero TVL for 7 consecutive days.

    Skips vaults that:
    - Have active user positions (users need live data)
    - Have no vault_metrics history at all (newly discovered, not yet scanned)

    Returns the number of vaults detracked.
    """
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

    result = await db.execute(stmt)
    count = result.rowcount
    if count > 0:
        logger.info("vaults_detracked", count=count, days=ZERO_TVL_DETRACK_DAYS)
    return count
