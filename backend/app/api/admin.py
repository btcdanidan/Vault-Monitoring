"""Admin routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin_id
from app.schemas.admin import (
    AccountActionResponse,
    AccountDeletionResponse,
    AccountListResponse,
    ProfileListItem,
)
from app.services import account_deletion as account_deletion_service
from app.services import admin as admin_service
from app.services import notifications as notifications_service

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Placeholder: health check."""
    return {"status": "ok"}


@router.get("/accounts", response_model=AccountListResponse)
async def list_accounts(
    status: str = "all",
    _admin_id: uuid.UUID = Depends(get_current_admin_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AccountListResponse:
    """List all accounts; filter by status: all, pending, approved, rejected."""
    if status not in ("all", "pending", "approved", "rejected"):
        status = "all"
    profiles = await admin_service.list_accounts(db, status_filter=status)
    total = await admin_service.count_accounts(db, status_filter=status)
    return AccountListResponse(
        accounts=[ProfileListItem.model_validate(p) for p in profiles],
        total=total,
    )


@router.post("/accounts/{user_id}/approve", response_model=AccountActionResponse)
async def approve_account(
    user_id: uuid.UUID,
    admin_id: uuid.UUID = Depends(get_current_admin_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AccountActionResponse:
    """Approve a pending account; sends approval email to user."""
    try:
        profile = await admin_service.approve_account(db, user_id, admin_id)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Profile not found") from e
        raise
    await notifications_service.send_approval_email(profile.email)
    return AccountActionResponse.model_validate(profile)


@router.post("/accounts/{user_id}/reject", response_model=AccountActionResponse)
async def reject_account(
    user_id: uuid.UUID,
    _admin_id: uuid.UUID = Depends(get_current_admin_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AccountActionResponse:
    """Reject a pending account; sends rejection email. Cannot reject an admin."""
    try:
        profile = await admin_service.reject_account(db, user_id)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Profile not found") from e
        if "admin" in str(e).lower():
            raise HTTPException(status_code=403, detail="Cannot reject an admin") from e
        raise
    await notifications_service.send_rejection_email(profile.email)
    return AccountActionResponse.model_validate(profile)


@router.delete("/accounts/{user_id}", response_model=AccountDeletionResponse)
async def delete_account(
    user_id: uuid.UUID,
    admin_id: uuid.UUID = Depends(get_current_admin_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AccountDeletionResponse:
    """Delete a user account (cascade) and Supabase auth user. Cannot delete self or another admin."""
    if admin_id == user_id:
        raise HTTPException(status_code=403, detail="Cannot delete your own admin account")
    try:
        deleted_ok, auth_cleanup_pending = await account_deletion_service.delete_user_account(db, user_id)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Profile not found") from e
        if "admin" in str(e).lower():
            raise HTTPException(status_code=403, detail="Cannot delete an admin account") from e
        raise
    return AccountDeletionResponse(
        deleted=deleted_ok,
        user_id=user_id,
        auth_cleanup_pending=auth_cleanup_pending,
    )
