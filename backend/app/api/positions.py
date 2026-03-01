"""Position routes."""

import uuid

from fastapi import APIRouter, Depends

from app.dependencies import get_current_user_id

router = APIRouter()


@router.get("/")
async def list_positions(
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
) -> list[object]:
    """List positions for current user."""
    return []
