"""Tests for wallet service — address validation, chain detection, business rules."""

import pytest

from app.exceptions import BadRequestException
from app.services.wallet import (
    detect_chain,
    validate_address,
    validate_evm_address,
    validate_solana_address,
)

# Well-known EVM checksummed addresses (EIP-55)
VITALIK_CHECKSUMMED = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
ALL_LOWER = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
# An address that is mixed-case but NOT valid EIP-55
BAD_CHECKSUM = "0xD8DA6BF26964af9d7EED9E03e53415D37aa96045"


class TestValidateEvmAddress:
    """EVM address validation tests."""

    def test_valid_checksummed_address(self) -> None:
        result = validate_evm_address(VITALIK_CHECKSUMMED)
        assert result == VITALIK_CHECKSUMMED

    def test_all_lowercase_normalised_to_checksum(self) -> None:
        result = validate_evm_address(ALL_LOWER)
        assert result == VITALIK_CHECKSUMMED

    def test_all_uppercase_accepted(self) -> None:
        upper = "0x" + ALL_LOWER[2:].upper()
        result = validate_evm_address(upper)
        assert result == VITALIK_CHECKSUMMED

    def test_invalid_checksum_rejected(self) -> None:
        with pytest.raises(BadRequestException, match="checksum mismatch"):
            validate_evm_address(BAD_CHECKSUM)

    def test_too_short_address_rejected(self) -> None:
        with pytest.raises(BadRequestException, match="0x-prefixed"):
            validate_evm_address("0x1234")

    def test_no_0x_prefix_rejected(self) -> None:
        with pytest.raises(BadRequestException, match="0x-prefixed"):
            validate_evm_address("d8da6bf26964af9d7eed9e03e53415d37aa96045")

    def test_non_hex_chars_rejected(self) -> None:
        with pytest.raises(BadRequestException, match="0x-prefixed"):
            validate_evm_address("0xGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG")


class TestValidateSolanaAddress:
    """Solana address validation tests."""

    def test_valid_solana_address(self) -> None:
        addr = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        result = validate_solana_address(addr)
        assert result == addr

    def test_short_solana_address_valid(self) -> None:
        addr = "11111111111111111111111111111111"
        result = validate_solana_address(addr)
        assert result == addr

    def test_too_short_rejected(self) -> None:
        with pytest.raises(BadRequestException, match="base58"):
            validate_solana_address("abc")

    def test_invalid_base58_chars_rejected(self) -> None:
        with pytest.raises(BadRequestException, match="base58"):
            validate_solana_address("0OIl" + "1" * 40)


class TestDetectChain:
    """Chain auto-detection tests."""

    def test_evm_address_detects_ethereum(self) -> None:
        assert detect_chain(ALL_LOWER) == "ethereum"

    def test_solana_address_detects_solana(self) -> None:
        assert detect_chain("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM") == "solana"

    def test_ambiguous_address_raises(self) -> None:
        with pytest.raises(BadRequestException, match="Cannot auto-detect"):
            detect_chain("not-a-valid-address!!!")


class TestValidateAddress:
    """Cross-chain address validation dispatch tests."""

    def test_ethereum_chain(self) -> None:
        result = validate_address(ALL_LOWER, "ethereum")
        assert result == VITALIK_CHECKSUMMED

    def test_base_chain_uses_evm_validation(self) -> None:
        result = validate_address(ALL_LOWER, "base")
        assert result == VITALIK_CHECKSUMMED

    def test_solana_chain(self) -> None:
        addr = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        assert validate_address(addr, "solana") == addr

    def test_unsupported_chain_raises(self) -> None:
        with pytest.raises(BadRequestException, match="Unsupported chain"):
            validate_address(ALL_LOWER, "polygon")
