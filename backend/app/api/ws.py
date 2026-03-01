"""WebSocket routes."""

from fastapi import APIRouter

router = APIRouter()


@router.websocket("/")
async def websocket_endpoint() -> None:
    """Placeholder: WebSocket connection."""
    pass
