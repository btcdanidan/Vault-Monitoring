"""HyperSync Python client wrapper for EVM historical event indexing.

Provides chain-aware client factory, query helpers with automatic pagination,
and protocol-specific event topic constants for Morpho, Aave v3, Pendle,
Euler v2, and common ERC-4626/ERC-20 events.

References: §4 (Tech Stack), §9 (Data Sources).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import os

import hypersync
import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Chain configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainConfig:
    """HyperSync endpoint configuration for an EVM chain."""

    chain_id: int
    url: str


HYPERSYNC_CHAINS: dict[str, ChainConfig] = {
    "ethereum": ChainConfig(chain_id=1, url="https://eth.hypersync.xyz"),
    "base": ChainConfig(chain_id=8453, url="https://base.hypersync.xyz"),
}


# ---------------------------------------------------------------------------
# Protocol-specific event topic0 constants (keccak256 of event signatures)
# ---------------------------------------------------------------------------

PROTOCOL_EVENT_TOPICS: dict[str, str] = {
    # ERC-20
    "erc20_transfer": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "erc20_approval": "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
    # ERC-4626 (Morpho vaults, Yearn v3, Euler eVaults)
    "erc4626_deposit": "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7",
    "erc4626_withdraw": "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db",
    # Aave v3 Pool
    "aave_supply": "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61",
    "aave_withdraw": "0x3115d1449a7b732c986cba18244e897a145df0b3b24b3ae1e1e35d8d3a3cc678",
    "aave_borrow": "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0",
    "aave_repay": "0xa534c8dbe71f871f9f3f77571f15f067af254a42571f132a7e0b817e4523ae96",
    "aave_liquidation": "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286",
    # Morpho Blue core
    "morpho_supply": "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d711e0d3a0082022",
    "morpho_withdraw": "0xa56fc0ad5702ec05ce63666221f796fb62437c32db1aa1aa075fc6484cf58fbf",
    "morpho_borrow": "0x570954540bed6b1304a87dfe815a5eda4a648f7097a16240dcd85c9b5fd42a43",
    "morpho_repay": "0x52acb05cebbd3cd39715469f22afbf5a17496295ef3bc9bb5944056c63ccaa09",
    "morpho_liquidate": "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452f2fe1a21c2d49c0b0069c0c2d1",
    # Pendle
    "pendle_mint": "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f",
    "pendle_burn": "0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496",
    # Euler v2 eVault
    "euler_deposit": "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7",
    "euler_withdraw": "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db",
    "euler_borrow": "0x312a5e5e1079f5dda4e95dbbd0b908b291fd5b992ef22073643f5d1f9e4e0a34",
    "euler_repay": "0x5c16de4f8b59bd9caf0f49a545f25819a895ed242076dfb503b5a17de90e1173",
    # DEX / aggregator swap events (§9 swap detection)
    "lifi_transfer_started": "0xcba69f43792f9f399347f4b4b1e8f90e6b12c43e1e8e3e4e3d3e0e4a2db9c8d0",
    "lifi_generic_swap": "0xd6d4f5681c8f0b8be82c97e04699e703530070e5e2a0414a36b9cb5a6e7c37e0",
    "oneinch_swapped": "0xd6d4f5681c8f0b8be82c97e04699e703530070e5e2a0414a36b9cb5a6e7c37e0",
    "cow_trade": "0xa07a543ab8a018198e99ca0184c93fe9050a79400a0a723441f84de1d972cc17",
    "uniswap_v3_swap": "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",
    "uniswap_v2_swap": "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",
}


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def get_hypersync_client(chain: str) -> hypersync.HypersyncClient:
    """Create a HypersyncClient for the given chain.

    Args:
        chain: Chain name key in ``HYPERSYNC_CHAINS`` (e.g. ``"ethereum"``, ``"base"``).

    Raises:
        ValueError: If *chain* is not a supported chain name.
    """
    chain_lower = chain.lower()
    cfg = HYPERSYNC_CHAINS.get(chain_lower)
    if cfg is None:
        supported = ", ".join(sorted(HYPERSYNC_CHAINS))
        raise ValueError(f"Unsupported chain {chain!r}. Supported: {supported}")

    token = os.getenv("ENVIO_API_TOKEN", "")
    client_config = hypersync.ClientConfig(url=cfg.url, bearer_token=token or None)
    logger.info(
        "hypersync_client_created",
        chain=chain_lower,
        chain_id=cfg.chain_id,
        url=cfg.url,
    )
    return hypersync.HypersyncClient(client_config)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def get_chain_height(client: hypersync.HypersyncClient) -> int:
    """Return the latest indexed block height for the client's chain."""
    height: int = await client.get_height()
    return height


async def query_events_by_contract(
    client: hypersync.HypersyncClient,
    address: str,
    from_block: int,
    to_block: int,
) -> list[hypersync.LogResponse]:
    """Query all log events emitted by *address* in ``[from_block, to_block)``."""
    query = hypersync.preset_query_logs(address, from_block, to_block)
    return await query_events_paginated(client, query, to_block)


async def query_events_by_topic(
    client: hypersync.HypersyncClient,
    address: str,
    topic0: str,
    from_block: int,
    to_block: int,
) -> list[hypersync.LogResponse]:
    """Query logs matching *topic0* from *address* in ``[from_block, to_block)``."""
    query = hypersync.preset_query_logs_of_event(address, topic0, from_block, to_block)
    return await query_events_paginated(client, query, to_block)


async def query_events_paginated(
    client: hypersync.HypersyncClient,
    query: hypersync.Query,
    to_block: int,
) -> list[hypersync.LogResponse]:
    """Execute *query* with automatic pagination, collecting all logs.

    Loops ``client.get(query)`` advancing ``query.from_block`` to
    ``res.next_block`` until we reach *to_block* or the archive height.
    """
    all_logs: list[hypersync.LogResponse] = []
    pages = 0

    while True:
        res = await client.get(query)
        batch = res.data.logs
        all_logs.extend(batch)
        pages += 1

        next_block: int = res.next_block
        archive_height: int = res.archive_height

        logger.debug(
            "hypersync_page_fetched",
            page=pages,
            logs_in_page=len(batch),
            next_block=next_block,
            archive_height=archive_height,
        )

        if next_block >= to_block or next_block >= archive_height:
            break

        query.from_block = next_block

    logger.info(
        "hypersync_query_complete",
        total_logs=len(all_logs),
        pages=pages,
    )
    return all_logs
