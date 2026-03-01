"""Tests for price feed service — DeFiLlama integration (§9, §10)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.schemas.price import PriceData
from app.services.prices import (
    PRICE_CACHE_TTL_SECONDS,
    PRICE_PUBSUB_CHANNEL,
    PriceService,
    _api_usage_key,
    _cache_key,
    _parse_price_entry,
    format_coin_id,
)

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def _make_mock_redis() -> MagicMock:
    """Create a mock Redis that mirrors redis.asyncio.Redis behaviour.

    pipeline() is sync (returns pipeline object directly).
    Pipeline methods (set, incr, expire) are sync accumulators.
    pipeline.execute() is async.
    Redis.get/publish are async.
    """
    r = MagicMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    r.pipeline = MagicMock(return_value=pipe)
    r.get = AsyncMock(return_value=None)
    r.publish = AsyncMock(return_value=0)
    return r


@pytest.fixture
async def price_db_session():
    """Lightweight async session with only the price_history table (avoids JSONB)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS price_history (
                asset_address VARCHAR(100) NOT NULL,
                chain VARCHAR(20) NOT NULL,
                timestamp DATETIME NOT NULL,
                price_usd NUMERIC(24, 8),
                source VARCHAR(20),
                PRIMARY KEY (asset_address, chain, timestamp)
            )
        """))
    session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with session_maker() as session:
        yield session
        await session.rollback()
        await session.execute(text("DELETE FROM price_history"))
        await session.commit()
    await engine.dispose()


class TestFormatCoinId:
    """Coin ID formatting."""

    def test_ethereum_address(self) -> None:
        assert format_coin_id("ethereum", WETH_ADDRESS) == f"ethereum:{WETH_ADDRESS}"

    def test_base_address(self) -> None:
        assert format_coin_id("base", USDC_ADDRESS) == f"base:{USDC_ADDRESS}"

    def test_solana_address(self) -> None:
        addr = "So11111111111111111111111111111111111111112"
        assert format_coin_id("solana", addr) == f"solana:{addr}"


class TestCacheKey:
    """Redis cache key formatting."""

    def test_cache_key_format(self) -> None:
        key = _cache_key("ethereum", WETH_ADDRESS)
        assert key == f"price:current:ethereum:{WETH_ADDRESS}"


class TestApiUsageKey:
    """API usage counter key formatting."""

    def test_includes_date(self) -> None:
        key = _api_usage_key()
        assert key.startswith("api_usage:defillama:")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert key.endswith(today)


class TestParsePriceEntry:
    """DeFiLlama response parsing."""

    def test_valid_entry(self) -> None:
        entry = {
            "price": 1921.93,
            "symbol": "WETH",
            "timestamp": 1672531200,
            "confidence": 0.99,
            "decimals": 18,
        }
        result = _parse_price_entry(f"ethereum:{WETH_ADDRESS}", entry)
        assert result.chain == "ethereum"
        assert result.asset_address == WETH_ADDRESS
        assert result.price_usd == Decimal("1921.93")
        assert result.confidence == 0.99
        assert result.source == "defillama"

    def test_missing_confidence_defaults_zero(self) -> None:
        entry = {"price": 100.0, "timestamp": 1672531200}
        result = _parse_price_entry("base:0x1234", entry)
        assert result.confidence == 0.0

    def test_missing_price_raises(self) -> None:
        entry = {"timestamp": 1672531200}
        with pytest.raises(KeyError):
            _parse_price_entry("ethereum:0xabc", entry)


class TestPriceServiceCurrentPrices:
    """PriceService.get_current_prices with mocked httpx."""

    async def test_empty_coins_returns_empty(self) -> None:
        service = PriceService(redis=_make_mock_redis())
        result = await service.get_current_prices([])
        assert result == {}

    async def test_successful_fetch(self) -> None:
        response_json = {
            "coins": {
                f"ethereum:{WETH_ADDRESS}": {
                    "price": 2000.50,
                    "symbol": "WETH",
                    "timestamp": 1672531200,
                    "confidence": 0.99,
                    "decimals": 18,
                },
            }
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        service = PriceService(redis=_make_mock_redis(), http_client=mock_client)
        result = await service.get_current_prices([("ethereum", WETH_ADDRESS)])

        assert ("ethereum", WETH_ADDRESS) in result
        price = result[("ethereum", WETH_ADDRESS)]
        assert price.price_usd == Decimal("2000.50")
        assert price.chain == "ethereum"
        assert price.source == "defillama"

    async def test_http_error_skips_batch(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        ))

        service = PriceService(redis=_make_mock_redis(), http_client=mock_client)
        result = await service.get_current_prices([("ethereum", WETH_ADDRESS)])
        assert result == {}


class TestPriceServiceHistoricalPrice:
    """PriceService.get_historical_price with mocked httpx."""

    async def test_successful_historical(self) -> None:
        coin_id = f"ethereum:{WETH_ADDRESS}"
        response_json = {
            "coins": {
                coin_id: {
                    "price": 1197.04,
                    "symbol": "WETH",
                    "timestamp": 1672531240,
                    "confidence": 0.99,
                },
            }
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        service = PriceService(redis=_make_mock_redis(), http_client=mock_client)
        result = await service.get_historical_price(
            "ethereum", WETH_ADDRESS, 1672531200
        )
        assert result is not None
        assert result.price_usd == Decimal("1197.04")

    async def test_missing_coin_returns_none(self) -> None:
        response_json = {"coins": {}}
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = response_json
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        service = PriceService(redis=_make_mock_redis(), http_client=mock_client)
        result = await service.get_historical_price(
            "ethereum", WETH_ADDRESS, 1672531200
        )
        assert result is None


class TestPriceServiceCache:
    """PriceService Redis caching."""

    async def test_cache_prices_sets_ttl(self) -> None:
        mock_redis = _make_mock_redis()
        service = PriceService(redis=mock_redis)

        prices = [
            PriceData(
                asset_address=WETH_ADDRESS,
                chain="ethereum",
                price_usd=Decimal("2000.0"),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                confidence=0.99,
                source="defillama",
            )
        ]
        await service.cache_prices(prices)

        pipe = mock_redis.pipeline.return_value
        pipe.set.assert_called_once()
        call_args = pipe.set.call_args
        assert call_args.kwargs.get("ex") == PRICE_CACHE_TTL_SECONDS

    async def test_get_cached_price_hit(self) -> None:
        mock_redis = _make_mock_redis()
        cached = json.dumps({
            "asset_address": WETH_ADDRESS,
            "chain": "ethereum",
            "price_usd": "2000.0",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "confidence": 0.99,
            "source": "defillama",
        })
        mock_redis.get = AsyncMock(return_value=cached)

        service = PriceService(redis=mock_redis)
        result = await service.get_cached_price("ethereum", WETH_ADDRESS)
        assert result is not None
        assert result.price_usd == Decimal("2000.0")

    async def test_get_cached_price_miss(self) -> None:
        mock_redis = _make_mock_redis()
        service = PriceService(redis=mock_redis)
        result = await service.get_cached_price("ethereum", WETH_ADDRESS)
        assert result is None


class TestPriceServicePubSub:
    """PriceService Redis pub/sub publishing."""

    async def test_publish_sends_to_channel(self) -> None:
        mock_redis = _make_mock_redis()
        service = PriceService(redis=mock_redis)

        prices = [
            PriceData(
                asset_address=WETH_ADDRESS,
                chain="ethereum",
                price_usd=Decimal("2000.0"),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                confidence=0.99,
                source="defillama",
            )
        ]
        await service.publish_price_updates(prices)

        mock_redis.publish.assert_called_once()
        channel = mock_redis.publish.call_args[0][0]
        assert channel == PRICE_PUBSUB_CHANNEL

        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert len(payload["updates"]) == 1
        assert payload["updates"][0]["asset_address"] == WETH_ADDRESS

    async def test_publish_empty_does_nothing(self) -> None:
        mock_redis = _make_mock_redis()
        service = PriceService(redis=mock_redis)
        await service.publish_price_updates([])
        mock_redis.publish.assert_not_called()


class TestPriceServicePersistence:
    """PriceService database persistence."""

    async def test_persist_prices(self, price_db_session: AsyncSession) -> None:
        service = PriceService(redis=_make_mock_redis())

        prices = [
            PriceData(
                asset_address=WETH_ADDRESS,
                chain="ethereum",
                price_usd=Decimal("2000.0"),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                confidence=0.99,
                source="defillama",
            )
        ]

        await service.persist_prices(price_db_session, prices)
        await price_db_session.commit()

        result = await price_db_session.execute(
            text("SELECT price_usd FROM price_history WHERE asset_address = :addr"),
            {"addr": WETH_ADDRESS},
        )
        row = result.fetchone()
        assert row is not None
        assert float(row[0]) == 2000.0

    async def test_persist_upsert_updates_price(
        self, price_db_session: AsyncSession
    ) -> None:
        service = PriceService(redis=_make_mock_redis())
        ts = datetime(2024, 1, 1, tzinfo=UTC)

        original = [
            PriceData(
                asset_address=WETH_ADDRESS,
                chain="ethereum",
                price_usd=Decimal("2000.0"),
                timestamp=ts,
                confidence=0.99,
                source="defillama",
            )
        ]
        await service.persist_prices(price_db_session, original)
        await price_db_session.commit()

        updated = [
            PriceData(
                asset_address=WETH_ADDRESS,
                chain="ethereum",
                price_usd=Decimal("2100.0"),
                timestamp=ts,
                confidence=0.99,
                source="defillama",
            )
        ]
        await service.persist_prices(price_db_session, updated)
        await price_db_session.commit()

        result = await price_db_session.execute(
            text("SELECT price_usd FROM price_history WHERE asset_address = :addr"),
            {"addr": WETH_ADDRESS},
        )
        row = result.fetchone()
        assert row is not None
        assert float(row[0]) == 2100.0


class TestPriceServiceBatchHistorical:
    """PriceService.get_batch_historical_prices with mocked httpx."""

    async def test_groups_by_timestamp(self) -> None:
        """Two timestamps with one coin each => two API calls."""
        ts1, ts2 = 1672531200, 1672617600

        def _make_response(coin_id: str, price: float, ts: int) -> MagicMock:
            resp = MagicMock(spec=httpx.Response)
            resp.json.return_value = {
                "coins": {
                    coin_id: {
                        "price": price,
                        "timestamp": ts,
                        "confidence": 0.99,
                    }
                }
            }
            resp.raise_for_status = MagicMock()
            return resp

        weth_id = f"ethereum:{WETH_ADDRESS}"
        usdc_id = f"ethereum:{USDC_ADDRESS}"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=[
            _make_response(weth_id, 1200.0, ts1),
            _make_response(usdc_id, 1.0, ts2),
        ])

        service = PriceService(
            redis=_make_mock_redis(), http_client=mock_client,
        )
        results = await service.get_batch_historical_prices([
            ("ethereum", WETH_ADDRESS, ts1),
            ("ethereum", USDC_ADDRESS, ts2),
        ])

        assert len(results) == 2
        assert mock_client.get.call_count == 2
        prices_by_addr = {r.asset_address: r for r in results}
        assert prices_by_addr[WETH_ADDRESS].price_usd == Decimal("1200.0")
        assert prices_by_addr[USDC_ADDRESS].price_usd == Decimal("1.0")

    async def test_multiple_coins_same_timestamp(self) -> None:
        """Two coins at the same timestamp => one API call."""
        ts = 1672531200
        resp = MagicMock(spec=httpx.Response)
        resp.json.return_value = {
            "coins": {
                f"ethereum:{WETH_ADDRESS}": {
                    "price": 1200.0,
                    "timestamp": ts,
                    "confidence": 0.99,
                },
                f"ethereum:{USDC_ADDRESS}": {
                    "price": 1.0,
                    "timestamp": ts,
                    "confidence": 0.99,
                },
            }
        }
        resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp)

        service = PriceService(
            redis=_make_mock_redis(), http_client=mock_client,
        )
        results = await service.get_batch_historical_prices([
            ("ethereum", WETH_ADDRESS, ts),
            ("ethereum", USDC_ADDRESS, ts),
        ])

        assert len(results) == 2
        mock_client.get.assert_called_once()

    async def test_empty_requests(self) -> None:
        service = PriceService(redis=_make_mock_redis())
        results = await service.get_batch_historical_prices([])
        assert results == []


class TestPriceServiceContextManager:
    """PriceService lifecycle management."""

    async def test_closes_owned_client(self) -> None:
        mock_redis = _make_mock_redis()
        service = PriceService(redis=mock_redis)
        client = await service._get_http()
        client.aclose = AsyncMock()

        await service.close()
        client.aclose.assert_called_once()

    async def test_does_not_close_external_client(self) -> None:
        mock_redis = _make_mock_redis()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = PriceService(redis=mock_redis, http_client=mock_client)

        await service.close()
        mock_client.aclose.assert_not_called()


class TestPriceServiceApiUsageTracking:
    """PriceService API usage counter."""

    async def test_track_increments_counter(self) -> None:
        mock_redis = _make_mock_redis()
        service = PriceService(redis=mock_redis)

        await service._track_api_call("prices/current")

        pipe = mock_redis.pipeline.return_value
        pipe.incr.assert_called_once()
        pipe.expire.assert_called_once()
        pipe.execute.assert_called_once()
