"""Morpho Blue / MetaMorpho protocol adapter (§9).

Live state: Morpho GraphQL API (zero RPC cost).
Historical events: HyperSync — Deposit, Withdraw, Supply, Borrow, Repay.
"""

from __future__ import annotations

from app.adapters.base import BaseProtocolAdapter
from app.adapters.registry import register_adapter
from app.schemas.adapter import RawEvent, RawPosition, VaultMetricsData


@register_adapter
class MorphoAdapter(BaseProtocolAdapter):
    """Morpho Blue / MetaMorpho adapter.

    Protocol logic will be implemented in a subsequent sprint; the abstract
    methods currently return empty lists.
    """

    @property
    def protocol_name(self) -> str:
        return "morpho"

    @property
    def supported_chains(self) -> list[str]:
        return ["ethereum", "base"]

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
