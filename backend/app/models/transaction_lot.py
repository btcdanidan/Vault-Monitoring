"""TransactionLot model — immutable deposit/withdraw/borrow/repay events (§10, hypertable)."""

import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class TransactionLot(Base):
    """Immutable record of every deposit, withdrawal, borrow, repay, claim, or transfer."""

    __tablename__ = "transaction_lots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="CASCADE"), nullable=True
    )
    wallet_address: Mapped[str] = mapped_column(String(100), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    protocol: Mapped[str] = mapped_column(String(30), nullable=False)
    vault_or_market_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(15), nullable=False)
    asset_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    asset_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    amount_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    price_per_unit_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 8), nullable=True
    )
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    tx_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lot_status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    remaining_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 12), nullable=True
    )
    source: Mapped[str] = mapped_column(String(15), default="auto", nullable=False)
    original_price_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 8), nullable=True
    )
    user_price_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 8), nullable=True
    )
    price_overridden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pendle_position_type: Mapped[str | None] = mapped_column(String(5), nullable=True)
    pendle_implied_apy_at_entry: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )
    pendle_maturity_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    pendle_accounting_asset: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
