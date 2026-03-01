"""Vault routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_vaults() -> list:
    """Placeholder: list vaults."""
    return []
