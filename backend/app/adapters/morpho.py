"""Morpho protocol adapter."""

from app.adapters.base import ProtocolAdapter


class MorphoAdapter(ProtocolAdapter):
    """Morpho Blue / MetaMorpho adapter. Stub."""

    async def fetch_live_metrics(
        self, vault_addresses: list[str]
    ) -> list[dict]:
        return []

    async def fetch_positions(self, wallet: str) -> list[dict]:
        return []

    async def fetch_historical_events(
        self, wallet: str, from_block: int, to_block: int
    ) -> list[dict]:
        return []
