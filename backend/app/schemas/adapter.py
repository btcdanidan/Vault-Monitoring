"""Pydantic schemas for protocol adapter return types (§9, §10)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class VaultMetricsData(BaseModel):
    """Intermediate representation of a vault_metrics row returned by adapters."""

    vault_id: str
    chain: str
    protocol: str
    vault_name: str | None = None
    asset_symbol: str | None = None
    asset_address: str | None = None
    timestamp: datetime

    apy_gross: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)
    apy_base: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)
    apy_reward: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)
    performance_fee_pct: Decimal | None = Field(default=None, max_digits=5, decimal_places=2)
    mgmt_fee_pct: Decimal | None = Field(default=None, max_digits=6, decimal_places=4)
    net_apy: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)

    tvl_usd: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    tvl_native: Decimal | None = Field(default=None, max_digits=24, decimal_places=8)

    utilisation_rate: Decimal | None = Field(default=None, max_digits=5, decimal_places=2)
    supply_rate: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)
    borrow_rate: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)

    redemption_type: (
        Literal["instant", "variable", "at_maturity"] | str | None
    ) = None
    redemption_days_est: int | None = None
    maturity_date: date | None = None


PositionType = Literal["supply", "borrow", "stake", "lp"]


class RawPosition(BaseModel):
    """Intermediate representation of a position returned by adapters.

    Adapters produce these; the P&L engine maps them to ``positions`` rows.
    """

    wallet_address: str
    chain: str
    protocol: str
    vault_or_market_id: str
    position_type: PositionType
    asset_symbol: str | None = None
    asset_address: str | None = None
    current_shares_or_amount: Decimal = Field(max_digits=30, decimal_places=12)
    health_factor: Decimal | None = Field(default=None, max_digits=8, decimal_places=4)


ActionType = Literal[
    "deposit",
    "withdraw",
    "borrow",
    "repay",
    "claim",
    "transfer_in",
    "transfer_out",
    "swap",
]


class RawEvent(BaseModel):
    """Intermediate representation of an on-chain event returned by adapters.

    Adapters produce these; the reconstruction pipeline maps them to
    ``transaction_lots`` rows.
    """

    wallet_address: str
    chain: str
    protocol: str
    vault_or_market_id: str
    action: ActionType
    asset_symbol: str | None = None
    asset_address: str | None = None
    amount: Decimal = Field(max_digits=30, decimal_places=12)
    timestamp: datetime
    tx_hash: str | None = None
    block_number: int | None = None


VaultType = Literal["lending", "vault", "staking", "lp", "leveraged"]


class DiscoveredVault(BaseModel):
    """Vault metadata discovered by an adapter for upserting into ``vaults``."""

    vault_id: str
    chain: str
    protocol: str
    vault_name: str | None = None
    contract_address: str | None = None
    asset_symbol: str | None = None
    asset_address: str | None = None
    vault_type: VaultType | None = None
    curator: str | None = None
