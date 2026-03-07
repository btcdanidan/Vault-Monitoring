"""VaultConcentration model — whale concentration per vault (§10, hypertable)."""

from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class VaultConcentration(Base):
    """Whale concentration data per vault; partitioned by timestamp."""

    __tablename__ = "vault_concentration"

    vault_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    chain: Mapped[str] = mapped_column(String(20), primary_key=True)
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    top_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top_n_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    top_holders: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_holders: Mapped[int | None] = mapped_column(Integer, nullable=True)
