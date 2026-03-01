"""Vault model — reference table for tracked vaults/markets (§10)."""

from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Vault(Base):
    """Reference table for every vault/market the platform tracks."""

    __tablename__ = "vaults"

    vault_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    chain: Mapped[str] = mapped_column(String(20), primary_key=True)
    protocol: Mapped[str] = mapped_column(String(30), nullable=False)
    vault_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    asset_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    asset_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vault_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    deployment_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    curator: Mapped[str | None] = mapped_column(String(100), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", onupdate="now()", nullable=False
    )
