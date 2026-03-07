"""Chain-specific event scanning for wallet history reconstruction (§12).

EVM chains: Uses HyperSync to query protocol events where the wallet address
appears in indexed topic positions.

Solana: Uses Helius to fetch all transaction signatures and parse known
DeFi program interactions.

Both scanners bridge async → sync via ``asyncio.run()`` for Celery worker
compatibility.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

from app.services.helius_client import (
    get_all_signatures_for_address,
    get_transaction,
)
from app.services.hypersync_client import (
    HYPERSYNC_CHAINS,
    PROTOCOL_EVENT_TOPICS,
    get_chain_height,
    get_hypersync_client,
)
from workers.services.schemas import RawEvent

logger = structlog.get_logger(__name__)

EVM_CHAINS = frozenset(HYPERSYNC_CHAINS.keys())

TOPIC0_TO_ACTION: dict[str, tuple[str, str]] = {
    PROTOCOL_EVENT_TOPICS["erc20_transfer"]: ("unknown", "transfer"),
    PROTOCOL_EVENT_TOPICS["erc4626_deposit"]: ("erc4626", "deposit"),
    PROTOCOL_EVENT_TOPICS["erc4626_withdraw"]: ("erc4626", "withdraw"),
    PROTOCOL_EVENT_TOPICS["aave_supply"]: ("aave_v3", "deposit"),
    PROTOCOL_EVENT_TOPICS["aave_withdraw"]: ("aave_v3", "withdraw"),
    PROTOCOL_EVENT_TOPICS["aave_borrow"]: ("aave_v3", "borrow"),
    PROTOCOL_EVENT_TOPICS["aave_repay"]: ("aave_v3", "repay"),
    PROTOCOL_EVENT_TOPICS["morpho_supply"]: ("morpho", "deposit"),
    PROTOCOL_EVENT_TOPICS["morpho_withdraw"]: ("morpho", "withdraw"),
    PROTOCOL_EVENT_TOPICS["morpho_borrow"]: ("morpho", "borrow"),
    PROTOCOL_EVENT_TOPICS["morpho_repay"]: ("morpho", "repay"),
    PROTOCOL_EVENT_TOPICS["lifi_generic_swap"]: ("lifi", "swap"),
    PROTOCOL_EVENT_TOPICS["oneinch_swapped"]: ("oneinch", "swap"),
    PROTOCOL_EVENT_TOPICS["cow_trade"]: ("cow", "swap"),
    PROTOCOL_EVENT_TOPICS["uniswap_v3_swap"]: ("uniswap_v3", "swap"),
    PROTOCOL_EVENT_TOPICS["uniswap_v2_swap"]: ("uniswap_v2", "swap"),
}

DEPOSIT_ACTIONS = frozenset({"deposit", "borrow"})
WITHDRAWAL_ACTIONS = frozenset({"withdraw", "repay"})

GENESIS_BLOCK = 0


def _pad_address(address: str) -> str:
    """Pad a 20-byte EVM address to a 32-byte topic value (left-zero-padded)."""
    addr = address.lower().removeprefix("0x")
    return "0x" + addr.zfill(64)


def _parse_topics(raw: Any) -> list[str]:
    """Parse Log.topics into a list of hex topic strings.

    HyperSync Python bindings may return topics as a list, a JSON string,
    or a single hex string depending on the version.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                pass
        if len(stripped) > 66:
            topics: list[str] = []
            s = stripped.removeprefix("0x")
            while len(s) >= 64:
                topics.append("0x" + s[:64])
                s = s[64:]
            if topics:
                return topics
        return [stripped] if stripped else []
    return []


def _decode_uint256(hex_str: str, offset: int = 0) -> int:
    """Decode a uint256 from hex data at the given 32-byte word offset."""
    start = offset * 64
    end = start + 64
    clean = hex_str.removeprefix("0x")
    if len(clean) < end:
        return 0
    return int(clean[start:end], 16)


def _unpad_address(topic: str) -> str:
    """Extract a 20-byte address from a 32-byte padded topic."""
    clean = topic.removeprefix("0x").lower()
    return "0x" + clean[-40:]


def _amount_to_decimal(raw_amount: int, decimals: int = 18) -> Decimal:
    """Convert a raw integer token amount to a Decimal with the given decimals."""
    return Decimal(raw_amount) / Decimal(10**decimals)


# ---------------------------------------------------------------------------
# EVM scanning via HyperSync
# ---------------------------------------------------------------------------

import hypersync  # noqa: E402


async def _scan_evm_events_async(
    wallet_address: str,
    chain: str,
    from_block: int,
    to_block: int | None = None,
) -> tuple[list[RawEvent], int]:
    """Async implementation of EVM event scanning.

    Returns (events, highest_block_seen).
    """
    client = get_hypersync_client(chain)

    if to_block is None:
        to_block = await get_chain_height(client)

    padded_wallet = _pad_address(wallet_address)
    known_topic0s = list(TOPIC0_TO_ACTION.keys())

    log_fields = [
        hypersync.LogField.BLOCK_NUMBER,
        hypersync.LogField.TRANSACTION_HASH,
        hypersync.LogField.ADDRESS,
        hypersync.LogField.TOPIC0,
        hypersync.LogField.TOPIC1,
        hypersync.LogField.TOPIC2,
        hypersync.LogField.TOPIC3,
        hypersync.LogField.DATA,
        hypersync.LogField.LOG_INDEX,
    ]
    block_fields = [
        hypersync.BlockField.NUMBER,
        hypersync.BlockField.TIMESTAMP,
    ]
    field_selection = hypersync.FieldSelection(
        log=log_fields,
        block=block_fields,
    )

    log_selections = [
        hypersync.LogSelection(topics=[known_topic0s, [padded_wallet]]),
        hypersync.LogSelection(topics=[known_topic0s, [], [padded_wallet]]),
        hypersync.LogSelection(topics=[known_topic0s, [], [], [padded_wallet]]),
    ]

    query = hypersync.Query(
        from_block=from_block,
        to_block=to_block,
        logs=log_selections,
        field_selection=field_selection,
        include_all_blocks=True,
    )

    all_logs: list[hypersync.Log] = []
    block_timestamps: dict[int, int] = {}
    highest_block = from_block

    while True:
        res = await client.get(query)

        for block in res.data.blocks:
            if block.number is not None and block.timestamp is not None:
                block_timestamps[block.number] = block.timestamp

        all_logs.extend(res.data.logs)

        if res.next_block >= (to_block or 0) or res.next_block >= (res.archive_height or 0):
            break
        query.from_block = res.next_block

    logger.info(
        "evm_scan_complete",
        chain=chain,
        wallet=wallet_address[:10],
        logs_found=len(all_logs),
        blocks_with_timestamps=len(block_timestamps),
    )

    events: list[RawEvent] = []
    for log in all_logs:
        try:
            event = _parse_evm_log(log, chain, wallet_address, block_timestamps)
            if event is not None:
                events.append(event)
                if event.block_number and event.block_number > highest_block:
                    highest_block = event.block_number
        except Exception:
            logger.debug(
                "evm_log_parse_failed",
                tx_hash=log.transaction_hash,
                exc_info=True,
            )

    events.sort(key=lambda e: (e.timestamp, e.log_index or 0))
    return events, highest_block


def _parse_evm_log(
    log: hypersync.Log,
    chain: str,
    wallet_address: str,
    block_timestamps: dict[int, int],
) -> RawEvent | None:
    """Parse a single HyperSync Log into a RawEvent."""
    topics = _parse_topics(log.topics)
    if not topics:
        return None

    topic0 = topics[0].lower() if topics else None
    if topic0 is None or topic0 not in TOPIC0_TO_ACTION:
        return None

    protocol, action = TOPIC0_TO_ACTION[topic0]

    contract_address = (log.address or "").lower()
    block_number = log.block_number
    tx_hash = log.transaction_hash or ""

    block_ts = block_timestamps.get(block_number or 0)
    if block_ts is None:
        return None
    timestamp = datetime.fromtimestamp(block_ts, tz=UTC)

    data_hex = (log.data or "0x").removeprefix("0x")

    if topic0 == PROTOCOL_EVENT_TOPICS["erc20_transfer"]:
        from_addr = _unpad_address(topics[1]) if len(topics) > 1 else ""
        to_addr = _unpad_address(topics[2]) if len(topics) > 2 else ""
        raw_amount = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_amount)

        if from_addr.lower() == wallet_address.lower():
            action = "transfer_out"
        elif to_addr.lower() == wallet_address.lower():
            action = "transfer_in"
        else:
            return None

        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol="erc20",
            vault_or_market_id=contract_address,
            action=action,
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if topic0 in (
        PROTOCOL_EVENT_TOPICS["erc4626_deposit"],
        PROTOCOL_EVENT_TOPICS["euler_deposit"],
    ):
        raw_assets = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_assets)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="deposit",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if topic0 in (
        PROTOCOL_EVENT_TOPICS["erc4626_withdraw"],
        PROTOCOL_EVENT_TOPICS["euler_withdraw"],
    ):
        raw_assets = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_assets)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="withdraw",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if topic0 in (
        PROTOCOL_EVENT_TOPICS["aave_supply"],
        PROTOCOL_EVENT_TOPICS["morpho_supply"],
    ):
        raw_amount = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_amount)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="deposit",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if topic0 in (
        PROTOCOL_EVENT_TOPICS["aave_withdraw"],
        PROTOCOL_EVENT_TOPICS["morpho_withdraw"],
    ):
        raw_amount = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_amount)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="withdraw",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if topic0 in (
        PROTOCOL_EVENT_TOPICS["aave_borrow"],
        PROTOCOL_EVENT_TOPICS["morpho_borrow"],
    ):
        raw_amount = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_amount)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="borrow",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if topic0 in (
        PROTOCOL_EVENT_TOPICS["aave_repay"],
        PROTOCOL_EVENT_TOPICS["morpho_repay"],
    ):
        raw_amount = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_amount)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="repay",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    if action == "swap":
        raw_amount = _decode_uint256("0x" + data_hex, 0) if data_hex else 0
        amount = _amount_to_decimal(raw_amount)
        return RawEvent(
            tx_hash=tx_hash,
            block_number=block_number,
            timestamp=timestamp,
            chain=chain,
            protocol=protocol,
            vault_or_market_id=contract_address,
            action="swap",
            wallet_address=wallet_address,
            asset_address=contract_address,
            asset_symbol=None,
            amount=amount,
            log_index=log.log_index,
        )

    return None


def scan_evm_events(
    wallet_address: str,
    chain: str,
    from_block: int = GENESIS_BLOCK,
    to_block: int | None = None,
) -> tuple[list[RawEvent], int]:
    """Scan EVM chain for events involving the wallet (sync wrapper).

    Returns (events, highest_block_seen).
    """
    return asyncio.run(
        _scan_evm_events_async(wallet_address, chain, from_block, to_block)
    )


# ---------------------------------------------------------------------------
# Solana scanning via Helius
# ---------------------------------------------------------------------------


async def _scan_solana_events_async(
    wallet_address: str,
) -> list[RawEvent]:
    """Async implementation of Solana event scanning."""
    sigs = await get_all_signatures_for_address(wallet_address)

    events: list[RawEvent] = []
    for sig_info in sigs:
        if sig_info.err is not None:
            continue

        try:
            tx = await get_transaction(sig_info.signature)
        except Exception:
            logger.debug("solana_tx_fetch_failed", sig=sig_info.signature[:16], exc_info=True)
            continue

        if tx is None:
            continue

        try:
            parsed = _parse_solana_transaction(tx, wallet_address, sig_info)
            events.extend(parsed)
        except Exception:
            logger.debug("solana_tx_parse_failed", sig=sig_info.signature[:16], exc_info=True)

    events.sort(key=lambda e: e.timestamp)
    logger.info(
        "solana_scan_complete",
        wallet=wallet_address[:10],
        signatures_checked=len(sigs),
        events_found=len(events),
    )
    return events


def _parse_solana_transaction(
    tx: dict[str, Any],
    wallet_address: str,
    sig_info: Any,
) -> list[RawEvent]:
    """Extract RawEvents from a parsed Solana transaction."""
    events: list[RawEvent] = []
    block_time = sig_info.block_time or tx.get("blockTime")
    if block_time is None:
        return events
    timestamp = datetime.fromtimestamp(block_time, tz=UTC)

    meta = tx.get("meta", {})
    if meta is None or meta.get("err") is not None:
        return events

    pre_balances = meta.get("preTokenBalances", [])
    post_balances = meta.get("postTokenBalances", [])

    pre_by_idx: dict[int, dict] = {b["accountIndex"]: b for b in pre_balances}
    post_by_idx: dict[int, dict] = {b["accountIndex"]: b for b in post_balances}

    account_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
    wallet_indices: set[int] = set()
    for i, key in enumerate(account_keys):
        pk = key if isinstance(key, str) else key.get("pubkey", "")
        if pk == wallet_address:
            wallet_indices.add(i)

    all_indices = set(pre_by_idx.keys()) | set(post_by_idx.keys())
    for idx in all_indices:
        pre = pre_by_idx.get(idx, {})
        post = post_by_idx.get(idx, {})

        pre_owner = pre.get("owner", "")
        post_owner = post.get("owner", "")
        if wallet_address not in (pre_owner, post_owner):
            continue

        pre_amount = Decimal(pre.get("uiTokenAmount", {}).get("uiAmountString", "0") or "0")
        post_amount = Decimal(post.get("uiTokenAmount", {}).get("uiAmountString", "0") or "0")
        diff = post_amount - pre_amount

        if diff == 0:
            continue

        mint = post.get("mint") or pre.get("mint", "unknown")

        action = "deposit" if diff > 0 else "withdraw"
        amount = abs(diff)

        events.append(
            RawEvent(
                tx_hash=sig_info.signature,
                block_number=None,
                timestamp=timestamp,
                chain="solana",
                protocol="spl_token",
                vault_or_market_id=mint,
                action=action,
                wallet_address=wallet_address,
                asset_address=mint,
                asset_symbol=None,
                amount=amount,
            )
        )

    return events


def scan_solana_events(wallet_address: str) -> list[RawEvent]:
    """Scan Solana for events involving the wallet (sync wrapper)."""
    return asyncio.run(_scan_solana_events_async(wallet_address))


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------


def scan_events(
    wallet_address: str,
    chain: str,
    from_block: int = GENESIS_BLOCK,
) -> tuple[list[RawEvent], int | None]:
    """Scan the appropriate chain for all events involving the wallet.

    Returns (events, highest_block_seen).
    ``highest_block_seen`` is None for Solana (block-less model).
    """
    chain_lower = chain.lower()

    if chain_lower in EVM_CHAINS:
        events, highest_block = scan_evm_events(wallet_address, chain_lower, from_block)
        logger.info(
            "scan_events_complete",
            chain=chain_lower,
            wallet=wallet_address[:10],
            events=len(events),
            highest_block=highest_block,
        )
        return events, highest_block

    if chain_lower == "solana":
        events = scan_solana_events(wallet_address)
        logger.info(
            "scan_events_complete",
            chain="solana",
            wallet=wallet_address[:10],
            events=len(events),
        )
        return events, None

    logger.warning("scan_events_unsupported_chain", chain=chain_lower)
    return [], None
