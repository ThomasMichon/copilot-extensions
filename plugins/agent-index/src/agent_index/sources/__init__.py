"""Generic source connector registry for agent-index.

Concrete connectors are intentionally not bundled in this Phase 2 core port.
Downstream packages can register connector factories at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_index.sources.base import FileEntry, SourceConnector

ConnectorFactory = Callable[..., SourceConnector]
_CONNECTORS: dict[str, ConnectorFactory] = {}

__all__ = [
    "ConnectorFactory",
    "FileEntry",
    "SourceConnector",
    "get_connector",
    "register_connector",
]


def register_connector(name: str, factory: ConnectorFactory) -> None:
    """Register a connector factory under a source name prefix."""
    if not name:
        raise ValueError("Connector name must be non-empty")
    _CONNECTORS[name] = factory


def get_connector(source: str, **kwargs: Any) -> SourceConnector:
    """Return a connector instance for the given source name.

    Exact names win first; otherwise the longest registered ``prefix:`` match
    wins so connector families can own hierarchical source names.
    """
    if source in _CONNECTORS:
        return _CONNECTORS[source](source=source, **kwargs)

    for prefix in sorted(_CONNECTORS, key=len, reverse=True):
        if source.startswith(f"{prefix}:"):
            return _CONNECTORS[prefix](source=source, **kwargs)

    # Phase 2b: built-in forge connectors (files/commits/issues/PRs).
    raise ValueError(f"Unknown source: {source!r}. No connector is registered.")
