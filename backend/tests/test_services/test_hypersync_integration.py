"""Integration tests for HyperSync client — live endpoints (§4, §9).

These tests require a valid ENVIO_API_TOKEN and network access.
They are skipped automatically when the token is not set.

Run with: pytest backend/tests/test_services/test_hypersync_integration.py -v
"""

from __future__ import annotations

import os
import time

import pytest

_SKIP = not os.getenv("ENVIO_API_TOKEN")
pytestmark = pytest.mark.skipif(_SKIP, reason="ENVIO_API_TOKEN not set")

# Guard imports behind the skip check so tests can be collected without
# the hypersync native binary being installed in CI.
if not _SKIP:
    from app.services.hypersync_client import (
        PROTOCOL_EVENT_TOPICS,
        get_chain_height,
        get_hypersync_client,
        query_events_by_contract,
        query_events_by_topic,
    )

USDT_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Morpho Blue on Ethereum — well-known contract with abundant events
MORPHO_BLUE_ADDRESS = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

# Block range with known USDT activity (Ethereum block ~17,000,000–17,000,050)
KNOWN_FROM_BLOCK = 17_000_000
KNOWN_TO_BLOCK = 17_000_050


class TestConnectionEthereum:
    """Verify HyperSync connection to Ethereum mainnet."""

    async def test_get_height_returns_positive(self) -> None:
        client = get_hypersync_client("ethereum")
        height = await get_chain_height(client)
        assert height > 0, "Ethereum chain height should be positive"
        assert height > 20_000_000, "Ethereum should be well past block 20M"


class TestConnectionBase:
    """Verify HyperSync connection to Base."""

    async def test_get_height_returns_positive(self) -> None:
        client = get_hypersync_client("base")
        height = await get_chain_height(client)
        assert height > 0, "Base chain height should be positive"


class TestFilterByContractAddress:
    """Query logs from a specific contract address."""

    async def test_usdt_logs_returned(self) -> None:
        client = get_hypersync_client("ethereum")
        logs = await query_events_by_contract(
            client, USDT_ADDRESS, KNOWN_FROM_BLOCK, KNOWN_TO_BLOCK
        )
        assert len(logs) > 0, "USDT should have events in this block range"

    async def test_logs_have_correct_address(self) -> None:
        client = get_hypersync_client("ethereum")
        logs = await query_events_by_contract(
            client, USDT_ADDRESS, KNOWN_FROM_BLOCK, KNOWN_TO_BLOCK
        )
        for log in logs[:10]:
            addr = getattr(log, "address", None)
            if addr is not None:
                assert addr.lower() == USDT_ADDRESS.lower()


class TestFilterByEventTopic:
    """Query logs filtered by event topic (Transfer)."""

    async def test_transfer_events_returned(self) -> None:
        client = get_hypersync_client("ethereum")
        logs = await query_events_by_topic(
            client, USDT_ADDRESS, ERC20_TRANSFER_TOPIC,
            KNOWN_FROM_BLOCK, KNOWN_TO_BLOCK,
        )
        assert len(logs) > 0, "USDT Transfers should exist in this range"

    async def test_topic0_matches(self) -> None:
        client = get_hypersync_client("ethereum")
        logs = await query_events_by_topic(
            client, USDT_ADDRESS, ERC20_TRANSFER_TOPIC,
            KNOWN_FROM_BLOCK, KNOWN_TO_BLOCK,
        )
        for log in logs[:10]:
            topics = getattr(log, "topics", None)
            if topics and len(topics) > 0:
                assert topics[0].lower() == ERC20_TRANSFER_TOPIC.lower()


class TestBenchmarkBulkRetrieval:
    """Benchmark query speed for bulk historical event retrieval."""

    async def test_bulk_query_completes_in_time(self) -> None:
        """Query ~1000 blocks of Morpho events; should complete in <30s."""
        client = get_hypersync_client("ethereum")
        height = await get_chain_height(client)
        from_block = height - 1000
        to_block = height

        start = time.monotonic()
        logs = await query_events_by_contract(
            client, MORPHO_BLUE_ADDRESS, from_block, to_block
        )
        elapsed = time.monotonic() - start

        blocks_per_sec = 1000 / elapsed if elapsed > 0 else float("inf")
        print(
            f"\n  Benchmark: {len(logs)} logs from {from_block}-{to_block} "
            f"in {elapsed:.2f}s ({blocks_per_sec:.0f} blocks/s)"
        )

        assert elapsed < 30.0, f"Bulk query took {elapsed:.1f}s (limit: 30s)"

    async def test_protocol_event_topic_query(self) -> None:
        """Query using a protocol-specific topic constant."""
        topic = PROTOCOL_EVENT_TOPICS["erc20_transfer"]
        client = get_hypersync_client("ethereum")

        start = time.monotonic()
        logs = await query_events_by_topic(
            client, USDT_ADDRESS, topic,
            KNOWN_FROM_BLOCK, KNOWN_FROM_BLOCK + 100,
        )
        elapsed = time.monotonic() - start

        print(f"\n  Topic query: {len(logs)} logs in {elapsed:.2f}s")
        assert elapsed < 15.0
