"""Wallet routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_wallets() -> list:
    """Placeholder: list wallets for current user."""
    return []
