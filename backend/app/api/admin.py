"""Admin routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Placeholder: health check."""
    return {"status": "ok"}
