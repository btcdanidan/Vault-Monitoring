"""Adapter discovery / registry."""

from app.adapters.base import ProtocolAdapter

# Stub: register adapters by protocol name and return adapter instance
_REGISTRY: dict[str, type[ProtocolAdapter]] = {}


def get_adapter(protocol: str) -> ProtocolAdapter | None:
    """Return adapter for protocol, or None if not registered."""
    adapter_cls = _REGISTRY.get(protocol)
    return adapter_cls() if adapter_cls else None


def register_adapter(protocol: str, adapter_cls: type[ProtocolAdapter]) -> None:
    """Register an adapter class for a protocol."""
    _REGISTRY[protocol] = adapter_cls
