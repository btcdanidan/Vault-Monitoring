"""Helius API client for Solana historical data and token account queries.

Provides async wrappers around Helius JSON-RPC endpoints:
- getSignaturesForAddress — transaction history for a wallet
- getTransaction — full transaction details with instruction data
- getAssetsByOwner (DAS API) — token accounts and NFTs for a wallet
- Credit usage tracking via Redis

References: §4 (Tech Stack), §9 (Data Sources — Kamino, Jupiter, Jito).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HELIUS_FREE_TIER_CREDITS = 1_000_000
HELIUS_CREDIT_USAGE_KEY_PREFIX = "api_usage:helius"
HELIUS_CREDIT_USAGE_TTL_SECONDS = 172_800  # 48h (matches DeFiLlama pattern)

SIGNATURES_MAX_LIMIT = 1_000
ASSETS_MAX_LIMIT = 1_000
DEFAULT_REQUEST_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HeliusApiError(Exception):
    """Raised when a Helius API call fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rpc_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rpc_code = rpc_code


class HeliusRateLimitError(HeliusApiError):
    """Raised on HTTP 429 — rate limit exceeded."""


class HeliusUnavailableError(HeliusApiError):
    """Raised on HTTP 503 — service temporarily unavailable."""


# ---------------------------------------------------------------------------
# Display options for DAS API
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetDisplayOptions:
    """Options controlling which fields getAssetsByOwner returns."""

    show_fungible: bool = True
    show_native_balance: bool = False
    show_zero_balance: bool = False
    show_collection_metadata: bool = False
    show_unverified_collections: bool = False
    show_grand_total: bool = False
    show_inscription: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "showFungible": self.show_fungible,
            "showNativeBalance": self.show_native_balance,
            "showZeroBalance": self.show_zero_balance,
            "showCollectionMetadata": self.show_collection_metadata,
            "showUnverifiedCollections": self.show_unverified_collections,
            "showGrandTotal": self.show_grand_total,
            "showInscription": self.show_inscription,
        }


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignatureInfo:
    """Single entry from getSignaturesForAddress result."""

    signature: str
    slot: int
    block_time: int | None
    err: dict[str, Any] | None
    memo: str | None
    confirmation_status: str | None


@dataclass(slots=True)
class SignaturesResult:
    """Paginated result from get_signatures_for_address."""

    signatures: list[SignatureInfo] = field(default_factory=list)
    has_more: bool = False


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def get_helius_rpc_url() -> str:
    """Build Helius RPC URL with API key from environment.

    Raises:
        ValueError: If ``HELIUS_API_KEY`` is not configured.
    """
    api_key = os.getenv("HELIUS_API_KEY", "")
    if not api_key:
        raise ValueError("HELIUS_API_KEY is not configured")
    base_url = os.getenv("HELIUS_BASE_URL", "https://mainnet.helius-rpc.com")
    return f"{base_url}/?api-key={api_key}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _next_rpc_id() -> str:
    """Simple monotonic request ID. Not thread-safe, but sufficient for tracing."""
    _next_rpc_id._counter = getattr(_next_rpc_id, "_counter", 0) + 1  # type: ignore[attr-defined]
    return str(_next_rpc_id._counter)  # type: ignore[attr-defined]


def _build_jsonrpc_body(method: str, params: list[Any] | dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": _next_rpc_id(),
        "method": method,
        "params": params,
    }


def _raise_for_status(response: httpx.Response) -> None:
    """Translate HTTP errors into typed exceptions."""
    if response.status_code == 429:
        raise HeliusRateLimitError(
            "Helius rate limit exceeded",
            status_code=429,
        )
    if response.status_code == 503:
        raise HeliusUnavailableError(
            "Helius service unavailable",
            status_code=503,
        )
    if response.status_code >= 400:
        raise HeliusApiError(
            f"Helius HTTP {response.status_code}: {response.text[:200]}",
            status_code=response.status_code,
        )


def _extract_result(data: dict[str, Any], method: str) -> Any:
    """Extract ``result`` from JSON-RPC response, raising on RPC-level errors."""
    if "error" in data:
        err = data["error"]
        code = err.get("code", -1)
        message = err.get("message", "Unknown RPC error")
        raise HeliusApiError(
            f"Helius RPC error in {method}: [{code}] {message}",
            rpc_code=code,
        )
    return data.get("result")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_signatures_for_address(
    address: str,
    *,
    limit: int = SIGNATURES_MAX_LIMIT,
    before: str | None = None,
    until: str | None = None,
    commitment: str = "finalized",
) -> SignaturesResult:
    """Fetch confirmed transaction signatures for a Solana address.

    Args:
        address: Base-58 encoded public key.
        limit: Max signatures per page (1–1000).
        before: Signature to paginate backwards from (exclusive).
        until: Signature to stop at (exclusive).
        commitment: ``"finalized"`` or ``"confirmed"``.

    Returns:
        ``SignaturesResult`` with parsed signature list and pagination hint.
    """
    url = get_helius_rpc_url()
    limit = min(max(limit, 1), SIGNATURES_MAX_LIMIT)

    options: dict[str, Any] = {"limit": limit, "commitment": commitment}
    if before is not None:
        options["before"] = before
    if until is not None:
        options["until"] = until

    body = _build_jsonrpc_body("getSignaturesForAddress", [address, options])

    logger.debug("helius_get_signatures", address=address, limit=limit, before=before)

    async with httpx.AsyncClient(timeout=DEFAULT_REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=body)

    _raise_for_status(response)
    data = response.json()
    raw_list: list[dict[str, Any]] = _extract_result(data, "getSignaturesForAddress") or []

    signatures = [
        SignatureInfo(
            signature=item["signature"],
            slot=item["slot"],
            block_time=item.get("blockTime"),
            err=item.get("err"),
            memo=item.get("memo"),
            confirmation_status=item.get("confirmationStatus"),
        )
        for item in raw_list
    ]

    has_more = len(signatures) == limit

    logger.info(
        "helius_get_signatures_complete",
        address=address,
        count=len(signatures),
        has_more=has_more,
    )
    return SignaturesResult(signatures=signatures, has_more=has_more)


async def get_all_signatures_for_address(
    address: str,
    *,
    until: str | None = None,
    commitment: str = "finalized",
    max_pages: int = 100,
) -> list[SignatureInfo]:
    """Auto-paginate getSignaturesForAddress, collecting all signatures.

    Walks backwards through history using the ``before`` cursor until no more
    results or *max_pages* reached.

    Args:
        address: Base-58 encoded public key.
        until: Stop at this signature.
        commitment: ``"finalized"`` or ``"confirmed"``.
        max_pages: Safety cap on pagination loops.

    Returns:
        Complete list of ``SignatureInfo`` in reverse chronological order.
    """
    all_sigs: list[SignatureInfo] = []
    before: str | None = None
    pages = 0

    while pages < max_pages:
        result = await get_signatures_for_address(
            address,
            limit=SIGNATURES_MAX_LIMIT,
            before=before,
            until=until,
            commitment=commitment,
        )
        all_sigs.extend(result.signatures)
        pages += 1

        if not result.has_more or not result.signatures:
            break

        before = result.signatures[-1].signature

    logger.info(
        "helius_get_all_signatures_complete",
        address=address,
        total=len(all_sigs),
        pages=pages,
    )
    return all_sigs


async def get_transaction(
    signature: str,
    *,
    max_supported_transaction_version: int = 0,
    commitment: str = "finalized",
) -> dict[str, Any] | None:
    """Fetch full transaction details for a signature.

    Args:
        signature: Base-58 encoded transaction signature.
        max_supported_transaction_version: Include versioned txs (0 = legacy + v0).
        commitment: ``"finalized"`` or ``"confirmed"``.

    Returns:
        Parsed transaction dict, or ``None`` if the transaction was not found.
    """
    url = get_helius_rpc_url()

    options: dict[str, Any] = {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": max_supported_transaction_version,
        "commitment": commitment,
    }
    body = _build_jsonrpc_body("getTransaction", [signature, options])

    logger.debug("helius_get_transaction", signature=signature[:16])

    async with httpx.AsyncClient(timeout=DEFAULT_REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=body)

    _raise_for_status(response)
    data = response.json()
    result: dict[str, Any] | None = _extract_result(data, "getTransaction")

    if result is None:
        logger.info("helius_transaction_not_found", signature=signature[:16])
    else:
        logger.info("helius_get_transaction_complete", signature=signature[:16])

    return result


async def get_assets_by_owner(
    owner_address: str,
    *,
    page: int = 1,
    limit: int = ASSETS_MAX_LIMIT,
    display_options: AssetDisplayOptions | None = None,
) -> dict[str, Any]:
    """Fetch digital assets owned by a Solana wallet (DAS API).

    Args:
        owner_address: Base-58 encoded wallet address.
        page: 1-based page number.
        limit: Assets per page (max 1000).
        display_options: Controls which fields to include.

    Returns:
        Raw DAS response dict with ``items``, ``total``, ``limit``, ``page`` keys.
    """
    url = get_helius_rpc_url()
    limit = min(max(limit, 1), ASSETS_MAX_LIMIT)
    opts = display_options or AssetDisplayOptions()

    params: dict[str, Any] = {
        "ownerAddress": owner_address,
        "page": page,
        "limit": limit,
        "displayOptions": opts.to_dict(),
    }
    body = _build_jsonrpc_body("getAssetsByOwner", params)

    logger.debug("helius_get_assets_by_owner", owner=owner_address, page=page, limit=limit)

    async with httpx.AsyncClient(timeout=DEFAULT_REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=body)

    _raise_for_status(response)
    data = response.json()
    result: dict[str, Any] = _extract_result(data, "getAssetsByOwner") or {}

    items = result.get("items", [])
    logger.info(
        "helius_get_assets_by_owner_complete",
        owner=owner_address,
        page=page,
        items_returned=len(items),
        total=result.get("total"),
    )
    return result


# ---------------------------------------------------------------------------
# Credit tracking
# ---------------------------------------------------------------------------


def track_helius_credits(
    r: Any,
    call_count: int,
) -> None:
    """Increment daily Helius API credit counter in Redis (§20).

    Follows the same pattern as DeFiLlama tracking in workers/tasks/prices.py.

    Args:
        r: Redis client instance.
        call_count: Number of API calls to record.
    """
    if call_count <= 0:
        return
    key = f"{HELIUS_CREDIT_USAGE_KEY_PREFIX}:{datetime.now(UTC).strftime('%Y-%m-%d')}"
    pipe = r.pipeline()
    pipe.incrby(key, call_count)
    pipe.expire(key, HELIUS_CREDIT_USAGE_TTL_SECONDS)
    pipe.execute()
    logger.debug("helius_credits_tracked", call_count=call_count, key=key)
