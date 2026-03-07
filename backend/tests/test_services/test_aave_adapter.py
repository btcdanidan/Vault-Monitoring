"""Tests for the Aave v3 protocol adapter and MulticallBatcher (§9).

Covers: ABI encoding/decoding helpers, MulticallBatcher batching logic,
APY conversion formula, and all three AaveAdapter methods with mocked
RPC / HyperSync responses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.aave import AaveAdapter, _amount_to_decimal, _pad_address, _unpad_address
from app.adapters.aave_constants import (
    AAVE_BORROW_TOPIC,
    AAVE_CHAIN_CONFIGS,
    AAVE_REPAY_TOPIC,
    AAVE_SUPPLY_TOPIC,
    AAVE_WITHDRAW_TOPIC,
    RAY,
    SECONDS_PER_YEAR,
    ray_to_apy,
)
from app.services.multicall import (
    MulticallBatcher,
    decode_address,
    decode_bool,
    decode_uint256,
    encode_address,
    encode_bool,
    encode_bytes,
    encode_function_call,
    encode_uint256,
)


# ---------------------------------------------------------------------------
# ABI encoding helpers
# ---------------------------------------------------------------------------


class TestEncodeAddress:
    def test_zero_address(self) -> None:
        result = encode_address("0x0000000000000000000000000000000000000000")
        assert len(result) == 32
        assert result == b"\x00" * 32

    def test_real_address(self) -> None:
        addr = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        result = encode_address(addr)
        assert len(result) == 32
        assert result[-20:] == bytes.fromhex("87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")

    def test_without_0x_prefix(self) -> None:
        result = encode_address("87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2")
        assert len(result) == 32


class TestEncodeUint256:
    def test_zero(self) -> None:
        result = encode_uint256(0)
        assert result == b"\x00" * 32

    def test_one(self) -> None:
        result = encode_uint256(1)
        assert result == b"\x00" * 31 + b"\x01"

    def test_max_uint256(self) -> None:
        result = encode_uint256(2**256 - 1)
        assert result == b"\xff" * 32

    def test_round_trip(self) -> None:
        for val in (0, 1, 42, 10**18, 2**128, 2**256 - 1):
            assert decode_uint256(encode_uint256(val)) == val


class TestEncodeBool:
    def test_true(self) -> None:
        result = encode_bool(True)
        assert decode_uint256(result) == 1

    def test_false(self) -> None:
        result = encode_bool(False)
        assert decode_uint256(result) == 0


class TestEncodeBytes:
    def test_empty(self) -> None:
        result = encode_bytes(b"")
        assert len(result) == 32  # just the length word

    def test_small_data(self) -> None:
        data = b"\x01\x02\x03"
        result = encode_bytes(data)
        assert decode_uint256(result[:32]) == 3
        assert result[32:35] == data

    def test_32_byte_data(self) -> None:
        data = b"\xab" * 32
        result = encode_bytes(data)
        assert decode_uint256(result[:32]) == 32
        assert result[32:64] == data


class TestDecodeHelpers:
    def test_decode_address(self) -> None:
        data = encode_address("0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2")
        addr = decode_address(data)
        assert addr == "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"

    def test_decode_bool_true(self) -> None:
        assert decode_bool(encode_bool(True)) is True

    def test_decode_bool_false(self) -> None:
        assert decode_bool(encode_bool(False)) is False


class TestEncodeFunctionCall:
    def test_no_args(self) -> None:
        selector = bytes.fromhex("d1946dbc")
        result = encode_function_call(selector)
        assert result == selector

    def test_with_address_arg(self) -> None:
        selector = bytes.fromhex("bf92857c")
        addr_bytes = encode_address("0x1234567890123456789012345678901234567890")
        result = encode_function_call(selector, addr_bytes)
        assert result[:4] == selector
        assert len(result) == 36


# ---------------------------------------------------------------------------
# MulticallBatcher
# ---------------------------------------------------------------------------


class TestMulticallBatcher:
    def test_add_call_returns_sequential_indices(self) -> None:
        batcher = MulticallBatcher()
        assert batcher.add_call("0xA", b"\x01") == 0
        assert batcher.add_call("0xB", b"\x02") == 1
        assert batcher.add_call("0xC", b"\x03") == 2

    async def test_execute_empty_returns_empty(self) -> None:
        batcher = MulticallBatcher()
        result = await batcher.execute("ethereum")
        assert result == []

    async def test_execute_unsupported_chain_raises(self) -> None:
        batcher = MulticallBatcher()
        batcher.add_call("0xA", b"\x01")
        with pytest.raises(ValueError, match="Unsupported chain"):
            await batcher.execute("solana")

    async def test_execute_sends_rpc_call(self) -> None:
        batcher = MulticallBatcher()
        batcher.add_call("0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2", b"\x01\x02")

        # Build a mock response that mimics a Multicall3 aggregate3 return:
        # Returns array of (bool, bytes) where success=true, data=0x01
        mock_result = _build_aggregate3_response([(True, b"\x00" * 32)])

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": mock_result}
        mock_client.post = AsyncMock(return_value=mock_resp)

        batcher._http_client = mock_client
        with patch("app.services.multicall.get_settings") as mock_settings:
            mock_settings.return_value.alchemy_api_key = "test-key"
            results = await batcher.execute("ethereum")

        assert len(results) == 1
        assert results[0][0] is True

    async def test_execute_handles_rpc_error(self) -> None:
        batcher = MulticallBatcher()
        batcher.add_call("0xA", b"\x01")

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "execution reverted"},
        }
        mock_client.post = AsyncMock(return_value=mock_resp)

        batcher._http_client = mock_client
        with (
            patch("app.services.multicall.get_settings") as mock_settings,
            pytest.raises(RuntimeError, match="RPC error"),
        ):
            mock_settings.return_value.alchemy_api_key = "test-key"
            await batcher.execute("ethereum")

    def test_encode_aggregate3_produces_valid_calldata(self) -> None:
        from app.services.multicall import Call

        calls = [
            Call(target="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2", calldata=b"\x01\x02"),
        ]
        encoded = MulticallBatcher._encode_aggregate3(calls)
        assert encoded[:4] == bytes.fromhex("82ad56cb")
        assert len(encoded) > 4


# ---------------------------------------------------------------------------
# APY conversion
# ---------------------------------------------------------------------------


class TestRayToApy:
    def test_zero_rate(self) -> None:
        assert ray_to_apy(0) == 0.0

    def test_known_rate(self) -> None:
        # 5% APR in ray = 0.05 * 1e27 / 31536000 per second
        # But Aave stores the per-second compound rate scaled to ray
        # For a ~3% supply rate: liquidityRate ~ 30000000000000000000000000 (3e25)
        rate_ray = 3 * 10**25  # ~3% APR
        apy = ray_to_apy(rate_ray)
        assert 2.5 < apy < 3.5  # roughly 3%

    def test_high_rate(self) -> None:
        rate_ray = 10**26  # ~10% APR
        apy = ray_to_apy(rate_ray)
        assert 9.0 < apy < 11.0

    def test_formula_matches_spec(self) -> None:
        # §9: supplyAPY = (((1 + (liquidityRate/1e27/31536000))^31536000) - 1) * 100
        rate_ray = 5 * 10**25
        expected = (((1 + (rate_ray / RAY / SECONDS_PER_YEAR)) ** SECONDS_PER_YEAR) - 1) * 100
        actual = ray_to_apy(rate_ray)
        assert abs(actual - expected) < 0.0001


# ---------------------------------------------------------------------------
# Aave adapter helpers
# ---------------------------------------------------------------------------


class TestAaveHelpers:
    def test_pad_address(self) -> None:
        addr = "0x1234567890AbCdEf1234567890AbCdEf12345678"
        padded = _pad_address(addr)
        assert padded.startswith("0x")
        assert len(padded) == 66  # 0x + 64 hex chars
        assert padded.endswith("1234567890abcdef1234567890abcdef12345678")

    def test_unpad_address(self) -> None:
        padded = "0x0000000000000000000000001234567890abcdef1234567890abcdef12345678"
        addr = _unpad_address(padded)
        assert addr == "0x1234567890abcdef1234567890abcdef12345678"

    def test_amount_to_decimal_18_decimals(self) -> None:
        result = _amount_to_decimal(10**18, 18)
        assert result == Decimal("1")

    def test_amount_to_decimal_6_decimals(self) -> None:
        result = _amount_to_decimal(1_000_000, 6)
        assert result == Decimal("1")

    def test_amount_to_decimal_zero(self) -> None:
        result = _amount_to_decimal(0, 18)
        assert result == Decimal("0")


# ---------------------------------------------------------------------------
# AaveAdapter properties
# ---------------------------------------------------------------------------


class TestAaveAdapterProperties:
    def test_protocol_name(self) -> None:
        adapter = AaveAdapter()
        assert adapter.protocol_name == "aave_v3"

    def test_supported_chains(self) -> None:
        adapter = AaveAdapter()
        assert adapter.supported_chains == ["ethereum", "base"]

    def test_chain_configs_present(self) -> None:
        for chain in ("ethereum", "base"):
            cfg = AAVE_CHAIN_CONFIGS[chain]
            assert cfg.pool.startswith("0x")
            assert cfg.ui_pool_data_provider.startswith("0x")
            assert cfg.pool_addresses_provider.startswith("0x")


# ---------------------------------------------------------------------------
# AaveAdapter.fetch_live_metrics (mocked)
# ---------------------------------------------------------------------------


class TestAaveAdapterFetchLiveMetrics:
    async def test_unsupported_chain_returns_empty(self) -> None:
        adapter = AaveAdapter()
        result = await adapter.fetch_live_metrics([], "solana")
        assert result == []

    async def test_fetches_reserves_and_metrics(self) -> None:
        adapter = AaveAdapter()

        reserve_addr = "0x6b175474e89094c44da98b954eedeac495271d0f"  # DAI-like
        reserves_list_data = _encode_address_array([reserve_addr])

        # Fake getReserveData return (15 words min)
        liquidity_rate = 3 * 10**25  # ~3% APR
        variable_rate = 5 * 10**25  # ~5% APR
        a_token = "0x0000000000000000000000000000000000000001"
        reserve_data = b"\x00" * 64  # words 0-1
        reserve_data += encode_uint256(liquidity_rate)  # word 2
        reserve_data += b"\x00" * 32  # word 3
        reserve_data += encode_uint256(variable_rate)  # word 4
        reserve_data += b"\x00" * 96  # words 5-7
        reserve_data += encode_address(a_token)  # word 8
        reserve_data += b"\x00" * 192  # words 9-14

        call_count = 0

        async def mock_execute(chain: str) -> list[tuple[bool, bytes]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [(True, reserves_list_data)]
            else:
                return [(True, reserve_data)]

        with (
            patch.object(MulticallBatcher, "execute", side_effect=mock_execute),
            patch.object(adapter, "_get_http", new_callable=AsyncMock),
        ):
            result = await adapter.fetch_live_metrics([], "ethereum")

        assert len(result) == 1
        m = result[0]
        assert m.vault_id == f"aave_v3_ethereum_{reserve_addr}"
        assert m.protocol == "aave_v3"
        assert m.chain == "ethereum"
        assert m.redemption_type == "instant"
        assert m.net_apy is not None
        assert float(m.net_apy) > 0
        assert m.borrow_rate is not None
        assert float(m.borrow_rate) > 0

    async def test_empty_reserves_returns_empty(self) -> None:
        adapter = AaveAdapter()

        empty_reserves = _encode_address_array([])

        async def mock_execute(chain: str) -> list[tuple[bool, bytes]]:
            return [(True, empty_reserves)]

        with (
            patch.object(MulticallBatcher, "execute", side_effect=mock_execute),
            patch.object(adapter, "_get_http", new_callable=AsyncMock),
        ):
            result = await adapter.fetch_live_metrics([], "ethereum")

        assert result == []

    async def test_failed_reserves_list_returns_empty(self) -> None:
        adapter = AaveAdapter()

        async def mock_execute(chain: str) -> list[tuple[bool, bytes]]:
            return [(False, b"")]

        with (
            patch.object(MulticallBatcher, "execute", side_effect=mock_execute),
            patch.object(adapter, "_get_http", new_callable=AsyncMock),
        ):
            result = await adapter.fetch_live_metrics([], "ethereum")

        assert result == []


# ---------------------------------------------------------------------------
# AaveAdapter.fetch_positions (mocked)
# ---------------------------------------------------------------------------


class TestAaveAdapterFetchPositions:
    async def test_unsupported_chain_returns_empty(self) -> None:
        adapter = AaveAdapter()
        result = await adapter.fetch_positions("0xwallet", "solana")
        assert result == []

    async def test_fetches_positions_with_balances(self) -> None:
        adapter = AaveAdapter()
        wallet = "0x1234567890123456789012345678901234567890"
        reserve = "0x6b175474e89094c44da98b954eedeac495271d0f"

        # Phase 1: getUserAccountData + getReservesList
        health_factor_raw = 2 * 10**18  # HF = 2.0
        user_account_data = b"\x00" * 160  # words 0-4
        user_account_data += encode_uint256(health_factor_raw)  # word 5 = healthFactor

        reserves_list = _encode_address_array([reserve])

        # Phase 2: getReserveData
        a_token = "0x0000000000000000000000000000000000000aaa"
        debt_token = "0x0000000000000000000000000000000000000bbb"
        reserve_data = b"\x00" * 256  # words 0-7
        reserve_data += encode_address(a_token)  # word 8
        reserve_data += b"\x00" * 32  # word 9
        reserve_data += encode_address(debt_token)  # word 10
        reserve_data += b"\x00" * 128  # words 11-14

        # Phase 3: balanceOf(aToken) + balanceOf(debtToken) + decimals
        supply_balance = 5 * 10**18  # 5 tokens
        borrow_balance = 2 * 10**18  # 2 tokens
        decimals_raw = 18

        call_count = 0

        async def mock_execute(chain: str) -> list[tuple[bool, bytes]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [
                    (True, user_account_data),
                    (True, reserves_list),
                ]
            elif call_count == 2:
                return [(True, reserve_data)]
            else:
                return [
                    (True, encode_uint256(supply_balance)),
                    (True, encode_uint256(borrow_balance)),
                    (True, encode_uint256(decimals_raw)),
                ]

        with (
            patch.object(MulticallBatcher, "execute", side_effect=mock_execute),
            patch.object(adapter, "_get_http", new_callable=AsyncMock),
        ):
            result = await adapter.fetch_positions(wallet, "ethereum")

        assert len(result) == 2
        supply_pos = next(p for p in result if p.position_type == "supply")
        borrow_pos = next(p for p in result if p.position_type == "borrow")

        assert supply_pos.current_shares_or_amount == Decimal("5")
        assert borrow_pos.current_shares_or_amount == Decimal("2")
        assert supply_pos.health_factor == Decimal("2")
        assert supply_pos.protocol == "aave_v3"

    async def test_zero_balances_excluded(self) -> None:
        adapter = AaveAdapter()
        wallet = "0x1234567890123456789012345678901234567890"
        reserve = "0x6b175474e89094c44da98b954eedeac495271d0f"

        user_account_data = b"\x00" * 192
        reserves_list = _encode_address_array([reserve])

        a_token = "0x0000000000000000000000000000000000000aaa"
        debt_token = "0x0000000000000000000000000000000000000bbb"
        reserve_data = b"\x00" * 256
        reserve_data += encode_address(a_token)
        reserve_data += b"\x00" * 32
        reserve_data += encode_address(debt_token)
        reserve_data += b"\x00" * 128

        call_count = 0

        async def mock_execute(chain: str) -> list[tuple[bool, bytes]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [(True, user_account_data), (True, reserves_list)]
            elif call_count == 2:
                return [(True, reserve_data)]
            else:
                return [
                    (True, encode_uint256(0)),  # zero supply
                    (True, encode_uint256(0)),  # zero debt
                    (True, encode_uint256(18)),
                ]

        with (
            patch.object(MulticallBatcher, "execute", side_effect=mock_execute),
            patch.object(adapter, "_get_http", new_callable=AsyncMock),
        ):
            result = await adapter.fetch_positions(wallet, "ethereum")

        assert result == []


# ---------------------------------------------------------------------------
# AaveAdapter.fetch_historical_events (mocked HyperSync)
# ---------------------------------------------------------------------------


class TestAaveAdapterFetchHistoricalEvents:
    async def test_unsupported_chain_returns_empty(self) -> None:
        adapter = AaveAdapter()
        result = await adapter.fetch_historical_events("0xwallet", "solana", 0, 100)
        assert result == []

    async def test_parses_supply_event(self) -> None:
        adapter = AaveAdapter()
        wallet = "0x1234567890123456789012345678901234567890"

        reserve = "0x6b175474e89094c44da98b954eedeac495271d0f"
        padded_reserve = "0x" + reserve.removeprefix("0x").zfill(64)
        padded_wallet = "0x" + wallet.removeprefix("0x").zfill(64)

        amount_raw = 10 * 10**18
        data_hex = "0x" + amount_raw.to_bytes(32, "big").hex() + "0" * 64

        mock_log = MagicMock()
        mock_log.topics = [AAVE_SUPPLY_TOPIC, padded_reserve, padded_wallet]
        mock_log.data = data_hex
        mock_log.block_number = 20000000
        mock_log.transaction_hash = "0xabc123"
        mock_log.log_index = 5

        mock_block = MagicMock()
        mock_block.number = 20000000
        mock_block.timestamp = 1700000000

        mock_res = MagicMock()
        mock_res.data.logs = [mock_log]
        mock_res.data.blocks = [mock_block]
        mock_res.next_block = 20000001
        mock_res.archive_height = 20000001

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_res)

        with (
            patch("app.adapters.aave.get_hypersync_client", return_value=mock_client),
            patch("app.adapters.aave.get_chain_height", return_value=20000001),
        ):
            result = await adapter.fetch_historical_events(wallet, "ethereum", 19999999, 20000001)

        assert len(result) == 1
        event = result[0]
        assert event.action == "deposit"
        assert event.protocol == "aave_v3"
        assert event.chain == "ethereum"
        assert event.wallet_address == wallet
        assert event.asset_address == reserve
        assert event.tx_hash == "0xabc123"
        assert event.block_number == 20000000

    async def test_parses_multiple_event_types(self) -> None:
        adapter = AaveAdapter()
        wallet = "0x1234567890123456789012345678901234567890"
        reserve = "0x6b175474e89094c44da98b954eedeac495271d0f"
        padded_reserve = "0x" + reserve.removeprefix("0x").zfill(64)
        padded_wallet = "0x" + wallet.removeprefix("0x").zfill(64)

        amount_hex = "0x" + (10**18).to_bytes(32, "big").hex()

        events_data = [
            (AAVE_SUPPLY_TOPIC, "deposit"),
            (AAVE_WITHDRAW_TOPIC, "withdraw"),
            (AAVE_BORROW_TOPIC, "borrow"),
            (AAVE_REPAY_TOPIC, "repay"),
        ]

        mock_logs = []
        for i, (topic, _) in enumerate(events_data):
            log = MagicMock()
            log.topics = [topic, padded_reserve, padded_wallet]
            log.data = amount_hex
            log.block_number = 20000000 + i
            log.transaction_hash = f"0xtx{i}"
            log.log_index = i
            mock_logs.append(log)

        mock_blocks = []
        for i in range(len(events_data)):
            block = MagicMock()
            block.number = 20000000 + i
            block.timestamp = 1700000000 + i * 12
            mock_blocks.append(block)

        mock_res = MagicMock()
        mock_res.data.logs = mock_logs
        mock_res.data.blocks = mock_blocks
        mock_res.next_block = 20000010
        mock_res.archive_height = 20000010

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_res)

        with (
            patch("app.adapters.aave.get_hypersync_client", return_value=mock_client),
            patch("app.adapters.aave.get_chain_height", return_value=20000010),
        ):
            result = await adapter.fetch_historical_events(wallet, "ethereum", 19999999, 20000010)

        assert len(result) == 4
        actions = [e.action for e in result]
        assert "deposit" in actions
        assert "withdraw" in actions
        assert "borrow" in actions
        assert "repay" in actions

    async def test_empty_logs_returns_empty(self) -> None:
        adapter = AaveAdapter()

        mock_res = MagicMock()
        mock_res.data.logs = []
        mock_res.data.blocks = []
        mock_res.next_block = 100
        mock_res.archive_height = 100

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_res)

        with (
            patch("app.adapters.aave.get_hypersync_client", return_value=mock_client),
            patch("app.adapters.aave.get_chain_height", return_value=100),
        ):
            result = await adapter.fetch_historical_events(
                "0xwallet", "ethereum", 0, 100
            )

        assert result == []


# ---------------------------------------------------------------------------
# Address array encoding helper (for test data)
# ---------------------------------------------------------------------------


def _encode_address_array(addresses: list[str]) -> bytes:
    """Encode a dynamic ``address[]`` as ABI return data."""
    data = encode_uint256(32)  # offset to the array
    data += encode_uint256(len(addresses))
    for addr in addresses:
        data += encode_address(addr)
    return data


def _build_aggregate3_response(results: list[tuple[bool, bytes]]) -> str:
    """Build a hex-encoded Multicall3 ``aggregate3`` response for testing."""
    num = len(results)

    # Dynamic array: offset + length + element offsets + element data
    array_offset = 32  # offset to array start
    array_data = encode_uint256(num)  # array length

    # Element offsets and data
    elem_offsets = b""
    elem_data = b""
    current_elem_offset = num * 32  # past all offset words

    for success, return_data in results:
        elem_offsets += encode_uint256(current_elem_offset)

        # Each element: bool success (32) + bytes returnData (offset=32 + length + padded data)
        elem = encode_bool(success)
        elem += encode_uint256(64)  # offset to returnData from struct start
        elem += encode_uint256(len(return_data))
        padded_len = ((len(return_data) + 31) // 32) * 32
        elem += return_data + b"\x00" * (padded_len - len(return_data))

        elem_data += elem
        current_elem_offset += len(elem)

    full = encode_uint256(array_offset)
    full += array_data
    full += elem_offsets
    full += elem_data

    return "0x" + full.hex()


# ---------------------------------------------------------------------------
# Integration: Existing test_adapters.py compatibility
# ---------------------------------------------------------------------------


class TestExistingTestsCompatibility:
    """Verify the updated adapter still passes the basic property checks
    from test_adapters.py."""

    def test_protocol_name(self) -> None:
        assert AaveAdapter().protocol_name == "aave_v3"

    def test_supported_chains(self) -> None:
        assert AaveAdapter().supported_chains == ["ethereum", "base"]

    def test_registered_in_registry(self) -> None:
        from app.adapters.registry import _REGISTRY

        assert "aave_v3" in _REGISTRY
