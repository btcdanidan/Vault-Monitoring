"""Abstract adapter interface for protocol integrations."""

from abc import ABC, abstractmethod


class ProtocolAdapter(ABC):
    """Abstract base for protocol adapters (Morpho, Aave, Pendle, Euler, etc.)."""

    @abstractmethod
    async def fetch_live_metrics(
        self, vault_addresses: list[str]
    ) -> list[dict]:  # list[VaultMetrics]
        """Fetch live metrics for the given vault addresses."""
        ...

    @abstractmethod
    async def fetch_positions(self, wallet: str) -> list[dict]:  # list[RawPosition]
        """Fetch positions for the given wallet."""
        ...

    @abstractmethod
    async def fetch_historical_events(
        self, wallet: str, from_block: int, to_block: int
    ) -> list[dict]:  # list[RawEvent]
        """Fetch historical events for the wallet in the block range."""
        ...
