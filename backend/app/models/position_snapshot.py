"""PositionSnapshot model — time-series snapshots for P&L charting (§10, hypertable)."""

import uuid
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class PositionSnapshot(Base):
    """Time-series snapshots of position state for P&L chart and history."""

    __tablename__ = "position_snapshots"

    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("positions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    value_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cost_basis_fifo_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    cost_basis_wac_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    cumulative_yield_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    cumulative_borrow_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    net_pnl_fifo_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    net_pnl_wac_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
