"""Adapter discovery and registry.

Maintains a mapping of ``(protocol_name) → adapter_class`` so Celery tasks and
API endpoints can look up the correct adapter at runtime.

Usage::

    from app.adapters.registry import get_adapter, get_adapters_for_chain

    adapter = get_adapter("morpho")
    chain_adapters = get_adapters_for_chain("ethereum")
"""

from __future__ import annotations

import structlog

from app.adapters.base import BaseProtocolAdapter

logger = structlog.get_logger(__name__)

_REGISTRY: dict[str, type[BaseProtocolAdapter]] = {}


def register_adapter(adapter_cls: type[BaseProtocolAdapter]) -> type[BaseProtocolAdapter]:
    """Register an adapter class by its ``protocol_name``.

    Can be used as a decorator::

        @register_adapter
        class MorphoAdapter(BaseProtocolAdapter): ...
    """
    name = adapter_cls.protocol_name.fget(adapter_cls)  # type: ignore[attr-defined]
    _REGISTRY[name] = adapter_cls
    logger.debug("adapter_registered", protocol=name)
    return adapter_cls


def get_adapter(protocol: str) -> BaseProtocolAdapter | None:
    """Return a fresh adapter instance for *protocol*, or ``None``."""
    adapter_cls = _REGISTRY.get(protocol)
    return adapter_cls() if adapter_cls else None


def get_adapters_for_chain(chain: str) -> list[BaseProtocolAdapter]:
    """Return adapter instances for every protocol that supports *chain*."""
    result: list[BaseProtocolAdapter] = []
    for adapter_cls in _REGISTRY.values():
        instance = adapter_cls()
        if chain in instance.supported_chains:
            result.append(instance)
    return result


def get_all_adapters() -> list[BaseProtocolAdapter]:
    """Return a fresh instance of every registered adapter."""
    return [cls() for cls in _REGISTRY.values()]


def list_registered_protocols() -> list[str]:
    """Return the names of all registered protocols."""
    return list(_REGISTRY.keys())
