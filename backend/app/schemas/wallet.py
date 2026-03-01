"""Wallet request/response schemas (§19.8, §18.7)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WalletCreate(BaseModel):
    """Request body for creating a wallet."""

    address: str = Field(..., min_length=1, max_length=100)
    chain: str | None = Field(
        default=None,
        description="'ethereum' | 'base' | 'solana'. Auto-detected from address if omitted.",
    )
    label: str | None = Field(default=None, max_length=50)


class WalletUpdate(BaseModel):
    """Request body for updating a wallet."""

    label: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None


class WalletResponse(BaseModel):
    """Single wallet in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    address: str
    chain: str
    label: str | None
    is_active: bool
    sync_status: str
    created_at: datetime
    updated_at: datetime


class WalletListResponse(BaseModel):
    """Paginated wallet list."""

    wallets: list[WalletResponse]
    total: int
