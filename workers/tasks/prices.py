"""Price feed and health factor tasks (§9, §10)."""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

import httpx
import redis
import structlog
from sqlalchemy import text

from workers.celery_app import app
from workers.database import get_sync_session

logger = structlog.get_logger()

DEFILLAMA_BASE_URL = os.getenv("DEFILLAMA_BASE_URL", "https://coins.llama.fi")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PRICE_CACHE_TTL_SECONDS = 30
PRICE_PUBSUB_CHANNEL = "price_updates"
API_USAGE_KEY_PREFIX = "api_usage:defillama"
API_USAGE_TTL_SECONDS = 172_800  # 48h
MAX_COINS_PER_REQUEST = 100
TASK_LOCK_TTL_SECONDS = 60

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


def _get_tracked_assets() -> list[tuple[str, str]]:
    """Query distinct (chain, asset_address) pairs from active positions."""
    with get_sync_session() as session:
        result = session.execute(
            text("""
                SELECT DISTINCT chain, asset_address
                FROM positions
                WHERE status = 'active'
                  AND asset_address IS NOT NULL
            """)
        )
        return [(row[0], row[1]) for row in result.fetchall()]


def _format_coin_id(chain: str, address: str) -> str:
    return f"{chain}:{address}"


def _fetch_current_prices(
    coins: list[tuple[str, str]],
) -> tuple[list[dict], int]:
    """Fetch current prices from DeFiLlama.

    Returns (parsed price dicts, successful API call count).
    """
    results: list[dict] = []
    api_calls = 0
    batches = [
        coins[i : i + MAX_COINS_PER_REQUEST]
        for i in range(0, len(coins), MAX_COINS_PER_REQUEST)
    ]

    with httpx.Client(timeout=15.0) as client:
        for batch in batches:
            coin_ids = ",".join(_format_coin_id(c, a) for c, a in batch)
            url = (
                f"{DEFILLAMA_BASE_URL}/prices/current"
                f"/{quote(coin_ids, safe=':,')}"
            )

            try:
                resp = client.get(url)
                resp.raise_for_status()
                api_calls += 1
            except httpx.HTTPError:
                logger.warning(
                    "defillama_fetch_failed", url=url, exc_info=True,
                )
                continue

            data = resp.json()
            for coin_id, entry in data.get("coins", {}).items():
                try:
                    chain, address = coin_id.split(":", 1)
                    results.append({
                        "asset_address": address,
                        "chain": chain,
                        "price_usd": Decimal(str(entry["price"])),
                        "timestamp": datetime.fromtimestamp(
                            entry["timestamp"], tz=UTC,
                        ),
                        "confidence": entry.get("confidence", 0.0),
                        "source": "defillama",
                    })
                except (KeyError, ValueError):
                    logger.warning(
                        "defillama_parse_failed",
                        coin_id=coin_id,
                        exc_info=True,
                    )

    return results, api_calls


def _cache_prices(
    r: redis.Redis, prices: list[dict],  # type: ignore[type-arg]
) -> None:
    """Write prices to Redis with 30s TTL."""
    pipe = r.pipeline()
    for p in prices:
        key = f"price:current:{p['chain']}:{p['asset_address']}"
        value = json.dumps({
            "asset_address": p["asset_address"],
            "chain": p["chain"],
            "price_usd": str(p["price_usd"]),
            "timestamp": p["timestamp"].isoformat(),
            "confidence": p["confidence"],
            "source": p["source"],
        })
        pipe.set(key, value, ex=PRICE_CACHE_TTL_SECONDS)
    pipe.execute()


def _persist_prices(prices: list[dict]) -> None:
    """Bulk upsert into price_history hypertable."""
    with get_sync_session() as session:
        stmt = text("""
            INSERT INTO price_history
                (asset_address, chain, timestamp, price_usd, source)
            VALUES
                (:asset_address, :chain, :timestamp, :price_usd, :source)
            ON CONFLICT (asset_address, chain, timestamp) DO UPDATE
                SET price_usd = EXCLUDED.price_usd,
                    source = EXCLUDED.source
        """)
        params = [
            {
                "asset_address": p["asset_address"],
                "chain": p["chain"],
                "timestamp": p["timestamp"],
                "price_usd": str(p["price_usd"]),
                "source": p["source"],
            }
            for p in prices
        ]
        session.execute(stmt, params)


def _publish_price_updates(
    r: redis.Redis, prices: list[dict],  # type: ignore[type-arg]
) -> None:
    """Publish price updates to Redis pub/sub for WebSocket consumers."""
    if not prices:
        return
    msg = {
        "updates": [
            {
                "asset_address": p["asset_address"],
                "chain": p["chain"],
                "price_usd": str(p["price_usd"]),
                "timestamp": p["timestamp"].isoformat(),
                "confidence": p["confidence"],
                "source": p["source"],
            }
            for p in prices
        ],
        "published_at": datetime.now(UTC).isoformat(),
    }
    r.publish(PRICE_PUBSUB_CHANNEL, json.dumps(msg))


def _track_api_usage(
    r: redis.Redis, call_count: int,  # type: ignore[type-arg]
) -> None:
    """Increment daily DeFiLlama API call counter (§20)."""
    if call_count <= 0:
        return
    key = (
        f"{API_USAGE_KEY_PREFIX}"
        f":{datetime.now(UTC).strftime('%Y-%m-%d')}"
    )
    pipe = r.pipeline()
    pipe.incrby(key, call_count)
    pipe.expire(key, API_USAGE_TTL_SECONDS)
    pipe.execute()


@app.task(
    name="workers.tasks.prices.refresh_prices",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    acks_late=True,
)
def refresh_prices(self) -> None:  # type: ignore[no-untyped-def]
    """Refresh current token prices from DeFiLlama (30s, critical queue)."""
    r = _get_redis()

    if not r.set("lock:refresh_prices", "1", nx=True, ex=TASK_LOCK_TTL_SECONDS):
        logger.debug("refresh_prices_skipped", reason="already_running")
        return

    try:
        tracked = _get_tracked_assets()
        if not tracked:
            logger.debug("refresh_prices_skipped", reason="no_tracked_assets")
            return

        logger.info("refresh_prices_start", asset_count=len(tracked))

        prices, api_calls = _fetch_current_prices(tracked)
        if not prices:
            logger.warning("refresh_prices_no_data", asset_count=len(tracked))
            return

        _cache_prices(r, prices)
        _persist_prices(prices)
        _publish_price_updates(r, prices)
        _track_api_usage(r, api_calls)

        logger.info(
            "refresh_prices_complete",
            prices_fetched=len(prices),
            assets_requested=len(tracked),
        )
    except Exception as exc:
        logger.error("refresh_prices_failed", exc_info=True)
        raise self.retry(exc=exc) from exc
    finally:
        r.delete("lock:refresh_prices")


@app.task(name="workers.tasks.prices.refresh_health_factors")
def refresh_health_factors() -> None:
    """Placeholder: refresh health factors for lending positions."""
    pass
