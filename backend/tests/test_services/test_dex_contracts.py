"""Tests for DEX contract address registry (§5, §9)."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.dex_contracts import (
    DexContract,
    DexContractsConfig,
    get_all_addresses_for_chain,
    get_contracts_for_chain,
    get_dex_contracts,
    load_dex_contracts,
)

_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

REQUIRED_PROTOCOLS_ETHEREUM = {"lifi", "oneinch", "cow", "uniswap_v2", "uniswap_v3"}
REQUIRED_PROTOCOLS_BASE = {"lifi", "oneinch", "cow", "uniswap_v2", "uniswap_v3"}


# ---------------------------------------------------------------------------
# YAML loading & validation
# ---------------------------------------------------------------------------


class TestLoadDexContracts:
    """Verify the real YAML file loads and validates."""

    def test_loads_successfully(self) -> None:
        config = load_dex_contracts()
        assert isinstance(config, DexContractsConfig)

    def test_has_ethereum_and_base(self) -> None:
        config = load_dex_contracts()
        assert "ethereum" in config.chains
        assert "base" in config.chains

    def test_ethereum_chain_id(self) -> None:
        config = load_dex_contracts()
        assert config.chains["ethereum"].chain_id == 1

    def test_base_chain_id(self) -> None:
        config = load_dex_contracts()
        assert config.chains["base"].chain_id == 8453

    def test_ethereum_has_required_protocols(self) -> None:
        config = load_dex_contracts()
        protocols = {c.protocol for c in config.chains["ethereum"].contracts}
        assert REQUIRED_PROTOCOLS_ETHEREUM.issubset(protocols), (
            f"Missing: {REQUIRED_PROTOCOLS_ETHEREUM - protocols}"
        )

    def test_base_has_required_protocols(self) -> None:
        config = load_dex_contracts()
        protocols = {c.protocol for c in config.chains["base"].contracts}
        assert REQUIRED_PROTOCOLS_BASE.issubset(protocols), (
            f"Missing: {REQUIRED_PROTOCOLS_BASE - protocols}"
        )

    @pytest.mark.parametrize("chain", ["ethereum", "base"])
    def test_all_addresses_valid_hex(self, chain: str) -> None:
        config = load_dex_contracts()
        for contract in config.chains[chain].contracts:
            assert _ETH_ADDRESS_RE.match(contract.address), (
                f"{contract.name}: {contract.address!r} is not a valid address"
            )

    def test_no_duplicate_addresses_per_chain(self) -> None:
        config = load_dex_contracts()
        for chain_name, chain_cfg in config.chains.items():
            addrs = [c.address.lower() for c in chain_cfg.contracts]
            assert len(addrs) == len(set(addrs)), f"Duplicate addresses found on {chain_name}"


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------


class TestDexContractModel:
    """DexContract pydantic model edge cases."""

    def test_valid_contract(self) -> None:
        c = DexContract(
            name="Test",
            address="0x1111111254EEB25477B68fb85Ed929f73A960582",
            protocol="oneinch",
            type="aggregator",
        )
        assert c.name == "Test"

    def test_invalid_address_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Ethereum address"):
            DexContract(
                name="Bad",
                address="not-an-address",
                protocol="test",
                type="router",
            )

    def test_short_address_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Ethereum address"):
            DexContract(
                name="Short",
                address="0x1234",
                protocol="test",
                type="router",
            )

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DexContract(
                name="Bad type",
                address="0x1111111254EEB25477B68fb85Ed929f73A960582",
                protocol="test",
                type="invalid",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestGetContractsForChain:
    """get_contracts_for_chain() lookups."""

    def test_ethereum_returns_contracts(self) -> None:
        contracts = get_contracts_for_chain("ethereum")
        assert len(contracts) >= 7

    def test_base_returns_contracts(self) -> None:
        contracts = get_contracts_for_chain("base")
        assert len(contracts) >= 6

    def test_case_insensitive(self) -> None:
        contracts = get_contracts_for_chain("Ethereum")
        assert len(contracts) >= 7

    def test_unknown_chain_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported chain"):
            get_contracts_for_chain("solana")


class TestGetAllAddressesForChain:
    """get_all_addresses_for_chain() returns lowercase addresses."""

    def test_returns_lowercase(self) -> None:
        addresses = get_all_addresses_for_chain("ethereum")
        for addr in addresses:
            assert addr == addr.lower(), f"Address not lowercase: {addr}"

    def test_all_start_with_0x(self) -> None:
        addresses = get_all_addresses_for_chain("base")
        for addr in addresses:
            assert addr.startswith("0x"), f"Missing 0x prefix: {addr}"

    def test_count_matches_contracts(self) -> None:
        contracts = get_contracts_for_chain("ethereum")
        addresses = get_all_addresses_for_chain("ethereum")
        assert len(addresses) == len(contracts)


# ---------------------------------------------------------------------------
# Malformed YAML
# ---------------------------------------------------------------------------


class TestMalformedYaml:
    """Verify graceful failures for bad input files."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_dex_contracts(tmp_path / "nonexistent.yaml")

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            textwrap.dedent("""\
                chains:
                  ethereum:
                    chain_id: 1
                    contracts:
                      - name: "Missing address"
                        protocol: test
                        type: router
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_dex_contracts(bad_yaml)

    def test_bad_address_in_yaml_raises(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad_addr.yaml"
        bad_yaml.write_text(
            textwrap.dedent("""\
                chains:
                  ethereum:
                    chain_id: 1
                    contracts:
                      - name: "Bad addr"
                        address: "0xZZZ"
                        protocol: test
                        type: router
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError, match="Invalid Ethereum address"):
            load_dex_contracts(bad_yaml)


# ---------------------------------------------------------------------------
# Cached singleton
# ---------------------------------------------------------------------------


class TestCachedSingleton:
    """get_dex_contracts() caches correctly."""

    def test_returns_same_instance(self) -> None:
        get_dex_contracts.cache_clear()
        a = get_dex_contracts()
        b = get_dex_contracts()
        assert a is b
