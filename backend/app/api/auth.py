"""Auth routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/me")
async def get_current_user() -> dict:
    """Placeholder: current user from JWT."""
    return {"message": "auth stub"}
