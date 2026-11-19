"""Preflight exact hinted acquisition before opening stores or embedding clients."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.change_hints_contract import execution_versions, validate_scope

if TYPE_CHECKING:
    from agent_index.indexing.engine import SourceSpec
    from agent_index.sources.base import SourceConnector
    from agent_index.sources.delta import AcquisitionPage, SourceDelta


class HintDiscovery(Protocol):
    def __call__(
        self, hints: ChangeHints, last_commit: str | None,
        *, cancel_check: Callable[[], None] | None = None,
    ) -> SourceDelta: ...


class HintPageDiscovery(Protocol):
    def __call__(
        self, hints: ChangeHints, last_commit: str | None, cursor: str | None,
        *, cancel_check: Callable[[], None] | None = None,
    ) -> AcquisitionPage: ...


def hint_page_discovery(connector: SourceConnector) -> HintPageDiscovery | None:
    """Negotiate both hint semantics and complete acquisition units on the callable."""
    discover = getattr(connector, "discover_hints_page", None)
    versions = getattr(discover, "acquisition_page_versions", ())
    if execution_versions(discover) and isinstance(versions, (tuple, list, set, frozenset)) and any(
        type(version) is int and version == 1 for version in versions
    ):
        return discover
    return None


def require_hint_connector(
    connector: SourceConnector, hints: ChangeHints,
) -> HintDiscovery | HintPageDiscovery:
    """Require the selected acquisition callable and its explicit effective scope."""
    discover = hint_page_discovery(connector) or getattr(connector, "discover_hints", None)
    if not callable(discover) or not execution_versions(discover):
        raise ValueError("Selected connector does not support change-hints v1")
    if getattr(connector, "hint_poll_refresh", False):
        from agent_index.indexing.poll_refresh import require_refresh_support

        require_refresh_support(getattr(connector, "discover_delta", None))
    if connector.source_name != hints.canonical_family or getattr(
        connector, "hint_scope", None,
    ) != (hints.canonical_family, hints.account_id):
        raise ValueError("Change hints do not match the connector family/account scope")
    return discover


def prepare_hint_connector(
    source: str | None, full: bool, hints: ChangeHints,
    configured: list[SourceSpec], kwargs_for: Callable[[SourceSpec], dict[str, object]],
) -> SourceConnector:
    """Do not resolve aliases or synthesize configurations for required hints."""
    from agent_index.sources import get_connector

    validate_scope(source, full, hints)
    matches = [spec for spec in configured if spec.name == source]
    if len(matches) != 1:
        raise ValueError("Change hints require one exact configured source")
    spec = matches[0]
    connector = get_connector(spec.name, **kwargs_for(spec))
    require_hint_connector(connector, hints)
    return connector
