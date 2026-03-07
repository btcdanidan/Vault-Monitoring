"""Aave v3 protocol adapter (§9).

Live state: UiPoolDataProviderV3 via MulticallBatcher.
Historical events: HyperSync — Supply, Withdraw, Borrow, Repay, LiquidationCall.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import hypersync
import structlog

from app.adapters.aave_constants import (
    AAVE_CHAIN_CONFIGS,
    AAVE_EVENT_TOPICS,
    SEL_BALANCE_OF,
    SEL_DECIMALS,
    SEL_GET_RESERVE_DATA,
    SEL_GET_RESERVES_LIST,
    SEL_GET_USER_ACCOUNT_DATA,
    TOPIC_TO_ACTION,
    ray_to_apy,
)
from app.adapters.base import BaseProtocolAdapter
from app.adapters.registry import register_adapter
from app.schemas.adapter import RawEvent, RawPosition, VaultMetricsData
from app.services.hypersync_client import get_chain_height, get_hypersync_client
from app.services.multicall import (
    MulticallBatcher,
    decode_address,
    decode_uint256,
    encode_address,
    encode_function_call,
)

logger = structlog.get_logger(__name__)


def _amount_to_decimal(raw: int, decimals: int) -> Decimal:
    """Convert a raw integer amount to Decimal with the given decimals."""
    if raw == 0:
        return Decimal(0)
    return Decimal(raw) / Decimal(10**decimals)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register_adapter
class AaveAdapter(BaseProtocolAdapter):
    """Aave v3 adapter with Multicall live state and HyperSync historical events."""

    @property
    def protocol_name(self) -> str:
        return "aave_v3"

    @property
    def supported_chains(self) -> list[str]:
        return ["ethereum", "base"]

    # -- Live metrics ----------------------------------------------------------

    async def fetch_live_metrics(
        self,
        vault_addresses: list[str],
        chain: str,
    ) -> list[VaultMetricsData]:
        """Fetch live reserve metrics from Aave v3 via Multicall.

        Uses ``Pool.getReservesList()`` to discover all reserves, then
        ``Pool.getReserveData(asset)`` for each to get rates and token
        addresses.
        """
        cfg = AAVE_CHAIN_CONFIGS.get(chain.lower())
        if cfg is None:
            self._log.warning("aave_unsupported_chain", chain=chain)
            return []

        batcher = MulticallBatcher()
        http = await self._get_http()
        batcher._http_client = http

        reserves_list_idx = batcher.add_call(
            cfg.pool,
            encode_function_call(SEL_GET_RESERVES_LIST),
        )

        results = await self._retry_with_backoff(lambda: batcher.execute(chain))

        success, data = results[reserves_list_idx]
        if not success or len(data) < 64:
            self._log.error("aave_get_reserves_list_failed", chain=chain)
            return []

        reserve_addresses = self._decode_address_array(data)
        if not reserve_addresses:
            return []

        self._log.info(
            "aave_reserves_discovered",
            chain=chain,
            count=len(reserve_addresses),
        )

        if vault_addresses:
            filter_set = {a.lower() for a in vault_addresses}
            reserve_addresses = [a for a in reserve_addresses if a.lower() in filter_set]

        batcher2 = MulticallBatcher()
        batcher2._http_client = http

        reserve_data_indices: dict[str, int] = {}
        for asset in reserve_addresses:
            idx = batcher2.add_call(
                cfg.pool,
                encode_function_call(SEL_GET_RESERVE_DATA, encode_address(asset)),
            )
            reserve_data_indices[asset] = idx

        results2 = await self._retry_with_backoff(lambda: batcher2.execute(chain))

        now = datetime.now(UTC)
        metrics: list[VaultMetricsData] = []

        for asset, idx in reserve_data_indices.items():
            ok, rd = results2[idx]
            if not ok or len(rd) < 320:
                continue

            try:
                m = self._parse_reserve_data(rd, asset, chain, now)
                if m is not None:
                    metrics.append(m)
            except Exception:
                self._log.debug(
                    "aave_parse_reserve_failed",
                    asset=asset[:10],
                    exc_info=True,
                )

        self._log.info("aave_live_metrics_fetched", chain=chain, reserves=len(metrics))
        return metrics

    def _parse_reserve_data(
        self,
        data: bytes,
        asset: str,
        chain: str,
        timestamp: datetime,
    ) -> VaultMetricsData | None:
        """Parse the return data from ``Pool.getReserveData(asset)``.

        Return layout (Aave v3 Pool):
          Word 0: ReserveConfigurationMap (uint256 packed)
          Word 1: liquidityIndex (uint128)
          Word 2: currentLiquidityRate (uint128)
          Word 3: variableBorrowIndex (uint128)
          Word 4: currentVariableBorrowRate (uint128)
          Word 5: currentStableBorrowRate (uint128)
          Word 6: lastUpdateTimestamp (uint40)
          Word 7: id (uint16)
          Word 8: aTokenAddress
          Word 9: stableDebtTokenAddress
          Word 10: variableDebtTokenAddress
          Word 11: interestRateStrategyAddress
          Word 12: accruedToTreasury (uint128)
          Word 13: unbacked (uint128)
          Word 14: isolationModeTotalDebt (uint128)
        """
        liquidity_rate = decode_uint256(data, 2 * 32)
        variable_borrow_rate = decode_uint256(data, 4 * 32)

        supply_apy = ray_to_apy(liquidity_rate)
        borrow_apy = ray_to_apy(variable_borrow_rate)

        vault_id = f"aave_v3_{chain}_{asset.lower()}"

        return VaultMetricsData(
            vault_id=vault_id,
            chain=chain,
            protocol="aave_v3",
            vault_name=f"Aave v3 {chain.title()}",
            asset_address=asset.lower(),
            timestamp=timestamp,
            apy_gross=Decimal(str(round(supply_apy, 4))),
            net_apy=Decimal(str(round(supply_apy, 4))),
            supply_rate=Decimal(str(round(supply_apy, 4))),
            borrow_rate=Decimal(str(round(borrow_apy, 4))),
            redemption_type="instant",
        )

    @staticmethod
    def _decode_address_array(data: bytes) -> list[str]:
        """Decode a dynamic ``address[]`` ABI return value."""
        if len(data) < 64:
            return []
        offset = decode_uint256(data, 0)
        length = decode_uint256(data, offset)
        addresses: list[str] = []
        for i in range(length):
            addr = decode_address(data, offset + 32 + i * 32)
            addresses.append(addr)
        return addresses

    # -- Positions -------------------------------------------------------------

    async def fetch_positions(
        self,
        wallet: str,
        chain: str,
    ) -> list[RawPosition]:
        """Fetch current Aave v3 positions for *wallet* on *chain*.

        1. ``getUserAccountData(wallet)`` for aggregate health factor.
        2. ``getReservesList()`` to enumerate reserves.
        3. ``getReserveData(asset)`` for each to get aToken/debtToken addresses.
        4. ``balanceOf(wallet)`` on each aToken and variableDebtToken.
        5. Filter non-zero balances.
        """
        cfg = AAVE_CHAIN_CONFIGS.get(chain.lower())
        if cfg is None:
            self._log.warning("aave_unsupported_chain", chain=chain)
            return []

        http = await self._get_http()

        # Phase 1: getUserAccountData + getReservesList
        batcher1 = MulticallBatcher()
        batcher1._http_client = http

        acct_idx = batcher1.add_call(
            cfg.pool,
            encode_function_call(SEL_GET_USER_ACCOUNT_DATA, encode_address(wallet)),
        )
        reserves_idx = batcher1.add_call(
            cfg.pool,
            encode_function_call(SEL_GET_RESERVES_LIST),
        )

        results1 = await self._retry_with_backoff(lambda: batcher1.execute(chain))

        health_factor: Decimal | None = None
        acct_ok, acct_data = results1[acct_idx]
        if acct_ok and len(acct_data) >= 192:
            hf_raw = decode_uint256(acct_data, 5 * 32)
            if hf_raw > 0:
                health_factor = Decimal(hf_raw) / Decimal(10**18)

        res_ok, res_data = results1[reserves_idx]
        if not res_ok or len(res_data) < 64:
            self._log.warning("aave_reserves_list_failed", chain=chain)
            return []

        reserve_addresses = self._decode_address_array(res_data)
        if not reserve_addresses:
            return []

        # Phase 2: getReserveData for each reserve
        batcher2 = MulticallBatcher()
        batcher2._http_client = http

        rd_indices: dict[str, int] = {}
        for asset in reserve_addresses:
            idx = batcher2.add_call(
                cfg.pool,
                encode_function_call(SEL_GET_RESERVE_DATA, encode_address(asset)),
            )
            rd_indices[asset] = idx

        results2 = await self._retry_with_backoff(lambda: batcher2.execute(chain))

        token_info: list[tuple[str, str, str]] = []  # (asset, aToken, debtToken)
        for asset, idx in rd_indices.items():
            ok, rd = results2[idx]
            if not ok or len(rd) < 352:
                continue
            a_token = decode_address(rd, 8 * 32)
            debt_token = decode_address(rd, 10 * 32)
            token_info.append((asset, a_token, debt_token))

        if not token_info:
            return []

        # Phase 3: balanceOf + decimals
        batcher3 = MulticallBatcher()
        batcher3._http_client = http

        balance_indices: list[tuple[str, str, int, int, str]] = []
        for asset, a_token, debt_token in token_info:
            a_bal_idx = batcher3.add_call(
                a_token,
                encode_function_call(SEL_BALANCE_OF, encode_address(wallet)),
            )
            d_bal_idx = batcher3.add_call(
                debt_token,
                encode_function_call(SEL_BALANCE_OF, encode_address(wallet)),
            )
            dec_idx = batcher3.add_call(
                asset,
                encode_function_call(SEL_DECIMALS),
            )
            balance_indices.append((asset, "supply", a_bal_idx, dec_idx, a_token))
            balance_indices.append((asset, "borrow", d_bal_idx, dec_idx, debt_token))

        results3 = await self._retry_with_backoff(lambda: batcher3.execute(chain))

        positions: list[RawPosition] = []
        seen_decimals: dict[int, int] = {}

        for asset, pos_type, bal_idx, dec_idx, _token in balance_indices:
            bal_ok, bal_data = results3[bal_idx]
            if not bal_ok or len(bal_data) < 32:
                continue

            raw_balance = decode_uint256(bal_data, 0)
            if raw_balance == 0:
                continue

            if dec_idx not in seen_decimals:
                dec_ok, dec_data = results3[dec_idx]
                if dec_ok and len(dec_data) >= 32:
                    seen_decimals[dec_idx] = decode_uint256(dec_data, 0)
                else:
                    seen_decimals[dec_idx] = 18
            decimals = seen_decimals[dec_idx]

            amount = _amount_to_decimal(raw_balance, decimals)
            vault_id = f"aave_v3_{chain}_{asset.lower()}"

            positions.append(
                RawPosition(
                    wallet_address=wallet,
                    chain=chain,
                    protocol="aave_v3",
                    vault_or_market_id=vault_id,
                    position_type=pos_type,  # type: ignore[arg-type]
                    asset_address=asset.lower(),
                    current_shares_or_amount=amount,
                    health_factor=health_factor,
                )
            )

        self._log.info(
            "aave_positions_fetched",
            chain=chain,
            wallet=wallet[:10],
            count=len(positions),
        )
        return positions

    # -- Historical events -----------------------------------------------------

    async def fetch_historical_events(
        self,
        wallet: str,
        chain: str,
        from_block: int,
        to_block: int,
    ) -> list[RawEvent]:
        """Fetch historical Aave v3 events for *wallet* via HyperSync.

        Queries the Aave Pool contract for Supply, Withdraw, Borrow, Repay,
        and LiquidationCall events, filtering for the wallet in topic positions.
        """
        cfg = AAVE_CHAIN_CONFIGS.get(chain.lower())
        if cfg is None:
            self._log.warning("aave_unsupported_chain", chain=chain)
            return []

        client = get_hypersync_client(chain)

        if to_block <= 0:
            to_block = await get_chain_height(client)

        pool_address = cfg.pool.lower()
        padded_wallet = _pad_address(wallet)

        log_selections = [
            hypersync.LogSelection(
                address=[pool_address],
                topics=[AAVE_EVENT_TOPICS, [], [padded_wallet]],
            ),
            hypersync.LogSelection(
                address=[pool_address],
                topics=[AAVE_EVENT_TOPICS, [], [], [padded_wallet]],
            ),
        ]

        field_selection = hypersync.FieldSelection(
            log=[
                hypersync.LogField.BLOCK_NUMBER,
                hypersync.LogField.TRANSACTION_HASH,
                hypersync.LogField.ADDRESS,
                hypersync.LogField.TOPIC0,
                hypersync.LogField.TOPIC1,
                hypersync.LogField.TOPIC2,
                hypersync.LogField.TOPIC3,
                hypersync.LogField.DATA,
                hypersync.LogField.LOG_INDEX,
            ],
            block=[
                hypersync.BlockField.NUMBER,
                hypersync.BlockField.TIMESTAMP,
            ],
        )

        query = hypersync.Query(
            from_block=from_block,
            to_block=to_block,
            logs=log_selections,
            field_selection=field_selection,
            include_all_blocks=True,
        )

        all_logs: list[Any] = []
        block_timestamps: dict[int, int] = {}

        while True:
            res = await client.get(query)

            for block in res.data.blocks:
                if block.number is not None and block.timestamp is not None:
                    block_timestamps[block.number] = block.timestamp

            all_logs.extend(res.data.logs)

            if res.next_block >= to_block or res.next_block >= (res.archive_height or 0):
                break
            query.from_block = res.next_block

        self._log.info(
            "aave_hypersync_scan_complete",
            chain=chain,
            wallet=wallet[:10],
            logs_found=len(all_logs),
        )

        events: list[RawEvent] = []
        for log in all_logs:
            try:
                event = self._parse_aave_log(log, chain, wallet, block_timestamps)
                if event is not None:
                    events.append(event)
            except Exception:
                self._log.debug(
                    "aave_log_parse_failed",
                    tx_hash=getattr(log, "transaction_hash", None),
                    exc_info=True,
                )

        events.sort(key=lambda e: (e.timestamp, e.block_number or 0))
        return events

    def _parse_aave_log(
        self,
        log: Any,
        chain: str,
        wallet: str,
        block_timestamps: dict[int, int],
    ) -> RawEvent | None:
        """Parse a single HyperSync log from the Aave Pool contract."""
        topics = _parse_topics(log.topics)
        if not topics:
            return None

        topic0 = topics[0].lower()
        action = TOPIC_TO_ACTION.get(topic0)
        if action is None:
            return None

        block_number = log.block_number
        tx_hash = log.transaction_hash or ""

        block_ts = block_timestamps.get(block_number or 0)
        if block_ts is None:
            return None
        timestamp = datetime.fromtimestamp(block_ts, tz=UTC)

        data_hex = (log.data or "0x").removeprefix("0x")

        # All Aave v3 Pool events encode the reserve/asset address as the
        # first indexed topic (topic1) and the amount in the data section.
        asset_address = _unpad_address(topics[1]) if len(topics) > 1 else ""

        raw_amount = _decode_hex_uint256(data_hex, 0) if data_hex else 0
        amount = Decimal(raw_amount) / Decimal(10**18)

        vault_id = f"aave_v3_{chain}_{asset_address.lower()}"

        return RawEvent(
            wallet_address=wallet,
            chain=chain,
            protocol="aave_v3",
            vault_or_market_id=vault_id,
            action=action,
            asset_address=asset_address.lower(),
            amount=amount,
            timestamp=timestamp,
            tx_hash=tx_hash,
            block_number=block_number,
        )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _pad_address(address: str) -> str:
    """Pad a 20-byte EVM address to a 32-byte topic value."""
    addr = address.lower().removeprefix("0x")
    return "0x" + addr.zfill(64)


def _unpad_address(topic: str) -> str:
    """Extract a 20-byte address from a 32-byte padded topic."""
    clean = topic.removeprefix("0x").lower()
    return "0x" + clean[-40:]


def _decode_hex_uint256(hex_str: str, offset: int = 0) -> int:
    """Decode a uint256 from hex data at the given 32-byte word offset."""
    start = offset * 64
    end = start + 64
    clean = hex_str.removeprefix("0x")
    if len(clean) < end:
        return 0
    return int(clean[start:end], 16)


def _parse_topics(raw: Any) -> list[str]:
    """Parse log topics into a list of hex strings.

    Mirrors the pattern from ``event_scanner._parse_topics``.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("["):
            import json

            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                pass
        return [stripped] if stripped else []
    return []
