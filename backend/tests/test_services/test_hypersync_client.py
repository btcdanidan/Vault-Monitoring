"""Tests for HyperSync client wrapper (§4, §9)."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hypersync_client import (
    HYPERSYNC_CHAINS,
    PROTOCOL_EVENT_TOPICS,
    get_chain_height,
    get_hypersync_client,
    query_events_by_contract,
    query_events_by_topic,
    query_events_paginated,
)


# ---------------------------------------------------------------------------
# Chain configuration
# ---------------------------------------------------------------------------


class TestChainConfig:
    """Verify supported chain configuration."""

    def test_ethereum_configured(self) -> None:
        cfg = HYPERSYNC_CHAINS["ethereum"]
        assert cfg.chain_id == 1
        assert "eth.hypersync.xyz" in cfg.url

    def test_base_configured(self) -> None:
        cfg = HYPERSYNC_CHAINS["base"]
        assert cfg.chain_id == 8453
        assert "base.hypersync.xyz" in cfg.url

    def test_both_chains_present(self) -> None:
        assert "ethereum" in HYPERSYNC_CHAINS
        assert "base" in HYPERSYNC_CHAINS
        assert len(HYPERSYNC_CHAINS) >= 2


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


class TestGetHypersyncClient:
    """Client factory function."""

    @patch("app.services.hypersync_client.hypersync")
    @patch("app.services.hypersync_client.get_settings")
    def test_creates_ethereum_client(
        self, mock_settings: MagicMock, mock_hs: MagicMock
    ) -> None:
        mock_settings.return_value.envio_api_token = "test-token"
        mock_hs.ClientConfig.return_value = "cfg"
        mock_hs.HypersyncClient.return_value = "client"

        result = get_hypersync_client("ethereum")

        mock_hs.ClientConfig.assert_called_once_with(
            url="https://eth.hypersync.xyz", bearer_token="test-token"
        )
        mock_hs.HypersyncClient.assert_called_once_with("cfg")
        assert result == "client"

    @patch("app.services.hypersync_client.hypersync")
    @patch("app.services.hypersync_client.get_settings")
    def test_creates_base_client(
        self, mock_settings: MagicMock, mock_hs: MagicMock
    ) -> None:
        mock_settings.return_value.envio_api_token = "base-token"
        mock_hs.ClientConfig.return_value = "cfg"
        mock_hs.HypersyncClient.return_value = "client"

        result = get_hypersync_client("base")

        mock_hs.ClientConfig.assert_called_once_with(
            url="https://base.hypersync.xyz", bearer_token="base-token"
        )
        assert result == "client"

    @patch("app.services.hypersync_client.hypersync")
    @patch("app.services.hypersync_client.get_settings")
    def test_case_insensitive(
        self, mock_settings: MagicMock, mock_hs: MagicMock
    ) -> None:
        mock_settings.return_value.envio_api_token = ""
        mock_hs.ClientConfig.return_value = "cfg"
        mock_hs.HypersyncClient.return_value = "client"

        get_hypersync_client("Ethereum")
        mock_hs.ClientConfig.assert_called_once_with(
            url="https://eth.hypersync.xyz", bearer_token=None
        )

    def test_unknown_chain_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported chain"):
            get_hypersync_client("solana")

    @patch("app.services.hypersync_client.hypersync")
    @patch("app.services.hypersync_client.get_settings")
    def test_empty_token_passes_none(
        self, mock_settings: MagicMock, mock_hs: MagicMock
    ) -> None:
        mock_settings.return_value.envio_api_token = ""
        mock_hs.ClientConfig.return_value = "cfg"
        mock_hs.HypersyncClient.return_value = "client"

        get_hypersync_client("ethereum")
        mock_hs.ClientConfig.assert_called_once_with(
            url="https://eth.hypersync.xyz", bearer_token=None
        )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _make_response(logs: list, next_block: int, archive_height: int) -> SimpleNamespace:
    """Build a mock HyperSync response."""
    return SimpleNamespace(
        data=SimpleNamespace(logs=logs),
        next_block=next_block,
        archive_height=archive_height,
    )


class TestGetChainHeight:
    """get_chain_height wrapper."""

    async def test_returns_height(self) -> None:
        client = MagicMock()
        client.get_height = AsyncMock(return_value=21_000_000)
        height = await get_chain_height(client)
        assert height == 21_000_000


class TestQueryEventsByContract:
    """query_events_by_contract with preset_query_logs."""

    @patch("app.services.hypersync_client.hypersync")
    async def test_returns_logs(self, mock_hs: MagicMock) -> None:
        mock_query = MagicMock()
        mock_hs.preset_query_logs.return_value = mock_query

        log1 = SimpleNamespace(address="0xabc", topics=["0x123"])
        response = _make_response([log1], next_block=100, archive_height=200)

        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        logs = await query_events_by_contract(client, "0xabc", 50, 100)

        mock_hs.preset_query_logs.assert_called_once_with("0xabc", 50, 100)
        assert len(logs) == 1
        assert logs[0].address == "0xabc"


class TestQueryEventsByTopic:
    """query_events_by_topic with preset_query_logs_of_event."""

    @patch("app.services.hypersync_client.hypersync")
    async def test_returns_filtered_logs(self, mock_hs: MagicMock) -> None:
        topic0 = PROTOCOL_EVENT_TOPICS["erc20_transfer"]
        mock_query = MagicMock()
        mock_hs.preset_query_logs_of_event.return_value = mock_query

        log1 = SimpleNamespace(address="0xdead", topics=[topic0])
        response = _make_response([log1], next_block=200, archive_height=300)

        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        logs = await query_events_by_topic(client, "0xdead", topic0, 100, 200)

        mock_hs.preset_query_logs_of_event.assert_called_once_with(
            "0xdead", topic0, 100, 200
        )
        assert len(logs) == 1


class TestQueryEventsPaginated:
    """Pagination logic in query_events_paginated."""

    async def test_single_page(self) -> None:
        log1 = SimpleNamespace(data="log1")
        response = _make_response([log1], next_block=200, archive_height=500)

        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        query = MagicMock()
        logs = await query_events_paginated(client, query, to_block=200)

        assert len(logs) == 1
        assert client.get.call_count == 1

    async def test_multi_page_collects_all(self) -> None:
        log_a = SimpleNamespace(data="a")
        log_b = SimpleNamespace(data="b")
        log_c = SimpleNamespace(data="c")

        page1 = _make_response([log_a, log_b], next_block=150, archive_height=500)
        page2 = _make_response([log_c], next_block=200, archive_height=500)

        client = MagicMock()
        client.get = AsyncMock(side_effect=[page1, page2])

        query = MagicMock()
        logs = await query_events_paginated(client, query, to_block=200)

        assert len(logs) == 3
        assert client.get.call_count == 2
        assert query.from_block == 150

    async def test_stops_at_archive_height(self) -> None:
        log1 = SimpleNamespace(data="x")
        response = _make_response([log1], next_block=180, archive_height=180)

        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        query = MagicMock()
        logs = await query_events_paginated(client, query, to_block=200)

        assert len(logs) == 1
        assert client.get.call_count == 1

    async def test_empty_response(self) -> None:
        response = _make_response([], next_block=200, archive_height=500)

        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        query = MagicMock()
        logs = await query_events_paginated(client, query, to_block=200)

        assert logs == []


# ---------------------------------------------------------------------------
# Protocol event topics
# ---------------------------------------------------------------------------

_HEX_TOPIC_RE = re.compile(r"^0x[0-9a-f]{64}$")


class TestProtocolEventTopics:
    """Validate PROTOCOL_EVENT_TOPICS constants."""

    def test_topics_not_empty(self) -> None:
        assert len(PROTOCOL_EVENT_TOPICS) > 0

    @pytest.mark.parametrize("name,topic", list(PROTOCOL_EVENT_TOPICS.items()))
    def test_topic_is_valid_hex(self, name: str, topic: str) -> None:
        assert _HEX_TOPIC_RE.match(topic), f"{name}: {topic!r} is not valid topic0 hex"

    def test_erc20_transfer_present(self) -> None:
        assert "erc20_transfer" in PROTOCOL_EVENT_TOPICS

    def test_aave_events_present(self) -> None:
        for key in ("aave_supply", "aave_withdraw", "aave_borrow", "aave_repay"):
            assert key in PROTOCOL_EVENT_TOPICS

    def test_morpho_events_present(self) -> None:
        for key in ("morpho_supply", "morpho_withdraw", "morpho_borrow", "morpho_repay"):
            assert key in PROTOCOL_EVENT_TOPICS

    def test_erc4626_events_present(self) -> None:
        for key in ("erc4626_deposit", "erc4626_withdraw"):
            assert key in PROTOCOL_EVENT_TOPICS
