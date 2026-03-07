"""Vault metrics and lifecycle tasks (§10).

refresh_vault_metrics: Fetch live metrics for all tracked vaults every 5 min.
check_vault_lifecycle: Daily check to detrack zero-TVL vaults after 7 days.
compute_vault_whale_concentration: Placeholder for whale concentration.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict

import redis
import structlog
from workers.celery_app import app

logger = structlog.get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TASK_LOCK_TTL_SECONDS = 300  # 5 min

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


def _build_async_db_url() -> str:
    """Build an asyncpg URL from worker environment variables."""
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "defi_vault")
    password = os.getenv("CELERY_DB_PASSWORD", "celery_worker_password")
    return f"postgresql+asyncpg://celery_worker:{password}@{host}:{port}/{db}"


async def _refresh_metrics_async() -> dict[str, int]:
    """Async core: fetch and store metrics for all tracked vaults.

    Returns a summary dict with counts per protocol.
    """
    from redis.asyncio import Redis as AsyncRedis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.adapters.registry import get_adapter
    from app.services.vault_discovery import get_tracked_vault_ids

    engine = create_async_engine(_build_async_db_url(), pool_size=2, max_overflow=1)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async_redis = AsyncRedis.from_url(REDIS_URL, decode_responses=True)

    summary: dict[str, int] = {}

    try:
        async with session_maker() as db:
            tracked = await get_tracked_vault_ids(db)

        if not tracked:
            logger.debug("refresh_vault_metrics_skipped", reason="no_tracked_vaults")
            return summary

        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for vault_id, chain, protocol in tracked:
            groups[(protocol, chain)].append(vault_id)

        for (protocol, chain), vault_ids in groups.items():
            adapter = get_adapter(protocol)
            if adapter is None:
                logger.warning("refresh_vault_metrics_no_adapter", protocol=protocol)
                continue

            try:
                async with session_maker() as db:
                    metrics = await adapter.fetch_and_store_metrics(
                        db, async_redis, vault_ids, chain,
                    )
                    await db.commit()
                    summary[f"{protocol}:{chain}"] = len(metrics)

                logger.info(
                    "refresh_vault_metrics_group_complete",
                    protocol=protocol,
                    chain=chain,
                    vaults_requested=len(vault_ids),
                    metrics_returned=len(metrics),
                )
            except Exception:
                logger.error(
                    "refresh_vault_metrics_group_failed",
                    protocol=protocol,
                    chain=chain,
                    exc_info=True,
                )
            finally:
                await adapter.close()

    finally:
        await async_redis.aclose()
        await engine.dispose()

    return summary


@app.task(
    name="workers.tasks.vault_metrics.refresh_vault_metrics",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def refresh_vault_metrics(self) -> None:  # type: ignore[no-untyped-def]
    """Refresh vault metrics from protocol APIs for all tracked vaults (5 min, default queue)."""
    r = _get_redis()

    if not r.set("lock:refresh_vault_metrics", "1", nx=True, ex=TASK_LOCK_TTL_SECONDS):
        logger.debug("refresh_vault_metrics_skipped", reason="already_running")
        return

    try:
        logger.info("refresh_vault_metrics_start")
        summary = asyncio.run(_refresh_metrics_async())

        total = sum(summary.values())
        logger.info(
            "refresh_vault_metrics_complete",
            total_metrics=total,
            groups=summary,
        )
    except Exception as exc:
        logger.error("refresh_vault_metrics_failed", exc_info=True)
        raise self.retry(exc=exc) from exc
    finally:
        r.delete("lock:refresh_vault_metrics")


@app.task(name="workers.tasks.vault_metrics.check_vault_lifecycle")
def check_vault_lifecycle() -> None:
    """Daily task: detrack vaults with zero TVL for 7 consecutive days."""
    from workers.services.vault_discovery import detrack_stale_vaults_sync

    logger.info("check_vault_lifecycle_start")
    count = detrack_stale_vaults_sync()
    logger.info("check_vault_lifecycle_complete", detracked=count)


@app.task(name="workers.tasks.vault_metrics.compute_vault_whale_concentration")
def compute_vault_whale_concentration() -> None:
    """Placeholder: compute whale concentration per vault (6h regular / 24h full)."""
