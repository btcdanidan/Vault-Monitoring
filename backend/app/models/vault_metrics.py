"""VaultMetrics model — time-series metrics per vault (§10, hypertable)."""

from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class VaultMetrics(Base):
    """Time-series metrics for every tracked vault; partitioned by timestamp."""

    __tablename__ = "vault_metrics"

    vault_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    chain: Mapped[str] = mapped_column(String(20), primary_key=True)
    protocol: Mapped[str] = mapped_column(String(30), nullable=False)
    vault_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    asset_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    apy_gross: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    apy_base: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    apy_reward: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    performance_fee_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    mgmt_fee_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    net_apy: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    tvl_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tvl_native: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    utilisation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    supply_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    borrow_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    redemption_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    redemption_days_est: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maturity_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
