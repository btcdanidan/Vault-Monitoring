"""PriceHistory model — historical token prices (§10, hypertable)."""

from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class PriceHistory(Base):
    """Historical token prices for cost basis and charts."""

    __tablename__ = "price_history"

    asset_address: Mapped[str] = mapped_column(String(100), primary_key=True)
    chain: Mapped[str] = mapped_column(String(20), primary_key=True)
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
