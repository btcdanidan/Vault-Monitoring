"""Auth routes."""

import uuid

from fastapi import APIRouter, Depends

from app.dependencies import get_current_user_id

router = APIRouter()


@router.get("/me")
async def get_me(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict[str, str]:  # noqa: B008
    """Return current user id for the authenticated request."""
    return {"user_id": str(user_id)}
