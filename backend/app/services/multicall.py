"""Multicall3 batching utility for EVM ``eth_call`` reads (§9).

Batches arbitrary contract calls through the Multicall3 contract
(``0xcA11bde05977b3631167028862bE2a173976CA11``, same address on all EVM
chains) to reduce Alchemy CU consumption by 85-90%.

Used by Aave, Euler, Compound, Gearbox, Lido, and Rocket Pool adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
import structlog

from app.config import get_settings

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
MAX_CALLS_PER_BATCH = 150

ALCHEMY_RPC_URLS: dict[str, str] = {
    "ethereum": "https://eth-mainnet.g.alchemy.com/v2/{key}",
    "base": "https://base-mainnet.g.alchemy.com/v2/{key}",
}

AGGREGATE3_SELECTOR = bytes.fromhex("82ad56cb")


# ---------------------------------------------------------------------------
# ABI encoding / decoding helpers
# ---------------------------------------------------------------------------


def encode_address(addr: str) -> bytes:
    """Encode an Ethereum address as a 32-byte ABI word."""
    clean = addr.lower().removeprefix("0x")
    return bytes.fromhex(clean.zfill(64))


def encode_uint256(value: int) -> bytes:
    """Encode a uint256 as a 32-byte ABI word."""
    return value.to_bytes(32, byteorder="big")


def encode_bool(value: bool) -> bytes:
    """Encode a boolean as a 32-byte ABI word."""
    return encode_uint256(1 if value else 0)


def encode_bytes(data: bytes) -> bytes:
    """Encode dynamic ``bytes`` with length prefix and padding."""
    length = encode_uint256(len(data))
    padded_len = ((len(data) + 31) // 32) * 32
    padded = data + b"\x00" * (padded_len - len(data))
    return length + padded


def decode_uint256(data: bytes, offset: int = 0) -> int:
    """Decode a uint256 from 32 bytes at *offset*."""
    return int.from_bytes(data[offset : offset + 32], byteorder="big")


def decode_address(data: bytes, offset: int = 0) -> str:
    """Decode an address from 32 bytes at *offset* (last 20 bytes)."""
    return "0x" + data[offset + 12 : offset + 32].hex()


def decode_bool(data: bytes, offset: int = 0) -> bool:
    """Decode a boolean from 32 bytes at *offset*."""
    return decode_uint256(data, offset) != 0


def decode_string(data: bytes, offset: int = 0) -> str:
    """Decode a dynamic ABI string at *offset* (pointer-based)."""
    ptr = decode_uint256(data, offset)
    length = decode_uint256(data, ptr)
    return data[ptr + 32 : ptr + 32 + length].decode("utf-8", errors="replace")


def encode_function_call(selector: bytes, *args: bytes) -> bytes:
    """Build calldata from a 4-byte selector and pre-encoded arguments."""
    return selector + b"".join(args)


# ---------------------------------------------------------------------------
# MulticallBatcher
# ---------------------------------------------------------------------------


@dataclass
class Call:
    """A single call to batch through Multicall3."""

    target: str
    calldata: bytes
    allow_failure: bool = True


@dataclass
class MulticallBatcher:
    """Collects ``eth_call`` payloads and fires them through Multicall3.

    Usage::

        batcher = MulticallBatcher()
        idx = batcher.add_call("0xPool", calldata)
        results = await batcher.execute("ethereum")
        success, return_data = results[idx]
    """

    _calls: list[Call] = field(default_factory=list)
    _http_client: httpx.AsyncClient | None = field(default=None, repr=False)

    def add_call(
        self,
        target: str,
        calldata: bytes,
        *,
        allow_failure: bool = True,
    ) -> int:
        """Enqueue a call and return its index in the results list."""
        idx = len(self._calls)
        self._calls.append(Call(target=target, calldata=calldata, allow_failure=allow_failure))
        return idx

    async def execute(self, chain: str) -> list[tuple[bool, bytes]]:
        """Execute all queued calls via Multicall3 ``aggregate3``.

        Returns a list of ``(success, returnData)`` tuples in the same order
        calls were added.  Splits into multiple batches if more than
        ``MAX_CALLS_PER_BATCH`` calls are queued.
        """
        if not self._calls:
            return []

        settings = get_settings()
        rpc_template = ALCHEMY_RPC_URLS.get(chain.lower())
        if rpc_template is None:
            supported = ", ".join(sorted(ALCHEMY_RPC_URLS))
            raise ValueError(f"Unsupported chain {chain!r} for multicall. Supported: {supported}")

        rpc_url = rpc_template.format(key=settings.alchemy_api_key)

        all_results: list[tuple[bool, bytes]] = []
        batches = [
            self._calls[i : i + MAX_CALLS_PER_BATCH]
            for i in range(0, len(self._calls), MAX_CALLS_PER_BATCH)
        ]

        client = self._http_client or httpx.AsyncClient(timeout=30.0)
        owns_client = self._http_client is None
        try:
            for batch_idx, batch in enumerate(batches):
                result = await self._execute_batch(client, rpc_url, batch, batch_idx)
                all_results.extend(result)
        finally:
            if owns_client:
                await client.aclose()

        self._calls.clear()
        return all_results

    async def _execute_batch(
        self,
        client: httpx.AsyncClient,
        rpc_url: str,
        batch: list[Call],
        batch_idx: int,
    ) -> list[tuple[bool, bytes]]:
        """Execute a single batch of calls through Multicall3."""
        multicall_calldata = self._encode_aggregate3(batch)

        payload = {
            "jsonrpc": "2.0",
            "id": batch_idx + 1,
            "method": "eth_call",
            "params": [
                {
                    "to": MULTICALL3_ADDRESS,
                    "data": "0x" + multicall_calldata.hex(),
                },
                "latest",
            ],
        }

        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            err = body["error"]
            logger.error("multicall_rpc_error", error=err, batch_idx=batch_idx)
            raise RuntimeError(f"RPC error: {err.get('message', err)}")

        raw_hex = body["result"]
        return self._decode_aggregate3_result(raw_hex, len(batch))

    @staticmethod
    def _encode_aggregate3(calls: list[Call]) -> bytes:
        """Encode ``aggregate3((address target, bool allowFailure, bytes callData)[])``."""
        num_calls = len(calls)

        # aggregate3 takes a single dynamic array parameter
        # offset to the array data (1 word = 32 bytes)
        parts = AGGREGATE3_SELECTOR
        parts += encode_uint256(32)  # offset to the array
        parts += encode_uint256(num_calls)  # array length

        # Each struct element is dynamic (contains bytes), so we first write
        # offsets for each struct, then the struct data.
        struct_data_parts: list[bytes] = []
        for call in calls:
            cd = bytes.fromhex(call.calldata.hex()) if isinstance(call.calldata, bytes) else call.calldata
            struct = encode_address(call.target)
            struct += encode_bool(call.allow_failure)
            # offset to the dynamic bytes field within the struct
            struct += encode_uint256(96)  # 3 words (target + allowFailure + offset) = 96
            struct += encode_bytes(cd)
            struct_data_parts.append(struct)

        # Write offsets to each struct, then struct data
        offsets_section = b""
        current_offset = num_calls * 32  # past all the offset words
        data_section = b""
        for struct in struct_data_parts:
            offsets_section += encode_uint256(current_offset)
            data_section += struct
            current_offset += len(struct)

        parts += offsets_section + data_section
        return parts

    @staticmethod
    def _decode_aggregate3_result(
        raw_hex: str,
        num_calls: int,
    ) -> list[tuple[bool, bytes]]:
        """Decode the ``(bool success, bytes returnData)[]`` return value."""
        data = bytes.fromhex(raw_hex.removeprefix("0x"))

        # The result is a dynamic array: offset (32) + length (32) + elements
        array_offset = decode_uint256(data, 0)
        array_length = decode_uint256(data, array_offset)

        if array_length != num_calls:
            logger.warning(
                "multicall_result_length_mismatch",
                expected=num_calls,
                got=array_length,
            )

        results: list[tuple[bool, bytes]] = []
        # Element offsets start at array_offset + 32
        base = array_offset + 32
        for i in range(min(array_length, num_calls)):
            elem_offset = decode_uint256(data, base + i * 32) + base
            success = decode_bool(data, elem_offset)
            # returnData is dynamic bytes: offset at elem_offset+32
            rd_offset = decode_uint256(data, elem_offset + 32) + elem_offset
            rd_length = decode_uint256(data, rd_offset)
            return_data = data[rd_offset + 32 : rd_offset + 32 + rd_length]
            results.append((success, return_data))

        return results
