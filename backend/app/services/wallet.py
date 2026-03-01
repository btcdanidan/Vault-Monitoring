"""Wallet management service (§19.8, §18.7)."""

import re
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.wallet import Wallet
from app.schemas.wallet import WalletCreate, WalletUpdate

logger = structlog.get_logger()

MAX_WALLETS_PER_USER = 20
VALID_CHAINS = {"ethereum", "base", "solana"}
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOLANA_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _keccak256(data: bytes) -> bytes:
    """Compute Keccak-256 hash (Ethereum-flavour, NOT FIPS-202 SHA3-256)."""
    from Crypto.Hash import keccak  # type: ignore[import-untyped]

    return keccak.new(data=data, digest_bits=256).digest()  # type: ignore[no-any-return]


def _to_checksum_address(address: str) -> str:
    """Convert a hex address to EIP-55 checksummed form."""
    addr_lower = address[2:].lower()
    hash_hex = _keccak256(addr_lower.encode("ascii")).hex()
    checksummed = "0x"
    for i, char in enumerate(addr_lower):
        if char in "0123456789":
            checksummed += char
        elif int(hash_hex[i], 16) >= 8:
            checksummed += char.upper()
        else:
            checksummed += char.lower()
    return checksummed


def validate_evm_address(address: str) -> str:
    """Validate and return EIP-55 checksummed EVM address.

    Accepts:
      - All-lowercase hex (0x + 40 lowercase hex): normalised to checksum form
      - Correctly checksummed mixed-case: returned as-is
    Rejects:
      - Invalid length or non-hex characters
      - Mixed-case that does NOT match the EIP-55 checksum
    """
    if not _EVM_ADDRESS_RE.match(address):
        raise BadRequestException(
            detail="Invalid EVM address: must be 0x-prefixed 40-character hex"
        )
    checksummed = _to_checksum_address(address)
    is_all_lower = address == address[:2] + address[2:].lower()
    is_all_upper = address == address[:2] + address[2:].upper()
    if not is_all_lower and not is_all_upper and address != checksummed:
        raise BadRequestException(
            detail="Invalid EVM address: checksum mismatch (EIP-55)"
        )
    return checksummed


def validate_solana_address(address: str) -> str:
    """Validate a Solana base58 address."""
    if not _SOLANA_BASE58_RE.match(address):
        raise BadRequestException(
            detail="Invalid Solana address: must be 32-44 character base58"
        )
    return address


def detect_chain(address: str) -> str:
    """Auto-detect chain from address format."""
    if address.startswith("0x") and len(address) == 42:
        return "ethereum"
    if _SOLANA_BASE58_RE.match(address):
        return "solana"
    raise BadRequestException(detail="Cannot auto-detect chain from address format")


def validate_address(address: str, chain: str) -> str:
    """Validate address for the given chain, return normalised form."""
    if chain in ("ethereum", "base"):
        return validate_evm_address(address)
    if chain == "solana":
        return validate_solana_address(address)
    raise BadRequestException(detail=f"Unsupported chain: {chain}")


async def list_wallets(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> tuple[list[Wallet], int]:
    """Return all wallets for a user (active ones) and total count."""
    stmt = (
        select(Wallet)
        .where(Wallet.user_id == user_id, Wallet.is_active.is_(True))
        .order_by(Wallet.created_at.desc())
    )
    result = await db.execute(stmt)
    wallets = list(result.scalars().all())

    count_stmt = select(func.count()).select_from(Wallet).where(
        Wallet.user_id == user_id, Wallet.is_active.is_(True)
    )
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()
    return wallets, total


async def get_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
) -> Wallet:
    """Fetch a single wallet belonging to the user."""
    stmt = select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user_id)
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()
    if wallet is None:
        raise NotFoundException(detail="Wallet not found")
    return wallet


async def create_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: WalletCreate,
) -> Wallet:
    """Create a new wallet with validation.

    - Validates address format per chain
    - Auto-detects chain if not provided
    - Enforces max 20 wallets per user
    - Reactivates soft-deleted duplicate instead of erroring
    - Triggers reconstruct_wallet_history Celery task on success
    """
    chain = data.chain
    if chain is not None and chain not in VALID_CHAINS:
        valid = ", ".join(sorted(VALID_CHAINS))
        raise BadRequestException(detail=f"Invalid chain '{chain}'. Must be one of: {valid}")

    if chain is None:
        chain = detect_chain(data.address)

    address = validate_address(data.address, chain)

    # Check for soft-deleted duplicate: reactivate instead of rejecting
    existing_stmt = select(Wallet).where(
        Wallet.user_id == user_id,
        Wallet.address == address,
        Wallet.chain == chain,
    )
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        if existing.is_active:
            raise ConflictException(detail="Wallet with this address and chain already exists")
        existing.is_active = True
        existing.label = data.label or existing.label
        existing.sync_status = "pending"
        existing.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(existing)
        _trigger_reconstruction(str(existing.id), str(user_id))
        logger.info("wallet_reactivated", wallet_id=str(existing.id), user_id=str(user_id))
        return existing

    # Enforce max wallets limit (count all, including inactive)
    count_stmt = select(func.count()).select_from(Wallet).where(Wallet.user_id == user_id)
    count_result = await db.execute(count_stmt)
    wallet_count = count_result.scalar_one()
    if wallet_count >= MAX_WALLETS_PER_USER:
        raise BadRequestException(detail=f"Maximum {MAX_WALLETS_PER_USER} wallets per user")

    now = datetime.now(UTC)
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=user_id,
        address=address,
        chain=chain,
        label=data.label,
        is_active=True,
        sync_status="pending",
        created_at=now,
        updated_at=now,
    )
    try:
        db.add(wallet)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ConflictException(
            detail="Wallet with this address and chain already exists"
        ) from None

    await db.refresh(wallet)
    _trigger_reconstruction(str(wallet.id), str(user_id))
    logger.info("wallet_created", wallet_id=str(wallet.id), user_id=str(user_id), chain=chain)
    return wallet


async def update_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
    data: WalletUpdate,
) -> Wallet:
    """Update wallet label and/or is_active flag."""
    wallet = await get_wallet(db, user_id, wallet_id)
    if data.label is not None:
        wallet.label = data.label
    if data.is_active is not None:
        wallet.is_active = data.is_active
    wallet.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(wallet)
    return wallet


async def soft_delete_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
) -> Wallet:
    """Soft-delete a wallet by setting is_active=False (§18.7)."""
    wallet = await get_wallet(db, user_id, wallet_id)
    wallet.is_active = False
    wallet.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(wallet)
    logger.info("wallet_soft_deleted", wallet_id=str(wallet_id), user_id=str(user_id))
    return wallet


def _trigger_reconstruction(wallet_id: str, user_id: str) -> None:
    """Fire-and-forget: dispatch reconstruct_wallet_history Celery task.

    Uses send_task to decouple from workers import — the task must be registered
    in the Celery worker with the matching name.
    """
    try:
        from celery import current_app  # type: ignore[import-untyped]

        current_app.send_task(
            "workers.tasks.reconstruction.reconstruct_wallet_history",
            args=[wallet_id, user_id],
        )
    except Exception:
        logger.warning(
            "celery_dispatch_failed",
            wallet_id=wallet_id,
            user_id=user_id,
            exc_info=True,
        )
