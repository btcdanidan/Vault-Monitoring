"""Position model — aggregates transaction lots into current holding (§10)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Position(Base):
    """Current holding in a specific protocol/vault/market."""

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    protocol: Mapped[str] = mapped_column(String(30), nullable=False)
    vault_or_market_id: Mapped[str] = mapped_column(String(100), nullable=False)
    position_type: Mapped[str] = mapped_column(String(10), nullable=False)
    asset_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    asset_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_shares_or_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 12), nullable=True
    )
    cost_basis_fifo_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    cost_basis_wac_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    total_yield_earned_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, nullable=False
    )
    total_borrow_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, nullable=False
    )
    unrealised_pnl_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    unrealised_pnl_native: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 8), nullable=True
    )
    realised_pnl_fifo_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, nullable=False
    )
    realised_pnl_wac_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, nullable=False
    )
    health_factor: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconstruction_status: Mapped[str] = mapped_column(
        String(10), default="complete", nullable=False
    )
    adapter_type: Mapped[str] = mapped_column(
        String(15), default="adapter", nullable=False
    )
    manual_protocol_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    pendle_position_type: Mapped[str | None] = mapped_column(String(5), nullable=True)
    pendle_maturity_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    pendle_implied_apy_at_entry: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )
    pendle_yt_claimed_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    pendle_yt_pending_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    pendle_pt_maturity_value_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", onupdate="now()", nullable=False
    )
