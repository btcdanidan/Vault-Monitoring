"""Pydantic schemas for price feed data (§9, §10)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PriceData(BaseModel):
    """Internal representation of a token price from DeFiLlama."""

    asset_address: str
    chain: str
    price_usd: Decimal = Field(max_digits=24, decimal_places=8)
    timestamp: datetime
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source: str = "defillama"


class PriceResponse(BaseModel):
    """Single price response for API consumers."""

    asset_address: str
    chain: str
    price_usd: Decimal
    timestamp: datetime
    confidence: float
    source: str


class PriceBatchResponse(BaseModel):
    """Batch price response for API consumers."""

    prices: list[PriceResponse]
    fetched_at: datetime


class PriceUpdate(BaseModel):
    """Redis pub/sub message format for real-time price updates."""

    updates: list[PriceResponse]
    published_at: datetime
