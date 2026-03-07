"""Morpho Blue / MetaMorpho protocol adapter (§9).

Live state: Morpho GraphQL API (zero RPC cost).
Historical events: HyperSync — Deposit, Withdraw, Supply, Borrow, Repay.

References: §9 (Data Sources), §10 (System Architecture).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import hypersync

from app.adapters.base import BaseProtocolAdapter
from app.adapters.registry import register_adapter
from app.schemas.adapter import RawEvent, RawPosition, VaultMetricsData
from app.services.hypersync_client import (
    PROTOCOL_EVENT_TOPICS,
    get_chain_height,
    get_hypersync_client,
)

MORPHO_GRAPHQL_URL = "https://api.morpho.org/graphql"

CHAIN_ID_MAP: dict[str, int] = {
    "ethereum": 1,
    "base": 8453,
}

MORPHO_EVENT_TOPIC0S: list[str] = [
    PROTOCOL_EVENT_TOPICS["morpho_supply"],
    PROTOCOL_EVENT_TOPICS["morpho_withdraw"],
    PROTOCOL_EVENT_TOPICS["morpho_borrow"],
    PROTOCOL_EVENT_TOPICS["morpho_repay"],
    PROTOCOL_EVENT_TOPICS["erc4626_deposit"],
    PROTOCOL_EVENT_TOPICS["erc4626_withdraw"],
]

_TOPIC0_TO_ACTION: dict[str, str] = {
    PROTOCOL_EVENT_TOPICS["morpho_supply"]: "deposit",
    PROTOCOL_EVENT_TOPICS["morpho_withdraw"]: "withdraw",
    PROTOCOL_EVENT_TOPICS["morpho_borrow"]: "borrow",
    PROTOCOL_EVENT_TOPICS["morpho_repay"]: "repay",
    PROTOCOL_EVENT_TOPICS["erc4626_deposit"]: "deposit",
    PROTOCOL_EVENT_TOPICS["erc4626_withdraw"]: "withdraw",
}

# ---------------------------------------------------------------------------
# GraphQL query templates
# ---------------------------------------------------------------------------

_VAULTS_QUERY = """
query FetchVaults($chainIds: [Int!]!) {
  vaults(
    first: 1000
    orderBy: TotalAssetsUsd
    orderDirection: Desc
    where: { chainId_in: $chainIds }
  ) {
    items {
      address
      name
      symbol
      asset {
        address
        symbol
        decimals
      }
      chain { id }
      state {
        totalAssetsUsd
        totalAssets
        fee
        apy
        netApy
        curator
      }
    }
  }
}
"""

_MARKETS_QUERY = """
query FetchMarkets($chainIds: [Int!]!) {
  markets(
    first: 1000
    orderBy: SupplyAssetsUsd
    orderDirection: Desc
    where: { chainId_in: $chainIds }
  ) {
    items {
      uniqueKey
      loanAsset {
        address
        symbol
        decimals
      }
      collateralAsset {
        address
        symbol
      }
      state {
        supplyAssetsUsd
        borrowAssetsUsd
        supplyApy
        borrowApy
        fee
        utilization
      }
    }
  }
}
"""

_USER_POSITIONS_QUERY = """
query FetchUserPositions($address: String!, $chainId: Int!) {
  userByAddress(address: $address, chainId: $chainId) {
    address
    vaultPositions {
      vault {
        address
        name
      }
      assets
      assetsUsd
      shares
    }
    marketPositions {
      market {
        uniqueKey
        loanAsset {
          address
          symbol
        }
        collateralAsset {
          address
          symbol
        }
      }
      supplyAssets
      supplyAssetsUsd
      borrowAssets
      borrowAssetsUsd
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class MorphoGraphQLError(Exception):
    """Raised when the Morpho GraphQL API returns errors."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        messages = [e.get("message", str(e)) for e in errors]
        super().__init__(f"Morpho GraphQL errors: {'; '.join(messages)}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_decimal(value: Any) -> Decimal | None:
    """Convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _pad_address(address: str) -> str:
    """Pad a 20-byte EVM address to a 32-byte topic value."""
    addr = address.lower().removeprefix("0x")
    return "0x" + addr.zfill(64)


def _unpad_address(topic: str) -> str:
    """Extract a 20-byte address from a 32-byte padded topic."""
    clean = topic.removeprefix("0x").lower()
    return "0x" + clean[-40:]


def _decode_uint256(hex_str: str, offset: int = 0) -> int:
    """Decode a uint256 from hex data at the given 32-byte word offset."""
    start = offset * 64
    end = start + 64
    clean = hex_str.removeprefix("0x")
    if len(clean) < end:
        return 0
    return int(clean[start:end], 16)


def _amount_to_decimal(raw_amount: int, decimals: int = 18) -> Decimal:
    """Convert a raw integer token amount to a Decimal."""
    return Decimal(raw_amount) / Decimal(10**decimals)


def _parse_topics(raw: Any) -> list[str]:
    """Parse HyperSync Log.topics into a list of hex strings."""
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
        return [stripped] if stripped else []
    return []


# ---------------------------------------------------------------------------
# Adapter implementation
# ---------------------------------------------------------------------------


@register_adapter
class MorphoAdapter(BaseProtocolAdapter):
    """Morpho Blue / MetaMorpho adapter.

    Live state via Morpho GraphQL API. Historical events via HyperSync.
    Supports Ethereum and Base chains.
    """

    @property
    def protocol_name(self) -> str:
        return "morpho"

    @property
    def supported_chains(self) -> list[str]:
        return ["ethereum", "base"]

    # -- GraphQL helper -------------------------------------------------------

    async def _graphql_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the Morpho API with retry."""
        http = await self._get_http()
        payload = {"query": query, "variables": variables or {}}

        resp: httpx.Response = await self._retry_with_backoff(
            lambda: http.post(MORPHO_GRAPHQL_URL, json=payload),
        )
        resp.raise_for_status()

        data: dict[str, Any] = resp.json()
        if "errors" in data:
            raise MorphoGraphQLError(data["errors"])
        return data.get("data", {})

    # -- fetch_live_metrics ---------------------------------------------------

    async def fetch_live_metrics(
        self,
        vault_addresses: list[str],
        chain: str,
    ) -> list[VaultMetricsData]:
        """Fetch live metrics from Morpho GraphQL for vaults and markets."""
        chain_id = CHAIN_ID_MAP.get(chain.lower())
        if chain_id is None:
            self._log.warning("morpho_unsupported_chain", chain=chain)
            return []

        now = datetime.now(UTC)
        results: list[VaultMetricsData] = []
        address_filter = {a.lower() for a in vault_addresses} if vault_addresses else None

        vault_metrics = await self._fetch_vault_metrics(
            chain_id,
            now,
            address_filter,
        )
        results.extend(vault_metrics)

        market_metrics = await self._fetch_market_metrics(
            chain_id,
            now,
            address_filter,
        )
        results.extend(market_metrics)

        self._log.info(
            "morpho_live_metrics_fetched",
            chain=chain,
            vaults=len(vault_metrics),
            markets=len(market_metrics),
        )
        return results

    async def _fetch_vault_metrics(
        self,
        chain_id: int,
        now: datetime,
        address_filter: set[str] | None,
    ) -> list[VaultMetricsData]:
        """Query MetaMorpho vaults from the GraphQL API."""
        try:
            data = await self._graphql_query(
                _VAULTS_QUERY,
                {"chainIds": [chain_id]},
            )
        except (MorphoGraphQLError, httpx.HTTPError) as exc:
            self._log.error("morpho_vaults_query_failed", error=str(exc))
            return []

        items = (data.get("vaults") or {}).get("items") or []
        chain_name = next(
            (k for k, v in CHAIN_ID_MAP.items() if v == chain_id),
            "ethereum",
        )
        results: list[VaultMetricsData] = []

        for item in items:
            address = (item.get("address") or "").lower()
            if not address:
                continue
            if address_filter and address not in address_filter:
                continue

            state = item.get("state") or {}
            asset = item.get("asset") or {}
            decimals = int(asset.get("decimals") or 18)

            apy_gross = _safe_decimal(state.get("apy"))
            net_apy = _safe_decimal(state.get("netApy"))
            fee = _safe_decimal(state.get("fee"))

            raw_total = _safe_decimal(state.get("totalAssets"))
            tvl_native = raw_total / Decimal(10**decimals) if raw_total is not None else None

            results.append(
                VaultMetricsData(
                    vault_id=address,
                    chain=chain_name,
                    protocol="morpho",
                    vault_name=item.get("name"),
                    asset_symbol=asset.get("symbol"),
                    asset_address=((asset.get("address") or "").lower() or None),
                    timestamp=now,
                    apy_gross=apy_gross,
                    net_apy=net_apy,
                    performance_fee_pct=fee,
                    tvl_usd=_safe_decimal(state.get("totalAssetsUsd")),
                    tvl_native=tvl_native,
                    redemption_type="instant",
                )
            )

        return results

    async def _fetch_market_metrics(
        self,
        chain_id: int,
        now: datetime,
        address_filter: set[str] | None,
    ) -> list[VaultMetricsData]:
        """Query Morpho Blue markets from the GraphQL API."""
        try:
            data = await self._graphql_query(
                _MARKETS_QUERY,
                {"chainIds": [chain_id]},
            )
        except (MorphoGraphQLError, httpx.HTTPError) as exc:
            self._log.error("morpho_markets_query_failed", error=str(exc))
            return []

        items = (data.get("markets") or {}).get("items") or []
        chain_name = next(
            (k for k, v in CHAIN_ID_MAP.items() if v == chain_id),
            "ethereum",
        )
        results: list[VaultMetricsData] = []

        for item in items:
            unique_key = (item.get("uniqueKey") or "").lower()
            if not unique_key:
                continue
            if address_filter and unique_key not in address_filter:
                continue

            state = item.get("state") or {}
            loan_asset = item.get("loanAsset") or {}
            collateral = item.get("collateralAsset") or {}
            loan_sym = loan_asset.get("symbol", "?")
            coll_sym = collateral.get("symbol", "?")

            results.append(
                VaultMetricsData(
                    vault_id=unique_key,
                    chain=chain_name,
                    protocol="morpho",
                    vault_name=f"Morpho Blue {loan_sym}/{coll_sym}",
                    asset_symbol=loan_asset.get("symbol"),
                    asset_address=((loan_asset.get("address") or "").lower() or None),
                    timestamp=now,
                    supply_rate=_safe_decimal(state.get("supplyApy")),
                    borrow_rate=_safe_decimal(state.get("borrowApy")),
                    tvl_usd=_safe_decimal(state.get("supplyAssetsUsd")),
                    utilisation_rate=_safe_decimal(state.get("utilization")),
                    performance_fee_pct=_safe_decimal(state.get("fee")),
                )
            )

        return results

    # -- fetch_positions ------------------------------------------------------

    async def fetch_positions(
        self,
        wallet: str,
        chain: str,
    ) -> list[RawPosition]:
        """Fetch current positions for a wallet via Morpho GraphQL API."""
        chain_id = CHAIN_ID_MAP.get(chain.lower())
        if chain_id is None:
            self._log.warning(
                "morpho_unsupported_chain_positions",
                chain=chain,
            )
            return []

        try:
            data = await self._graphql_query(
                _USER_POSITIONS_QUERY,
                {"address": wallet, "chainId": chain_id},
            )
        except (MorphoGraphQLError, httpx.HTTPError) as exc:
            self._log.error("morpho_positions_query_failed", error=str(exc))
            return []

        user = data.get("userByAddress")
        if user is None:
            return []

        results: list[RawPosition] = []

        for vp in user.get("vaultPositions") or []:
            vault = vp.get("vault") or {}
            shares = _safe_decimal(vp.get("shares"))
            if shares is None or shares == 0:
                continue

            results.append(
                RawPosition(
                    wallet_address=wallet,
                    chain=chain.lower(),
                    protocol="morpho",
                    vault_or_market_id=((vault.get("address") or "").lower()),
                    position_type="supply",
                    asset_symbol=None,
                    current_shares_or_amount=shares,
                )
            )

        for mp in user.get("marketPositions") or []:
            market = mp.get("market") or {}
            market_id = (market.get("uniqueKey") or "").lower()
            loan_asset = market.get("loanAsset") or {}

            supply = _safe_decimal(mp.get("supplyAssets"))
            if supply is not None and supply > 0:
                results.append(
                    RawPosition(
                        wallet_address=wallet,
                        chain=chain.lower(),
                        protocol="morpho",
                        vault_or_market_id=market_id,
                        position_type="supply",
                        asset_symbol=loan_asset.get("symbol"),
                        asset_address=((loan_asset.get("address") or "").lower() or None),
                        current_shares_or_amount=supply,
                    )
                )

            borrow = _safe_decimal(mp.get("borrowAssets"))
            if borrow is not None and borrow > 0:
                results.append(
                    RawPosition(
                        wallet_address=wallet,
                        chain=chain.lower(),
                        protocol="morpho",
                        vault_or_market_id=market_id,
                        position_type="borrow",
                        asset_symbol=loan_asset.get("symbol"),
                        asset_address=((loan_asset.get("address") or "").lower() or None),
                        current_shares_or_amount=borrow,
                    )
                )

        self._log.info(
            "morpho_positions_fetched",
            chain=chain,
            wallet=wallet[:10],
            count=len(results),
        )
        return results

    # -- fetch_historical_events via HyperSync --------------------------------

    async def fetch_historical_events(
        self,
        wallet: str,
        chain: str,
        from_block: int,
        to_block: int,
    ) -> list[RawEvent]:
        """Fetch Morpho historical events for a wallet via HyperSync."""
        chain_lower = chain.lower()
        if chain_lower not in CHAIN_ID_MAP:
            self._log.warning(
                "morpho_unsupported_chain_events",
                chain=chain,
            )
            return []

        client = get_hypersync_client(chain_lower)

        if to_block <= 0:
            to_block = await get_chain_height(client)

        padded_wallet = _pad_address(wallet)

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

        log_selections = [
            hypersync.LogSelection(
                topics=[MORPHO_EVENT_TOPIC0S, [padded_wallet]],
            ),
            hypersync.LogSelection(
                topics=[MORPHO_EVENT_TOPIC0S, [], [padded_wallet]],
            ),
            hypersync.LogSelection(
                topics=[MORPHO_EVENT_TOPIC0S, [], [], [padded_wallet]],
            ),
        ]

        query = hypersync.Query(
            from_block=from_block,
            to_block=to_block,
            logs=log_selections,
            field_selection=hypersync.FieldSelection(
                log=log_fields,
                block=block_fields,
            ),
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
            "morpho_hypersync_scan_complete",
            chain=chain_lower,
            wallet=wallet[:10],
            logs_found=len(all_logs),
        )

        events: list[RawEvent] = []
        for log in all_logs:
            try:
                event = self._parse_morpho_log(
                    log,
                    chain_lower,
                    wallet,
                    block_timestamps,
                )
                if event is not None:
                    events.append(event)
            except Exception:
                self._log.debug(
                    "morpho_log_parse_failed",
                    tx_hash=getattr(log, "transaction_hash", None),
                    exc_info=True,
                )

        events.sort(key=lambda e: (e.timestamp, e.block_number or 0))
        return events

    @staticmethod
    def _parse_morpho_log(
        log: Any,
        chain: str,
        wallet_address: str,
        block_timestamps: dict[int, int],
    ) -> RawEvent | None:
        """Parse a HyperSync log into a Morpho-specific RawEvent."""
        topics = _parse_topics(log.topics)
        if not topics:
            return None

        topic0 = topics[0].lower()
        action = _TOPIC0_TO_ACTION.get(topic0)
        if action is None:
            return None

        contract_address = (log.address or "").lower()
        block_number = log.block_number
        tx_hash = log.transaction_hash or ""

        block_ts = block_timestamps.get(block_number or 0)
        if block_ts is None:
            return None
        timestamp = datetime.fromtimestamp(block_ts, tz=UTC)

        data_hex = (log.data or "0x").removeprefix("0x")
        raw_amount = _decode_uint256("0x" + data_hex, 0)
        amount = _amount_to_decimal(raw_amount)

        return RawEvent(
            wallet_address=wallet_address,
            chain=chain,
            protocol="morpho",
            vault_or_market_id=contract_address,
            action=action,
            asset_address=contract_address,
            amount=amount,
            timestamp=timestamp,
            tx_hash=tx_hash,
            block_number=block_number,
        )
