"""Recommendation and RecommendationOutcome models (§10, §13)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Recommendation(Base):
    """Advisory engine output; lifecycle in §13."""

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True
    )
    target_vault_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_chain: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False)
    urgency: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(15), default="active", nullable=False)
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    apy_source_at_creation: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    apy_target_at_creation: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    risk_source_at_creation: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), nullable=True
    )
    risk_target_at_creation: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), nullable=True
    )
    net_benefit_usd_30d: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    gas_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    bridge_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    rationale_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class RecommendationOutcome(Base):
    """Tracks whether executed rebalances improved P&L (§7)."""

    __tablename__ = "recommendation_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recommendations.id", ondelete="CASCADE"),
        nullable=False,
    )
    new_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True
    )
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    actual_tx_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
