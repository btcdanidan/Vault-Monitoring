"""Tests for Helius API client (§4, §9)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.helius_client import (
    HELIUS_CREDIT_USAGE_KEY_PREFIX,
    HELIUS_CREDIT_USAGE_TTL_SECONDS,
    HELIUS_FREE_TIER_CREDITS,
    SIGNATURES_MAX_LIMIT,
    AssetDisplayOptions,
    HeliusApiError,
    HeliusRateLimitError,
    HeliusUnavailableError,
    SignatureInfo,
    get_all_signatures_for_address,
    get_assets_by_owner,
    get_helius_rpc_url,
    get_signatures_for_address,
    get_transaction,
    track_helius_credits,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_ADDRESS = "86xCnPeV69n6t3DnyGvkKobf9FdN2H9oiVDdaMpo2MMY"
SAMPLE_SIGNATURE = (
    "5h6xBEauJ3PK6SWCZ1PGjBvj8vDdWG3KpwATGy1ARAX"
    "FSDwt8GFXM7W5Ncn16wmqokgpiKRLuS83KUxyZyv2sUYv"
)


def _mock_settings(
    api_key: str = "test-helius-key",
    base_url: str = "https://mainnet.helius-rpc.com",
) -> MagicMock:
    s = MagicMock()
    s.helius_api_key = api_key
    s.helius_base_url = base_url
    return s


def _jsonrpc_response(result: object, status_code: int = 200) -> httpx.Response:
    """Build a mock httpx.Response with JSON-RPC payload."""
    import json

    body = json.dumps({"jsonrpc": "2.0", "id": "1", "result": result})
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers={"content-type": "application/json"},
    )


def _jsonrpc_error_response(code: int, message: str, status_code: int = 200) -> httpx.Response:
    import json

    body = json.dumps(
        {"jsonrpc": "2.0", "id": "1", "error": {"code": code, "message": message}},
    )
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers={"content-type": "application/json"},
    )


def _http_error_response(status_code: int, text: str = "error") -> httpx.Response:
    return httpx.Response(status_code=status_code, content=text)


# ---------------------------------------------------------------------------
# TestHeliusConfig
# ---------------------------------------------------------------------------


class TestHeliusConfig:
    """Configuration and URL construction."""

    def test_free_tier_credits_constant(self) -> None:
        assert HELIUS_FREE_TIER_CREDITS == 1_000_000

    def test_signatures_max_limit(self) -> None:
        assert SIGNATURES_MAX_LIMIT == 1_000

    @patch.dict(os.environ, {"HELIUS_API_KEY": "test-helius-key", "HELIUS_BASE_URL": "https://mainnet.helius-rpc.com"})
    def test_rpc_url_construction(self) -> None:
        url = get_helius_rpc_url()
        assert url == "https://mainnet.helius-rpc.com/?api-key=test-helius-key"

    @patch.dict(os.environ, {"HELIUS_API_KEY": "test-helius-key", "HELIUS_BASE_URL": "https://devnet.helius-rpc.com"})
    def test_rpc_url_custom_base(self) -> None:
        url = get_helius_rpc_url()
        assert url == "https://devnet.helius-rpc.com/?api-key=test-helius-key"

    @patch.dict(os.environ, {"HELIUS_API_KEY": ""}, clear=False)
    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="HELIUS_API_KEY is not configured"):
            get_helius_rpc_url()


# ---------------------------------------------------------------------------
# TestGetSignaturesForAddress
# ---------------------------------------------------------------------------


class TestGetSignaturesForAddress:
    """getSignaturesForAddress wrapper."""

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_returns_parsed_signatures(self, _mock_url: MagicMock) -> None:
        raw = [
            {
                "signature": SAMPLE_SIGNATURE,
                "slot": 114,
                "blockTime": 1700000000,
                "err": None,
                "memo": None,
                "confirmationStatus": "finalized",
            }
        ]
        response = _jsonrpc_response(raw)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_signatures_for_address(SAMPLE_ADDRESS, limit=10)

        assert len(result.signatures) == 1
        sig = result.signatures[0]
        assert sig.signature == SAMPLE_SIGNATURE
        assert sig.slot == 114
        assert sig.block_time == 1700000000
        assert sig.err is None
        assert sig.confirmation_status == "finalized"
        assert result.has_more is False

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_has_more_when_full_page(self, _mock_url: MagicMock) -> None:
        raw = [
            {
                "signature": f"sig{i}", "slot": i, "blockTime": None,
                "err": None, "memo": None, "confirmationStatus": "finalized",
            }
            for i in range(5)
        ]
        response = _jsonrpc_response(raw)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_signatures_for_address(SAMPLE_ADDRESS, limit=5)

        assert result.has_more is True
        assert len(result.signatures) == 5

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_empty_result(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response([])

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_signatures_for_address(SAMPLE_ADDRESS)

        assert len(result.signatures) == 0
        assert result.has_more is False

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_clamps_limit(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response([])

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_signatures_for_address(SAMPLE_ADDRESS, limit=9999)

            call_args = mock_client.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body["params"][1]["limit"] == SIGNATURES_MAX_LIMIT

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_passes_before_and_until(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response([])

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_signatures_for_address(SAMPLE_ADDRESS, before="sigA", until="sigB")

            call_args = mock_client.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body["params"][1]["before"] == "sigA"
            assert body["params"][1]["until"] == "sigB"


# ---------------------------------------------------------------------------
# TestGetAllSignaturesForAddress
# ---------------------------------------------------------------------------


class TestGetAllSignaturesForAddress:
    """Auto-paginating wrapper."""

    @patch("app.services.helius_client.get_signatures_for_address")
    async def test_collects_multiple_pages(self, mock_get: AsyncMock) -> None:
        from app.services.helius_client import SignaturesResult

        page1 = SignaturesResult(
            signatures=[
                SignatureInfo(
                    signature=f"sig{i}", slot=i, block_time=None,
                    err=None, memo=None, confirmation_status="finalized",
                )
                for i in range(1000)
            ],
            has_more=True,
        )
        page2 = SignaturesResult(
            signatures=[
                SignatureInfo(
                    signature="sig_last", slot=2000, block_time=None,
                    err=None, memo=None, confirmation_status="finalized",
                ),
            ],
            has_more=False,
        )
        mock_get.side_effect = [page1, page2]

        result = await get_all_signatures_for_address(SAMPLE_ADDRESS)

        assert len(result) == 1001
        assert mock_get.call_count == 2
        second_call_kwargs = mock_get.call_args_list[1].kwargs
        assert second_call_kwargs["before"] == "sig999"

    @patch("app.services.helius_client.get_signatures_for_address")
    async def test_stops_at_max_pages(self, mock_get: AsyncMock) -> None:
        from app.services.helius_client import SignaturesResult

        page = SignaturesResult(
            signatures=[
                SignatureInfo(
                    signature="sig0", slot=1, block_time=None,
                    err=None, memo=None, confirmation_status="finalized",
                ),
            ],
            has_more=True,
        )
        mock_get.return_value = page

        result = await get_all_signatures_for_address(SAMPLE_ADDRESS, max_pages=3)

        assert mock_get.call_count == 3
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TestGetTransaction
# ---------------------------------------------------------------------------


class TestGetTransaction:
    """getTransaction wrapper."""

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_returns_transaction(self, _mock_url: MagicMock) -> None:
        tx_data = {
            "slot": 114,
            "transaction": {"message": {"instructions": []}},
            "meta": {"fee": 5000},
        }
        response = _jsonrpc_response(tx_data)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_transaction(SAMPLE_SIGNATURE)

        assert result is not None
        assert result["slot"] == 114
        assert result["meta"]["fee"] == 5000

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_returns_none_for_missing_tx(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response(None)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_transaction(SAMPLE_SIGNATURE)

        assert result is None

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_sends_json_parsed_encoding(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response(None)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_transaction(SAMPLE_SIGNATURE)

            call_args = mock_client.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body["params"][1]["encoding"] == "jsonParsed"
            assert body["params"][1]["maxSupportedTransactionVersion"] == 0


# ---------------------------------------------------------------------------
# TestGetAssetsByOwner
# ---------------------------------------------------------------------------


class TestGetAssetsByOwner:
    """DAS API getAssetsByOwner wrapper."""

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_returns_assets(self, _mock_url: MagicMock) -> None:
        das_result = {
            "total": 2,
            "limit": 50,
            "page": 1,
            "items": [
                {"id": "token1", "interface": "FungibleToken"},
                {"id": "token2", "interface": "FungibleToken"},
            ],
        }
        response = _jsonrpc_response(das_result)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_assets_by_owner(SAMPLE_ADDRESS, page=1, limit=50)

        assert result["total"] == 2
        assert len(result["items"]) == 2

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_sends_display_options(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response({"items": [], "total": 0, "limit": 50, "page": 1})
        opts = AssetDisplayOptions(show_fungible=True, show_native_balance=True)

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_assets_by_owner(SAMPLE_ADDRESS, display_options=opts)

            call_args = mock_client.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            display = body["params"]["displayOptions"]
            assert display["showFungible"] is True
            assert display["showNativeBalance"] is True

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_empty_result(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_response({"items": [], "total": 0, "limit": 1000, "page": 1})

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_assets_by_owner(SAMPLE_ADDRESS)

        assert result["items"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# TestAssetDisplayOptions
# ---------------------------------------------------------------------------


class TestAssetDisplayOptions:
    """Display options dataclass."""

    def test_defaults(self) -> None:
        opts = AssetDisplayOptions()
        d = opts.to_dict()
        assert d["showFungible"] is True
        assert d["showNativeBalance"] is False
        assert d["showZeroBalance"] is False

    def test_custom_values(self) -> None:
        opts = AssetDisplayOptions(
            show_fungible=False, show_native_balance=True, show_zero_balance=True,
        )
        d = opts.to_dict()
        assert d["showFungible"] is False
        assert d["showNativeBalance"] is True
        assert d["showZeroBalance"] is True

    def test_all_keys_present(self) -> None:
        d = AssetDisplayOptions().to_dict()
        expected_keys = {
            "showFungible", "showNativeBalance", "showZeroBalance",
            "showCollectionMetadata", "showUnverifiedCollections",
            "showGrandTotal", "showInscription",
        }
        assert set(d.keys()) == expected_keys


# ---------------------------------------------------------------------------
# TestTrackHeliusCredits
# ---------------------------------------------------------------------------


class TestTrackHeliusCredits:
    """Redis credit tracking."""

    def test_increments_counter(self) -> None:
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        track_helius_credits(mock_redis, 5)

        mock_pipe.incrby.assert_called_once()
        key_arg = mock_pipe.incrby.call_args[0][0]
        assert key_arg.startswith(HELIUS_CREDIT_USAGE_KEY_PREFIX)
        assert mock_pipe.incrby.call_args[0][1] == 5
        mock_pipe.expire.assert_called_once()
        assert mock_pipe.expire.call_args[0][1] == HELIUS_CREDIT_USAGE_TTL_SECONDS
        mock_pipe.execute.assert_called_once()

    def test_skips_zero_calls(self) -> None:
        mock_redis = MagicMock()
        track_helius_credits(mock_redis, 0)
        mock_redis.pipeline.assert_not_called()

    def test_skips_negative_calls(self) -> None:
        mock_redis = MagicMock()
        track_helius_credits(mock_redis, -3)
        mock_redis.pipeline.assert_not_called()


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """HTTP and RPC error handling."""

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_429_raises_rate_limit(self, _mock_url: MagicMock) -> None:
        response = _http_error_response(429, "rate limited")

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HeliusRateLimitError) as exc_info:
                await get_signatures_for_address(SAMPLE_ADDRESS)

            assert exc_info.value.status_code == 429

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_503_raises_unavailable(self, _mock_url: MagicMock) -> None:
        response = _http_error_response(503, "service unavailable")

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HeliusUnavailableError) as exc_info:
                await get_transaction(SAMPLE_SIGNATURE)

            assert exc_info.value.status_code == 503

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_rpc_error_raises_api_error(self, _mock_url: MagicMock) -> None:
        response = _jsonrpc_error_response(-32602, "Invalid params")

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HeliusApiError) as exc_info:
                await get_signatures_for_address(SAMPLE_ADDRESS)

            assert exc_info.value.rpc_code == -32602
            assert "Invalid params" in str(exc_info.value)

    @patch("app.services.helius_client.get_helius_rpc_url", return_value="https://test.rpc/")
    async def test_generic_4xx_raises_api_error(self, _mock_url: MagicMock) -> None:
        response = _http_error_response(401, "Unauthorized")

        with patch("app.services.helius_client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HeliusApiError) as exc_info:
                await get_assets_by_owner(SAMPLE_ADDRESS)

            assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# TestSignatureInfo
# ---------------------------------------------------------------------------


class TestSignatureInfo:
    """SignatureInfo dataclass."""

    def test_frozen(self) -> None:
        sig = SignatureInfo(
            signature="abc", slot=1, block_time=None,
            err=None, memo=None, confirmation_status="finalized",
        )
        with pytest.raises(AttributeError):
            sig.signature = "xyz"  # type: ignore[misc]

    def test_fields(self) -> None:
        sig = SignatureInfo(
            signature="abc", slot=42, block_time=1700000000,
            err={"code": 1}, memo="test memo", confirmation_status="confirmed",
        )
        assert sig.signature == "abc"
        assert sig.slot == 42
        assert sig.block_time == 1700000000
        assert sig.err == {"code": 1}
        assert sig.memo == "test memo"
        assert sig.confirmation_status == "confirmed"
