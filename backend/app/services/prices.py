"""Price feed service — DeFiLlama integration for current & historical prices (§9, §10)."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
import structlog
from sqlalchemy import text

from app.config import get_settings
from app.schemas.price import PriceData, PriceResponse, PriceUpdate

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

PRICE_CACHE_TTL_SECONDS = 30
PRICE_PUBSUB_CHANNEL = "price_updates"
API_USAGE_KEY_PREFIX = "api_usage:defillama"
API_USAGE_TTL_SECONDS = 172_800  # 48 hours
MAX_COINS_PER_REQUEST = 100


def format_coin_id(chain: str, address: str) -> str:
    """Format a chain/address pair into a DeFiLlama coin identifier."""
    return f"{chain}:{address}"


def _parse_coin_id(coin_id: str) -> tuple[str, str]:
    """Extract (chain, address) from a DeFiLlama coin identifier."""
    chain, address = coin_id.split(":", 1)
    return chain, address


def _cache_key(chain: str, address: str) -> str:
    return f"price:current:{chain}:{address}"


def _api_usage_key() -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{API_USAGE_KEY_PREFIX}:{today}"


def _parse_price_entry(
    coin_id: str,
    entry: dict,
    source: str = "defillama",
) -> PriceData:
    """Parse a single coin entry from a DeFiLlama response."""
    chain, address = _parse_coin_id(coin_id)
    return PriceData(
        asset_address=address,
        chain=chain,
        price_usd=Decimal(str(entry["price"])),
        timestamp=datetime.fromtimestamp(entry["timestamp"], tz=UTC),
        confidence=entry.get("confidence", 0.0),
        source=source,
    )


class PriceService:
    """Async price service for backend use (FastAPI endpoints, reconstruction)."""

    def __init__(
        self,
        redis: Redis,  # type: ignore[type-arg]
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._redis = redis
        self._http = http_client
        self._owns_http = http_client is None
        self._settings = get_settings()

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def close(self) -> None:
        """Close the HTTP client if we created it."""
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> PriceService:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get_current_prices(
        self,
        coins: list[tuple[str, str]],
    ) -> dict[tuple[str, str], PriceData]:
        """Fetch current prices for a batch of (chain, address) pairs."""
        if not coins:
            return {}

        results: dict[tuple[str, str], PriceData] = {}
        batches = [
            coins[i : i + MAX_COINS_PER_REQUEST]
            for i in range(0, len(coins), MAX_COINS_PER_REQUEST)
        ]

        client = await self._get_http()
        base = self._settings.defillama_base_url

        for batch in batches:
            coin_ids = ",".join(format_coin_id(c, a) for c, a in batch)
            url = f"{base}/prices/current/{quote(coin_ids, safe=':,')}"

            try:
                resp = await client.get(url)
                resp.raise_for_status()
                await self._track_api_call("prices/current")
            except httpx.HTTPError:
                logger.warning(
                    "defillama_current_prices_failed", url=url, exc_info=True,
                )
                continue

            data = resp.json()
            for coin_id, entry in data.get("coins", {}).items():
                try:
                    price = _parse_price_entry(coin_id, entry)
                    results[(price.chain, price.asset_address)] = price
                except (KeyError, ValueError):
                    logger.warning(
                        "defillama_parse_failed", coin_id=coin_id, exc_info=True,
                    )

        return results

    async def get_historical_price(
        self,
        chain: str,
        address: str,
        timestamp: int,
    ) -> PriceData | None:
        """Fetch a single historical price from DeFiLlama."""
        client = await self._get_http()
        base = self._settings.defillama_base_url
        coin_id = format_coin_id(chain, address)
        url = f"{base}/prices/historical/{timestamp}/{quote(coin_id, safe=':')}"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
            await self._track_api_call("prices/historical")
        except httpx.HTTPError:
            logger.warning(
                "defillama_historical_price_failed", url=url, exc_info=True,
            )
            return None

        data = resp.json()
        entry = data.get("coins", {}).get(coin_id)
        if entry is None:
            return None

        try:
            return _parse_price_entry(coin_id, entry)
        except (KeyError, ValueError):
            logger.warning(
                "defillama_parse_failed", coin_id=coin_id, exc_info=True,
            )
            return None

    async def get_batch_historical_prices(
        self,
        requests: list[tuple[str, str, int]],
    ) -> list[PriceData]:
        """Fetch historical prices for multiple (chain, address, timestamp) tuples.

        Groups by timestamp to minimise API calls (DeFiLlama accepts multiple
        coins per historical request, but each request targets a single timestamp).
        """
        by_timestamp: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for chain, address, ts in requests:
            by_timestamp[ts].append((chain, address))

        results: list[PriceData] = []
        client = await self._get_http()
        base = self._settings.defillama_base_url

        for ts, coins in by_timestamp.items():
            chunks = [
                coins[i : i + MAX_COINS_PER_REQUEST]
                for i in range(0, len(coins), MAX_COINS_PER_REQUEST)
            ]
            for batch in chunks:
                coin_ids = ",".join(format_coin_id(c, a) for c, a in batch)
                url = (
                    f"{base}/prices/historical/{ts}"
                    f"/{quote(coin_ids, safe=':,')}"
                )

                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    await self._track_api_call("prices/historical")
                except httpx.HTTPError:
                    logger.warning(
                        "defillama_batch_historical_failed",
                        url=url,
                        exc_info=True,
                    )
                    continue

                data = resp.json()
                for coin_id, entry in data.get("coins", {}).items():
                    try:
                        results.append(_parse_price_entry(coin_id, entry))
                    except (KeyError, ValueError):
                        logger.warning(
                            "defillama_parse_failed",
                            coin_id=coin_id,
                            exc_info=True,
                        )

        return results

    # ── Redis caching ────────────────────────────────────────────────────

    async def cache_prices(self, prices: list[PriceData]) -> None:
        """Write prices to Redis with 30s TTL."""
        pipe = self._redis.pipeline()
        for p in prices:
            key = _cache_key(p.chain, p.asset_address)
            value = json.dumps({
                "asset_address": p.asset_address,
                "chain": p.chain,
                "price_usd": str(p.price_usd),
                "timestamp": p.timestamp.isoformat(),
                "confidence": p.confidence,
                "source": p.source,
            })
            pipe.set(key, value, ex=PRICE_CACHE_TTL_SECONDS)
        await pipe.execute()

    async def get_cached_price(
        self, chain: str, address: str,
    ) -> PriceData | None:
        """Read a single cached price from Redis."""
        raw = await self._redis.get(_cache_key(chain, address))
        if raw is None:
            return None
        data = json.loads(raw)
        return PriceData(
            asset_address=data["asset_address"],
            chain=data["chain"],
            price_usd=Decimal(data["price_usd"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            confidence=data["confidence"],
            source=data["source"],
        )

    # ── Redis pub/sub ────────────────────────────────────────────────────

    async def publish_price_updates(self, prices: list[PriceData]) -> None:
        """Publish price updates to Redis pub/sub for WebSocket consumers."""
        if not prices:
            return
        msg = PriceUpdate(
            updates=[
                PriceResponse(
                    asset_address=p.asset_address,
                    chain=p.chain,
                    price_usd=p.price_usd,
                    timestamp=p.timestamp,
                    confidence=p.confidence,
                    source=p.source,
                )
                for p in prices
            ],
            published_at=datetime.now(UTC),
        )
        await self._redis.publish(
            PRICE_PUBSUB_CHANNEL,
            msg.model_dump_json(),
        )

    # ── Database persistence ─────────────────────────────────────────────

    async def persist_prices(
        self,
        session: AsyncSession,
        prices: list[PriceData],
    ) -> None:
        """Bulk upsert prices into the price_history hypertable."""
        if not prices:
            return

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
                "asset_address": p.asset_address,
                "chain": p.chain,
                "timestamp": p.timestamp,
                "price_usd": str(p.price_usd),
                "source": p.source,
            }
            for p in prices
        ]
        await session.execute(stmt, params)

    # ── API usage tracking (§20) ─────────────────────────────────────────

    async def _track_api_call(self, endpoint: str) -> None:
        """Increment daily API usage counter in Redis."""
        key = _api_usage_key()
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, API_USAGE_TTL_SECONDS)
        await pipe.execute()
        logger.debug("defillama_api_call_tracked", endpoint=endpoint)
