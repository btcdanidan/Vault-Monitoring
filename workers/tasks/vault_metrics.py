"""Vault metrics tasks (§10 Layer 1, §9).

refresh_vault_metrics — orchestrator task running every 5 minutes on the
default queue.  Calls each protocol adapter's ``fetch_live_metrics()`` in
parallel, computes ``net_apy``, and writes results to the ``vault_metrics``
hypertable.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import redis
import structlog
from sqlalchemy import text

from workers.celery_app import app
from workers.database import get_sync_session

if TYPE_CHECKING:
    from app.adapters.base import BaseProtocolAdapter
    from app.schemas.adapter import VaultMetricsData

logger = structlog.get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TASK_LOCK_TTL_SECONDS = 300  # 5 min — matches Beat cadence
API_USAGE_KEY_PREFIX = "api_usage"
API_USAGE_TTL_SECONDS = 172_800  # 48h

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


# ---------------------------------------------------------------------------
# Tracked-vault loading
# ---------------------------------------------------------------------------


def _load_tracked_vaults() -> dict[tuple[str, str], list[str]]:
    """Return tracked vault addresses grouped by ``(protocol, chain)``.

    Only vaults with ``is_tracked=true`` are included.
    """
    with get_sync_session() as session:
        rows = session.execute(
            text("""
                SELECT protocol, chain, vault_id
                FROM vaults
                WHERE is_tracked = true
            """)
        ).fetchall()

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for protocol, chain, vault_id in rows:
        grouped[(protocol, chain)].append(vault_id)
    return dict(grouped)


# ---------------------------------------------------------------------------
# net_apy computation
# ---------------------------------------------------------------------------

_HUNDRED = Decimal("100")


def _ensure_net_apy(metrics: list[VaultMetricsData]) -> list[VaultMetricsData]:
    """Compute ``net_apy`` for metrics where the adapter did not set it.

    Formula: ``net_apy = apy_gross * (1 - performance_fee_pct / 100) - mgmt_fee_pct``
    """
    for m in metrics:
        if m.net_apy is not None:
            continue
        if m.apy_gross is None:
            continue
        perf_fee = m.performance_fee_pct or Decimal("0")
        mgmt_fee = m.mgmt_fee_pct or Decimal("0")
        m.net_apy = m.apy_gross * (Decimal("1") - perf_fee / _HUNDRED) - mgmt_fee
    return metrics


# ---------------------------------------------------------------------------
# Persistence (sync, mirrors BaseProtocolAdapter.write_vault_metrics)
# ---------------------------------------------------------------------------

_UPSERT_VAULT_METRICS_SQL = text("""
    INSERT INTO vault_metrics (
        vault_id, chain, protocol, vault_name, asset_symbol, asset_address,
        timestamp,
        apy_gross, apy_base, apy_reward,
        performance_fee_pct, mgmt_fee_pct, net_apy,
        tvl_usd, tvl_native,
        utilisation_rate, supply_rate, borrow_rate,
        redemption_type, redemption_days_est, maturity_date
    ) VALUES (
        :vault_id, :chain, :protocol, :vault_name, :asset_symbol, :asset_address,
        :timestamp,
        :apy_gross, :apy_base, :apy_reward,
        :performance_fee_pct, :mgmt_fee_pct, :net_apy,
        :tvl_usd, :tvl_native,
        :utilisation_rate, :supply_rate, :borrow_rate,
        :redemption_type, :redemption_days_est, :maturity_date
    )
    ON CONFLICT (vault_id, chain, timestamp) DO UPDATE SET
        protocol          = EXCLUDED.protocol,
        vault_name        = EXCLUDED.vault_name,
        asset_symbol      = EXCLUDED.asset_symbol,
        asset_address     = EXCLUDED.asset_address,
        apy_gross         = EXCLUDED.apy_gross,
        apy_base          = EXCLUDED.apy_base,
        apy_reward        = EXCLUDED.apy_reward,
        performance_fee_pct = EXCLUDED.performance_fee_pct,
        mgmt_fee_pct      = EXCLUDED.mgmt_fee_pct,
        net_apy           = EXCLUDED.net_apy,
        tvl_usd           = EXCLUDED.tvl_usd,
        tvl_native        = EXCLUDED.tvl_native,
        utilisation_rate  = EXCLUDED.utilisation_rate,
        supply_rate       = EXCLUDED.supply_rate,
        borrow_rate       = EXCLUDED.borrow_rate,
        redemption_type   = EXCLUDED.redemption_type,
        redemption_days_est = EXCLUDED.redemption_days_est,
        maturity_date     = EXCLUDED.maturity_date
""")

_UPSERT_VAULTS_SQL = text("""
    INSERT INTO vaults (
        vault_id, chain, protocol, vault_name, contract_address,
        asset_symbol, asset_address, vault_type, curator,
        is_tracked, discovered_at, updated_at
    ) VALUES (
        :vault_id, :chain, :protocol, :vault_name, :contract_address,
        :asset_symbol, :asset_address, :vault_type, :curator,
        :is_tracked, now(), now()
    )
    ON CONFLICT (vault_id, chain) DO UPDATE SET
        protocol         = EXCLUDED.protocol,
        vault_name       = COALESCE(EXCLUDED.vault_name, vaults.vault_name),
        contract_address = COALESCE(EXCLUDED.contract_address, vaults.contract_address),
        asset_symbol     = COALESCE(EXCLUDED.asset_symbol, vaults.asset_symbol),
        asset_address    = COALESCE(EXCLUDED.asset_address, vaults.asset_address),
        vault_type       = COALESCE(EXCLUDED.vault_type, vaults.vault_type),
        curator          = COALESCE(EXCLUDED.curator, vaults.curator),
        updated_at       = now()
""")

MIN_TVL_FOR_TRACKING_USD = 100_000


def _persist_metrics(metrics: list[VaultMetricsData]) -> None:
    """Bulk upsert metrics into ``vault_metrics`` hypertable (sync)."""
    if not metrics:
        return
    params = [
        {
            "vault_id": m.vault_id,
            "chain": m.chain,
            "protocol": m.protocol,
            "vault_name": m.vault_name,
            "asset_symbol": m.asset_symbol,
            "asset_address": m.asset_address,
            "timestamp": m.timestamp,
            "apy_gross": str(m.apy_gross) if m.apy_gross is not None else None,
            "apy_base": str(m.apy_base) if m.apy_base is not None else None,
            "apy_reward": str(m.apy_reward) if m.apy_reward is not None else None,
            "performance_fee_pct": (
                str(m.performance_fee_pct) if m.performance_fee_pct is not None else None
            ),
            "mgmt_fee_pct": (
                str(m.mgmt_fee_pct) if m.mgmt_fee_pct is not None else None
            ),
            "net_apy": str(m.net_apy) if m.net_apy is not None else None,
            "tvl_usd": str(m.tvl_usd) if m.tvl_usd is not None else None,
            "tvl_native": str(m.tvl_native) if m.tvl_native is not None else None,
            "utilisation_rate": (
                str(m.utilisation_rate) if m.utilisation_rate is not None else None
            ),
            "supply_rate": str(m.supply_rate) if m.supply_rate is not None else None,
            "borrow_rate": str(m.borrow_rate) if m.borrow_rate is not None else None,
            "redemption_type": m.redemption_type,
            "redemption_days_est": m.redemption_days_est,
            "maturity_date": m.maturity_date.isoformat() if m.maturity_date else None,
        }
        for m in metrics
    ]
    with get_sync_session() as session:
        session.execute(_UPSERT_VAULT_METRICS_SQL, params)


def _discover_vaults(metrics: list[VaultMetricsData]) -> None:
    """Upsert discovered vaults into the ``vaults`` reference table (sync)."""
    if not metrics:
        return
    tvl_map = {m.vault_id: float(m.tvl_usd) for m in metrics if m.tvl_usd is not None}
    params = [
        {
            "vault_id": m.vault_id,
            "chain": m.chain,
            "protocol": m.protocol,
            "vault_name": m.vault_name,
            "contract_address": None,
            "asset_symbol": m.asset_symbol,
            "asset_address": m.asset_address,
            "vault_type": None,
            "curator": None,
            "is_tracked": tvl_map.get(m.vault_id, 0) >= MIN_TVL_FOR_TRACKING_USD
            if m.vault_id in tvl_map
            else True,
        }
        for m in metrics
    ]
    with get_sync_session() as session:
        session.execute(_UPSERT_VAULTS_SQL, params)


# ---------------------------------------------------------------------------
# Adapter execution (async → sync bridge)
# ---------------------------------------------------------------------------


def _run_adapter_for_chain(
    adapter: BaseProtocolAdapter,
    vault_ids: list[str],
    chain: str,
) -> list[VaultMetricsData]:
    """Call a single adapter's ``fetch_live_metrics`` from a sync context."""
    return asyncio.run(adapter.fetch_live_metrics(vault_ids, chain))


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def _track_api_usage(
    r: redis.Redis,  # type: ignore[type-arg]
    protocol: str,
    call_count: int,
) -> None:
    """Increment daily API-usage counter for a protocol (§20)."""
    if call_count <= 0:
        return
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    key = f"{API_USAGE_KEY_PREFIX}:{protocol}:fetch_live_metrics:{today}"
    pipe = r.pipeline()
    pipe.incrby(key, call_count)
    pipe.expire(key, API_USAGE_TTL_SECONDS)
    pipe.execute()


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@app.task(
    name="workers.tasks.vault_metrics.refresh_vault_metrics",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def refresh_vault_metrics(self) -> None:  # type: ignore[no-untyped-def]
    """Refresh vault metrics from all protocol adapters (5 min, default queue).

    Orchestrator that:
    1. Loads tracked vaults grouped by (protocol, chain)
    2. Runs each adapter's ``fetch_live_metrics()`` in parallel
    3. Computes ``net_apy`` where missing
    4. Persists results to ``vault_metrics`` hypertable
    5. Updates the ``vaults`` reference table
    """
    from app.adapters.registry import get_adapter

    r = _get_redis()

    if not r.set("lock:refresh_vault_metrics", "1", nx=True, ex=TASK_LOCK_TTL_SECONDS):
        logger.debug("refresh_vault_metrics_skipped", reason="already_running")
        return

    try:
        grouped = _load_tracked_vaults()
        if not grouped:
            logger.debug("refresh_vault_metrics_skipped", reason="no_tracked_vaults")
            return

        logger.info(
            "refresh_vault_metrics_start",
            protocol_chain_groups=len(grouped),
            total_vaults=sum(len(v) for v in grouped.values()),
        )

        all_metrics: list[VaultMetricsData] = []
        errors: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=len(grouped)) as pool:
            future_to_key: dict = {}
            for (protocol, chain), vault_ids in grouped.items():
                adapter = get_adapter(protocol)
                if adapter is None:
                    logger.warning(
                        "refresh_vault_metrics_no_adapter",
                        protocol=protocol,
                        chain=chain,
                    )
                    continue
                future = pool.submit(
                    _run_adapter_for_chain, adapter, vault_ids, chain,
                )
                future_to_key[future] = (protocol, chain)

            for future in as_completed(future_to_key):
                protocol, chain = future_to_key[future]
                try:
                    metrics = future.result()
                    metrics = _ensure_net_apy(metrics)
                    all_metrics.extend(metrics)
                    _track_api_usage(r, protocol, 1)
                    logger.info(
                        "refresh_vault_metrics_adapter_done",
                        protocol=protocol,
                        chain=chain,
                        metrics_count=len(metrics),
                    )
                except Exception:
                    errors[f"{protocol}:{chain}"] = "adapter_fetch_failed"
                    logger.error(
                        "refresh_vault_metrics_adapter_failed",
                        protocol=protocol,
                        chain=chain,
                        exc_info=True,
                    )

        if all_metrics:
            _persist_metrics(all_metrics)
            _discover_vaults(all_metrics)

        logger.info(
            "refresh_vault_metrics_complete",
            metrics_written=len(all_metrics),
            adapter_errors=len(errors),
            error_details=errors if errors else None,
        )

    except Exception as exc:
        logger.error("refresh_vault_metrics_failed", exc_info=True)
        raise self.retry(exc=exc) from exc
    finally:
        r.delete("lock:refresh_vault_metrics")


@app.task(name="workers.tasks.vault_metrics.compute_vault_whale_concentration")
def compute_vault_whale_concentration() -> None:
    """Placeholder: compute whale concentration per vault (6h regular / 24h full)."""
    pass
