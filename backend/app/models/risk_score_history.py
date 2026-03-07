"""RiskScoreHistory model — risk score audit trail (§10, hypertable)."""

from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class RiskScoreHistory(Base):
    """Risk score changes for audit trail and trend display."""

    __tablename__ = "risk_score_history"

    vault_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    chain: Mapped[str] = mapped_column(String(20), primary_key=True)
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    previous_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    new_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    grade_before: Mapped[str | None] = mapped_column(String(1), nullable=True)
    grade_after: Mapped[str | None] = mapped_column(String(1), nullable=True)
    changed_layers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
