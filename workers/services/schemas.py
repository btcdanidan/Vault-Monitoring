"""Pipeline intermediate data structures for wallet history reconstruction (§12)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


class ReconstructionPhase(str, enum.Enum):
    """Phases of the reconstruction pipeline reported via Redis."""

    SCANNING = "scanning"
    BACKFILLING_PRICES = "backfilling_prices"
    COMPUTING_LOTS = "computing_lots"
    COMPUTING_POSITIONS = "computing_positions"
    COMPLETE = "complete"
    ERROR = "error"


VALID_ACTIONS = frozenset(
    {
        "deposit",
        "withdraw",
        "borrow",
        "repay",
        "claim",
        "transfer_in",
        "transfer_out",
        "swap",
    }
)


@dataclass(slots=True)
class RawEvent:
    """A single on-chain event parsed from HyperSync or Helius data."""

    tx_hash: str
    block_number: int | None
    timestamp: datetime
    chain: str
    protocol: str
    vault_or_market_id: str
    action: str
    wallet_address: str
    asset_address: str
    asset_symbol: str | None
    amount: Decimal
    log_index: int | None = None

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"Invalid action {self.action!r}; expected one of {VALID_ACTIONS}")


@dataclass(slots=True)
class EnrichedEvent:
    """A RawEvent augmented with historical price data."""

    tx_hash: str
    block_number: int | None
    timestamp: datetime
    chain: str
    protocol: str
    vault_or_market_id: str
    action: str
    wallet_address: str
    asset_address: str
    asset_symbol: str | None
    amount: Decimal
    log_index: int | None
    price_per_unit_usd: Decimal | None
    amount_usd: Decimal | None

    @classmethod
    def from_raw(cls, raw: RawEvent, price_usd: Decimal | None) -> EnrichedEvent:
        amount_usd = (raw.amount * price_usd) if price_usd is not None else None
        return cls(
            tx_hash=raw.tx_hash,
            block_number=raw.block_number,
            timestamp=raw.timestamp,
            chain=raw.chain,
            protocol=raw.protocol,
            vault_or_market_id=raw.vault_or_market_id,
            action=raw.action,
            wallet_address=raw.wallet_address,
            asset_address=raw.asset_address,
            asset_symbol=raw.asset_symbol,
            amount=raw.amount,
            log_index=raw.log_index,
            price_per_unit_usd=price_usd,
            amount_usd=amount_usd,
        )


@dataclass(slots=True)
class ProgressStatus:
    """Current reconstruction progress stored in Redis."""

    phase: ReconstructionPhase
    progress_pct: int = 0
    events_found: int = 0
    transactions_found: int = 0
    error_message: str | None = None
    last_updated: datetime | None = field(default=None)
