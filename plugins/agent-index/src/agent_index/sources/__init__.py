"""Generic source connector registry for agent-index."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.sources.base import FileEntry, SourceConnector
from agent_index.sources.git_repo import GitRepoConnector
from agent_index.sources.github import GitHubConnector

ConnectorFactory = Callable[..., SourceConnector]
_CONNECTORS: dict[str, ConnectorFactory] = {}

__all__ = [
    "ConnectorFactory",
    "FileEntry",
    "SourceConnector",
    "get_connector",
    "register_connector",
    "registered_source_prefixes",
]


def register_connector(name: str, factory: ConnectorFactory) -> None:
    """Register a connector factory under a source name prefix."""
    if not name:
        raise ValueError("Connector name must be non-empty")
    _CONNECTORS[name] = factory


def registered_source_prefixes() -> frozenset[str]:
    """Source-name prefixes owned by a registered connector.

    A stored chunk source is "live" iff a currently-registered connector owns
    its scheme -- the bare crawl-marker name (``git``) or any hierarchical
    source it emits (``git:<repo>``, ``git:<repo>:commits``,
    ``github:<owner>/<repo>:issues``, ...). Source GC derives its live set from
    this so it stays correct as connectors are added, instead of hardcoding
    each scheme (the #116 extraction gap, where GC knew only VEI's ``forge:*``
    and purged the generic ``git:*`` index on every full reindex).
    """
    return frozenset(_CONNECTORS)


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

    raise ValueError(f"Unknown source: {source!r}. No connector is registered.")
register_connector("git", GitRepoConnector)
register_connector("github", GitHubConnector)
register_connector("ado", AzureDevOpsConnector)
register_connector("azure-devops", AzureDevOpsConnector)
