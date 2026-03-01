"""Wallet CRUD routes (§19.8, §18.7)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.schemas.wallet import (
    WalletCreate,
    WalletListResponse,
    WalletResponse,
    WalletUpdate,
)
from app.services import wallet as wallet_service

router = APIRouter()


@router.get("/", response_model=WalletListResponse)
async def list_wallets(
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> WalletListResponse:
    """List active wallets for the current user."""
    wallets, total = await wallet_service.list_wallets(db, user_id)
    return WalletListResponse(
        wallets=[WalletResponse.model_validate(w) for w in wallets],
        total=total,
    )


@router.post("/", response_model=WalletResponse, status_code=201)
async def create_wallet(
    body: WalletCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> WalletResponse:
    """Add a new wallet. Triggers history reconstruction."""
    wallet = await wallet_service.create_wallet(db, user_id, body)
    return WalletResponse.model_validate(wallet)


@router.get("/{wallet_id}", response_model=WalletResponse)
async def get_wallet(
    wallet_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> WalletResponse:
    """Get a single wallet by ID."""
    wallet = await wallet_service.get_wallet(db, user_id, wallet_id)
    return WalletResponse.model_validate(wallet)


@router.patch("/{wallet_id}", response_model=WalletResponse)
async def update_wallet(
    wallet_id: uuid.UUID,
    body: WalletUpdate,
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> WalletResponse:
    """Update wallet label or active status."""
    wallet = await wallet_service.update_wallet(db, user_id, wallet_id, body)
    return WalletResponse.model_validate(wallet)


@router.delete("/{wallet_id}", response_model=WalletResponse)
async def delete_wallet(
    wallet_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> WalletResponse:
    """Soft-delete a wallet (sets is_active=False, data retained)."""
    wallet = await wallet_service.soft_delete_wallet(db, user_id, wallet_id)
    return WalletResponse.model_validate(wallet)
