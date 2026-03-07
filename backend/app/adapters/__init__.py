"""Protocol adapters for DeFi vaults.

Importing this package registers all concrete adapters with the registry.
"""

import app.adapters.aave as _aave  # noqa: F401
import app.adapters.euler as _euler  # noqa: F401
import app.adapters.morpho as _morpho  # noqa: F401
import app.adapters.pendle as _pendle  # noqa: F401
from app.adapters.base import BaseProtocolAdapter, ProtocolAdapter
from app.adapters.registry import (
    get_adapter,
    get_adapters_for_chain,
    get_all_adapters,
    list_registered_protocols,
    register_adapter,
)

__all__ = [
    "BaseProtocolAdapter",
    "ProtocolAdapter",
    "get_adapter",
    "get_adapters_for_chain",
    "get_all_adapters",
    "list_registered_protocols",
    "register_adapter",
]
