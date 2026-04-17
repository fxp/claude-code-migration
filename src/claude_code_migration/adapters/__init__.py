"""Target adapters registry."""
from .base import Adapter, MigrationResult
from .hermes import HermesAdapter
from .opencode import OpenCodeAdapter
from .cursor import CursorAdapter
from .windsurf import WindsurfAdapter


ADAPTERS: dict[str, type[Adapter]] = {
    "hermes": HermesAdapter,
    "opencode": OpenCodeAdapter,
    "cursor": CursorAdapter,
    "windsurf": WindsurfAdapter,
}


def get_adapter(name: str) -> Adapter:
    if name not in ADAPTERS:
        raise ValueError(f"Unknown target '{name}'. Available: {', '.join(ADAPTERS)}")
    return ADAPTERS[name]()


__all__ = ["Adapter", "MigrationResult", "ADAPTERS", "get_adapter",
           "HermesAdapter", "OpenCodeAdapter", "CursorAdapter", "WindsurfAdapter"]
