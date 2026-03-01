"""Admin models — api_usage_log, cost_service_config, cost_throttle_status (§20)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ApiUsageLog(Base):
    """API usage log for cost monitoring; hypertable partitioned by timestamp."""

    __tablename__ = "api_usage_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)


class CostServiceConfig(Base):
    """Per-service cost/config for admin cost monitoring."""

    __tablename__ = "cost_service_config"

    service_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", onupdate="now()", nullable=False
    )


class CostThrottleStatus(Base):
    """Throttle status per service for admin cost monitoring."""

    __tablename__ = "cost_throttle_status"

    service_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    throttled_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", onupdate="now()", nullable=False
    )
