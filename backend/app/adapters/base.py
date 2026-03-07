"""Abstract adapter interface and concrete base class for protocol integrations.

``ProtocolAdapter`` defines the interface every protocol adapter must implement.
``BaseProtocolAdapter`` adds shared infrastructure: retry, rate-limit tracking,
vault-metrics persistence, and vault discovery.

References: §9 (Data Sources), §10 (System Architecture).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import structlog
from sqlalchemy import text

from app.schemas.adapter import (
    DiscoveredVault,
    RawEvent,
    RawPosition,
    VaultMetricsData,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import TypeVar

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    T = TypeVar("T")


logger = structlog.get_logger(__name__)

RATE_LIMIT_TTL_SECONDS = 172_800  # 48 h, matches PriceService pattern
MIN_TVL_FOR_TRACKING_USD = 100_000  # §10: vaults with TVL > $100K are auto-tracked


# ---------------------------------------------------------------------------
# Abstract interface — every protocol adapter must implement these
# ---------------------------------------------------------------------------


class ProtocolAdapter(ABC):
    """Abstract interface for protocol adapters (Morpho, Aave, Pendle, …)."""

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """Canonical protocol identifier (e.g. ``'morpho'``, ``'aave_v3'``)."""
        ...

    @property
    @abstractmethod
    def supported_chains(self) -> list[str]:
        """Chains this adapter can operate on (e.g. ``['ethereum', 'base']``)."""
        ...

    @abstractmethod
    async def fetch_live_metrics(
        self,
        vault_addresses: list[str],
        chain: str,
    ) -> list[VaultMetricsData]:
        """Fetch live metrics for the given vault addresses on *chain*."""
        ...

    @abstractmethod
    async def fetch_positions(
        self,
        wallet: str,
        chain: str,
    ) -> list[RawPosition]:
        """Fetch current positions for *wallet* on *chain*."""
        ...

    @abstractmethod
    async def fetch_historical_events(
        self,
        wallet: str,
        chain: str,
        from_block: int,
        to_block: int,
    ) -> list[RawEvent]:
        """Fetch historical events for *wallet* on *chain* in ``[from_block, to_block)``."""
        ...


# ---------------------------------------------------------------------------
# Concrete base class — shared infrastructure for all adapters
# ---------------------------------------------------------------------------


class BaseProtocolAdapter(ProtocolAdapter):
    """Concrete base providing retry, rate-limit tracking, DB writes, and vault discovery.

    Subclasses must still implement the three abstract ``fetch_*`` methods and
    the ``protocol_name`` / ``supported_chains`` properties.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client
        self._owns_http = http_client is None
        self._log = structlog.get_logger(__name__).bind(protocol=self.protocol_name)

    # -- HTTP client lifecycle ------------------------------------------------

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close the internal HTTP client if we own it."""
        if self._owns_http and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> BaseProtocolAdapter:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # -- Retry with exponential backoff ---------------------------------------

    async def _retry_with_backoff(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> T:
        """Execute *fn* with exponential backoff on transient errors.

        Retries on ``httpx.HTTPError``, ``ConnectionError``, ``TimeoutError``,
        and ``OSError``.  All other exceptions propagate immediately.
        """
        retryable = (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return await fn()
            except retryable as exc:
                last_exc = exc
                if attempt == max_retries:
                    break
                delay = base_delay * (2**attempt)
                self._log.warning(
                    "adapter_retry",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)

        self._log.error(
            "adapter_retries_exhausted",
            max_retries=max_retries,
            error=str(last_exc),
        )
        raise last_exc  # type: ignore[misc]

    # -- Rate-limit tracking (Redis) ------------------------------------------

    async def _track_rate_limit(
        self,
        redis: Redis,  # type: ignore[type-arg]
        endpoint: str,
    ) -> None:
        """Increment daily API-usage counter in Redis for ``§20`` cost monitoring."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        key = f"api_usage:{self.protocol_name}:{endpoint}:{today}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, RATE_LIMIT_TTL_SECONDS)
        await pipe.execute()
        self._log.debug("rate_limit_tracked", endpoint=endpoint, key=key)

    # -- Vault-metrics persistence --------------------------------------------

    async def write_vault_metrics(
        self,
        db: AsyncSession,
        metrics: list[VaultMetricsData],
    ) -> None:
        """Bulk upsert metrics into the ``vault_metrics`` hypertable."""
        if not metrics:
            return

        stmt = text("""
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
        await db.execute(stmt, params)
        self._log.info("vault_metrics_written", count=len(metrics))

    # -- Vault discovery ------------------------------------------------------

    async def discover_vaults(
        self,
        db: AsyncSession,
        vaults: list[DiscoveredVault],
        *,
        tvl_by_vault: dict[str, float] | None = None,
    ) -> None:
        """Upsert discovered vaults into the ``vaults`` reference table.

        *tvl_by_vault* maps ``vault_id`` to current TVL in USD.  If a vault's
        TVL exceeds ``MIN_TVL_FOR_TRACKING_USD`` it is marked ``is_tracked=true``;
        otherwise it inherits the existing ``is_tracked`` value (defaulting to
        ``true`` for brand-new vaults).
        """
        if not vaults:
            return

        tvl_map = tvl_by_vault or {}

        stmt = text("""
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

        params = [
            {
                "vault_id": v.vault_id,
                "chain": v.chain,
                "protocol": v.protocol,
                "vault_name": v.vault_name,
                "contract_address": v.contract_address,
                "asset_symbol": v.asset_symbol,
                "asset_address": v.asset_address,
                "vault_type": v.vault_type,
                "curator": v.curator,
                "is_tracked": tvl_map.get(v.vault_id, 0) >= MIN_TVL_FOR_TRACKING_USD
                if v.vault_id in tvl_map
                else True,
            }
            for v in vaults
        ]
        await db.execute(stmt, params)
        self._log.info("vaults_discovered", count=len(vaults))

    # -- Orchestration: fetch → write → discover ------------------------------

    async def fetch_and_store_metrics(
        self,
        db: AsyncSession,
        redis: Redis,  # type: ignore[type-arg]
        vault_addresses: list[str],
        chain: str,
    ) -> list[VaultMetricsData]:
        """High-level orchestration: fetch live metrics, persist, and discover vaults.

        Returns the fetched metrics for downstream consumers.
        """
        metrics = await self._retry_with_backoff(
            lambda: self.fetch_live_metrics(vault_addresses, chain),
        )
        await self._track_rate_limit(redis, "fetch_live_metrics")

        await self.write_vault_metrics(db, metrics)

        tvl_map = {
            m.vault_id: float(m.tvl_usd) for m in metrics if m.tvl_usd is not None
        }
        discovered = [
            DiscoveredVault(
                vault_id=m.vault_id,
                chain=m.chain,
                protocol=m.protocol,
                vault_name=m.vault_name,
                asset_symbol=m.asset_symbol,
                asset_address=m.asset_address,
            )
            for m in metrics
        ]
        await self.discover_vaults(db, discovered, tvl_by_vault=tvl_map)

        return metrics
