"""DEX contract address registry loaded from dex_contracts.yaml (§5, §9).

Provides typed access to DEX/aggregator contract addresses per chain.
The YAML file is admin-editable without code changes — restart workers
to pick up modifications.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, field_validator

logger = structlog.get_logger(__name__)

_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

_DEFAULT_YAML_PATH = Path(__file__).resolve().parents[2] / "dex_contracts.yaml"


class DexContract(BaseModel):
    """A single DEX / aggregator contract entry."""

    name: str
    address: str
    protocol: str
    type: Literal["aggregator", "router", "settlement"]

    @field_validator("address")
    @classmethod
    def _validate_address(cls, v: str) -> str:
        if not _ETH_ADDRESS_RE.match(v):
            msg = f"Invalid Ethereum address: {v!r}"
            raise ValueError(msg)
        return v


class ChainContracts(BaseModel):
    """Contract list for a single EVM chain."""

    chain_id: int
    contracts: list[DexContract]


class DexContractsConfig(BaseModel):
    """Top-level config mapping chain names to their contract sets."""

    chains: dict[str, ChainContracts]


def load_dex_contracts(path: Path | None = None) -> DexContractsConfig:
    """Load and validate ``dex_contracts.yaml``.

    Args:
        path: Explicit path to the YAML file.  Falls back to the default
              location next to the ``backend/`` package root.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
        pydantic.ValidationError: If the data doesn't match the schema.
    """
    resolved = path or _DEFAULT_YAML_PATH
    logger.info("dex_contracts_loading", path=str(resolved))

    raw = resolved.read_text(encoding="utf-8")
    data: dict = yaml.safe_load(raw)  # type: ignore[assignment]

    config = DexContractsConfig.model_validate(data)
    total = sum(len(c.contracts) for c in config.chains.values())
    logger.info(
        "dex_contracts_loaded",
        chains=list(config.chains.keys()),
        total_contracts=total,
    )
    return config


@lru_cache
def get_dex_contracts() -> DexContractsConfig:
    """Cached singleton — loads from the default YAML path once."""
    return load_dex_contracts()


def get_contracts_for_chain(chain: str) -> list[DexContract]:
    """Return the contract list for *chain* (case-insensitive).

    Raises:
        ValueError: If *chain* is not present in the config.
    """
    cfg = get_dex_contracts()
    chain_lower = chain.lower()
    chain_cfg = cfg.chains.get(chain_lower)
    if chain_cfg is None:
        supported = ", ".join(sorted(cfg.chains))
        raise ValueError(f"Unsupported chain {chain!r}. Supported: {supported}")
    return chain_cfg.contracts


def get_all_addresses_for_chain(chain: str) -> list[str]:
    """Return a flat list of **lowercase** addresses for *chain*.

    Useful for building HyperSync address filters.
    """
    return [c.address.lower() for c in get_contracts_for_chain(chain)]
