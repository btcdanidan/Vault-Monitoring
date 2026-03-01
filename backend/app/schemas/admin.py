"""Admin account management schemas (§19.3.1)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProfileListItem(BaseModel):
    """Single profile row for admin account list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    approved: bool
    rejected: bool
    is_admin: bool
    created_at: datetime
    approved_at: datetime | None


class AccountListResponse(BaseModel):
    """Paginated list of accounts for admin UI."""

    accounts: list[ProfileListItem]
    total: int


class AccountActionResponse(BaseModel):
    """Response after approve/reject action."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    approved: bool
    rejected: bool
    approved_at: datetime | None
    approved_by: uuid.UUID | None


class AccountDeletionRequest(BaseModel):
    """Request body for self-deletion; confirm_email must match profile email."""

    confirm_email: str


class AccountDeletionResponse(BaseModel):
    """Response after account deletion (§19.9)."""

    deleted: bool
    user_id: uuid.UUID
    auth_cleanup_pending: bool
