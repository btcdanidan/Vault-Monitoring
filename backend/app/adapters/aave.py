"""Aave protocol adapter."""

from app.adapters.base import ProtocolAdapter


class AaveAdapter(ProtocolAdapter):
    """Aave adapter. Stub."""

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
