"""Auth routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.schemas.admin import AccountDeletionRequest, AccountDeletionResponse
from app.services import account_deletion as account_deletion_service
from app.services import admin as admin_service

router = APIRouter()


@router.get("/me")
async def get_me(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict[str, str]:  # noqa: B008
    """Return current user id for the authenticated request."""
    return {"user_id": str(user_id)}


@router.delete("/account", response_model=AccountDeletionResponse)
async def delete_own_account(
    body: AccountDeletionRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> AccountDeletionResponse:
    """Delete the current user's account. Requires confirm_email to match profile email."""
    profile = await admin_service.get_profile(db, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.email.strip().lower() != body.confirm_email.strip().lower():
        raise HTTPException(
            status_code=400,
            detail="Email confirmation does not match your account email",
        )
    try:
        deleted_ok, auth_cleanup_pending = await account_deletion_service.delete_user_account(db, user_id)
    except ValueError as e:
        if "admin" in str(e).lower():
            raise HTTPException(status_code=403, detail="Admin accounts cannot be deleted via this endpoint") from e
        raise
    return AccountDeletionResponse(
        deleted=deleted_ok,
        user_id=user_id,
        auth_cleanup_pending=auth_cleanup_pending,
    )
