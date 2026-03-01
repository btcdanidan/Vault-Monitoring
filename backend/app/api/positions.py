"""Position routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_positions() -> list:
    """Placeholder: list positions for current user."""
    return []
