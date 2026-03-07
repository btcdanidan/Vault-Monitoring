"""Aave v3 contract addresses, function selectors, and protocol constants (§9).

Extracted from the adapter to keep business logic clean and allow updates
without touching the adapter code.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Contract addresses per chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AaveChainConfig:
    """Aave v3 deployment addresses for a single chain."""

    pool: str
    ui_pool_data_provider: str
    pool_addresses_provider: str


AAVE_CHAIN_CONFIGS: dict[str, AaveChainConfig] = {
    "ethereum": AaveChainConfig(
        pool="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        ui_pool_data_provider="0x91c0eA31b49B69Ea18607702c5d9aC360bf3dE7d",
        pool_addresses_provider="0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e",
    ),
    "base": AaveChainConfig(
        pool="0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        ui_pool_data_provider="0x174446a6741300cD2E7C1b1A636b503c91C7e46A",
        pool_addresses_provider="0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D",
    ),
}

# ---------------------------------------------------------------------------
# Function selectors (first 4 bytes of keccak256 of the signature)
# ---------------------------------------------------------------------------

# UiPoolDataProviderV3.getReservesData(address provider)
SEL_GET_RESERVES_DATA = bytes.fromhex("ec489c21")

# Pool.getUserAccountData(address user)
SEL_GET_USER_ACCOUNT_DATA = bytes.fromhex("bf92857c")

# Pool.getReservesList()
SEL_GET_RESERVES_LIST = bytes.fromhex("d1946dbc")

# Pool.getReserveData(address asset)
SEL_GET_RESERVE_DATA = bytes.fromhex("35ea6a75")

# ERC-20 balanceOf(address)
SEL_BALANCE_OF = bytes.fromhex("70a08231")

# ERC-20 decimals()
SEL_DECIMALS = bytes.fromhex("313ce567")

# ERC-20 symbol()
SEL_SYMBOL = bytes.fromhex("95d89b41")

# ---------------------------------------------------------------------------
# APY conversion
# ---------------------------------------------------------------------------

RAY = 10**27
SECONDS_PER_YEAR = 31_536_000


def ray_to_apy(rate_ray: int) -> float:
    """Convert an Aave v3 ray-denominated rate to an annualised APY percentage.

    Formula from §9:
    ``supplyAPY = (((1 + (liquidityRate / 1e27 / 31536000)) ^ 31536000) - 1) * 100``
    """
    if rate_ray == 0:
        return 0.0
    rate_per_second = rate_ray / RAY / SECONDS_PER_YEAR
    apy = ((1 + rate_per_second) ** SECONDS_PER_YEAR - 1) * 100
    return apy


# ---------------------------------------------------------------------------
# Aave v3 event topic0 constants (cross-referenced with hypersync_client.py)
# ---------------------------------------------------------------------------

AAVE_SUPPLY_TOPIC = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
AAVE_WITHDRAW_TOPIC = "0x3115d1449a7b732c986cba18244e897a145df0b3b24b3ae1e1e35d8d3a3cc678"
AAVE_BORROW_TOPIC = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
AAVE_REPAY_TOPIC = "0xa534c8dbe71f871f9f3f77571f15f067af254a42571f132a7e0b817e4523ae96"
AAVE_LIQUIDATION_TOPIC = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"

AAVE_EVENT_TOPICS: list[str] = [
    AAVE_SUPPLY_TOPIC,
    AAVE_WITHDRAW_TOPIC,
    AAVE_BORROW_TOPIC,
    AAVE_REPAY_TOPIC,
    AAVE_LIQUIDATION_TOPIC,
]

TOPIC_TO_ACTION: dict[str, str] = {
    AAVE_SUPPLY_TOPIC: "deposit",
    AAVE_WITHDRAW_TOPIC: "withdraw",
    AAVE_BORROW_TOPIC: "borrow",
    AAVE_REPAY_TOPIC: "repay",
    AAVE_LIQUIDATION_TOPIC: "repay",
}

# ---------------------------------------------------------------------------
# ReserveData struct field offsets (from UiPoolDataProviderV3.getReservesData)
#
# The struct has many fields. We decode the ones we need by word offset:
#   0: underlyingAsset (address)
#   1-2: name (dynamic string offset/data — skip, use symbol)
#   ... The actual layout varies by Aave version. We decode from the raw
#       getReserveData(asset) return which has a fixed layout.
# ---------------------------------------------------------------------------

# getReserveData return struct word offsets (ReserveData from Pool):
# Word 0-15 is the ReserveConfigurationMap + various rates and indices
# We primarily need:
#   configuration (word 0): packed bitmap
#   liquidityIndex (word 1): uint128
#   currentLiquidityRate (word 2): uint128
#   variableBorrowIndex (word 3): uint128
#   currentVariableBorrowRate (word 4): uint128
#   currentStableBorrowRate (word 5): uint128
#   lastUpdateTimestamp (word 6): uint40
#   aTokenAddress (word 8): address
#   stableDebtTokenAddress (word 9): address
#   variableDebtTokenAddress (word 10): address
RESERVE_DATA_WORDS = 15
