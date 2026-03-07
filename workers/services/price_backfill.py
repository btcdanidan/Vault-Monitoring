"""Sync DeFiLlama historical price backfill for reconstruction pipeline (§12, §9).

Fetches historical prices for each unique (chain, asset_address, timestamp)
found in raw events.  Uses sync httpx to match the worker task pattern in
workers/tasks/prices.py.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

import httpx
import redis
import structlog
from sqlalchemy import text

from workers.services.schemas import EnrichedEvent, RawEvent

logger = structlog.get_logger(__name__)

DEFILLAMA_BASE_URL = os.getenv("DEFILLAMA_BASE_URL", "https://coins.llama.fi")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
API_USAGE_KEY_PREFIX = "api_usage:defillama"
API_USAGE_TTL_SECONDS = 172_800  # 48h
MAX_COINS_PER_REQUEST = 100
HTTP_TIMEOUT = 30.0

# DeFiLlama uses lowercase chain names different from our internal names
CHAIN_TO_DEFILLAMA: dict[str, str] = {
    "ethereum": "ethereum",
    "base": "base",
    "solana": "solana",
}

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


def _format_coin_id(chain: str, address: str) -> str:
    ll_chain = CHAIN_TO_DEFILLAMA.get(chain, chain)
    return f"{ll_chain}:{address}"


def _track_api_usage(r: redis.Redis, call_count: int) -> None:  # type: ignore[type-arg]
    if call_count <= 0:
        return
    key = f"{API_USAGE_KEY_PREFIX}:{datetime.now(UTC).strftime('%Y-%m-%d')}"
    pipe = r.pipeline()
    pipe.incrby(key, call_count)
    pipe.expire(key, API_USAGE_TTL_SECONDS)
    pipe.execute()


def _fetch_historical_prices_batch(
    coins: list[tuple[str, str]],
    timestamp: int,
    http_client: httpx.Client,
) -> dict[tuple[str, str], Decimal]:
    """Fetch historical prices for multiple coins at one timestamp.

    Returns mapping of (chain, asset_address) → price_usd.
    """
    results: dict[tuple[str, str], Decimal] = {}
    batches = [
        coins[i : i + MAX_COINS_PER_REQUEST]
        for i in range(0, len(coins), MAX_COINS_PER_REQUEST)
    ]

    for batch in batches:
        coin_ids = ",".join(_format_coin_id(c, a) for c, a in batch)
        url = f"{DEFILLAMA_BASE_URL}/prices/historical/{timestamp}/{quote(coin_ids, safe=':,')}"

        try:
            resp = http_client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.warning("backfill_fetch_failed", url=url[:120], exc_info=True)
            continue

        data = resp.json()
        for coin_id, entry in data.get("coins", {}).items():
            try:
                parts = coin_id.split(":", 1)
                if len(parts) != 2:
                    continue
                ll_chain, address = parts
                chain = next(
                    (k for k, v in CHAIN_TO_DEFILLAMA.items() if v == ll_chain),
                    ll_chain,
                )
                results[(chain, address)] = Decimal(str(entry["price"]))
            except (KeyError, ValueError):
                logger.debug("backfill_parse_failed", coin_id=coin_id, exc_info=True)

    return results


def _persist_prices(prices: list[dict]) -> None:
    """Bulk upsert historical prices into price_history hypertable."""
    if not prices:
        return
    from workers.database import get_sync_session

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
        session.execute(stmt, prices)


def backfill_prices(events: list[RawEvent]) -> list[EnrichedEvent]:
    """Backfill historical prices for all events and return enriched events.

    Groups events by unix timestamp to minimise DeFiLlama API calls (each
    historical request targets a single timestamp but accepts multiple coins).
    Persists fetched prices to the ``price_history`` table.
    """
    if not events:
        return []

    by_timestamp: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for ev in events:
        ts = int(ev.timestamp.timestamp())
        key = (ev.chain, ev.asset_address)
        if key not in by_timestamp[ts]:
            by_timestamp[ts].append(key)

    all_prices: dict[tuple[str, str, int], Decimal] = {}
    price_rows: list[dict] = []
    api_calls = 0

    with httpx.Client(timeout=HTTP_TIMEOUT) as http_client:
        for ts, coins in by_timestamp.items():
            batch_prices = _fetch_historical_prices_batch(coins, ts, http_client)
            api_calls += 1

            for (chain, address), price_usd in batch_prices.items():
                all_prices[(chain, address, ts)] = price_usd
                price_rows.append({
                    "asset_address": address,
                    "chain": chain,
                    "timestamp": datetime.fromtimestamp(ts, tz=UTC),
                    "price_usd": str(price_usd),
                    "source": "defillama",
                })

    _persist_prices(price_rows)

    r = _get_redis()
    _track_api_usage(r, api_calls)

    logger.info(
        "backfill_prices_complete",
        events=len(events),
        unique_timestamps=len(by_timestamp),
        prices_fetched=len(all_prices),
        api_calls=api_calls,
    )

    enriched: list[EnrichedEvent] = []
    for ev in events:
        ts = int(ev.timestamp.timestamp())
        price_usd = all_prices.get((ev.chain, ev.asset_address, ts))
        enriched.append(EnrichedEvent.from_raw(ev, price_usd))

    return enriched
