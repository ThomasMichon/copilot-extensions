"""Preflight exact hinted acquisition before opening stores or embedding clients."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.change_hints_contract import execution_versions, validate_scope

if TYPE_CHECKING:
    from agent_index.indexing.engine import SourceSpec
    from agent_index.sources.base import SourceConnector
    from agent_index.sources.delta import SourceDelta


class HintDiscovery(Protocol):
    def __call__(
        self, hints: ChangeHints, last_commit: str | None,
        *, cancel_check: Callable[[], None] | None = None,
    ) -> SourceDelta: ...


def require_hint_connector(connector: SourceConnector, hints: ChangeHints) -> HintDiscovery:
    """Require the selected acquisition callable and its explicit effective scope."""
    discover = getattr(connector, "discover_hints", None)
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
