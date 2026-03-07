"""Pendle protocol adapter (§5.A, §9).

Live state: Pendle REST API — markets, PT/YT prices, implied yields.
Historical events: HyperSync — Mint, Burn, Transfer on PT/YT/LP tokens.
"""

from __future__ import annotations

from app.adapters.base import BaseProtocolAdapter
from app.adapters.registry import register_adapter
from app.schemas.adapter import RawEvent, RawPosition, VaultMetricsData


@register_adapter
class PendleAdapter(BaseProtocolAdapter):
    """Pendle adapter.

    Protocol logic will be implemented in Sprint 10; the abstract methods
    currently return empty lists.
    """

    @property
    def protocol_name(self) -> str:
        return "pendle"

    @property
    def supported_chains(self) -> list[str]:
        return ["ethereum"]

    async def fetch_live_metrics(
        self,
        vault_addresses: list[str],
        chain: str,
    ) -> list[VaultMetricsData]:
        return []

    async def fetch_positions(
        self,
        wallet: str,
        chain: str,
    ) -> list[RawPosition]:
        return []

    async def fetch_historical_events(
        self,
        wallet: str,
        chain: str,
        from_block: int,
        to_block: int,
    ) -> list[RawEvent]:
        return []
