"""Opt-in page-protocol enforcement at callable, collector and recovery boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.indexing.change_hints import ChangeHints
    from agent_index.sources.base import SourceConnector

CONTRACT_ID = "agent-index.paged-acquisition"


def _supports_v1(selected: object, attribute: str) -> bool:
    versions = getattr(selected, attribute, ())
    return (
        callable(selected)
        and isinstance(versions, (tuple, list, set, frozenset))
        and all(type(value) is int and value > 0 for value in versions)
        and 1 in versions
    )


def require_execution_v1(selected_run_reindex: object) -> None:
    """Negotiate on the actual callable before supplying the opt-in keyword."""
    if not _supports_v1(selected_run_reindex, "paged_acquisition_versions"):
        raise ValueError("Selected indexing callable does not support paged-acquisition v1")


def require_connector_pages(
    connector: SourceConnector, *, source: str, full: bool,
    change_hints: ChangeHints | None, required: bool,
) -> None:
    if type(required) is not bool:
        raise ValueError("require_paged_acquisition must be a bool")
    if not required:
        return
    if change_hints is not None:
        from agent_index.indexing.change_hints_contract import validate_scope
        from agent_index.indexing.hinted_crawl import require_hint_connector

        validate_scope(source, full, change_hints)
        require_hint_connector(connector, change_hints)
        selected = getattr(connector, "discover_hints_page", None)
        if not _supports_v1(selected, "change_hints_versions"):
            raise ValueError("Required hinted acquisition needs change-hints v1 pages")
    else:
        selected = getattr(
            connector, "discover_snapshot_page" if full else "discover_delta_page", None,
        )
    attribute = "snapshot_page_versions" if full else "acquisition_page_versions"
    if not _supports_v1(selected, attribute):
        raise ValueError(f"Required acquisition needs {attribute} v1 on the selected callable")


def require_pending_pages(
    data: object, *, full: bool, change_hints: ChangeHints | None,
) -> None:
    """Reuse the owner's decoded envelope without traversing manifest bodies."""
    if not isinstance(data, dict):
        raise ValueError("Required page protocol needs a decoded pending envelope")
    version = data.get("version")
    if change_hints is not None:
        qualified = version in (5, 6) and data.get("hints") == change_hints.to_dict()
    elif full:
        qualified = version == 4 or (
            version == 7 and data.get("page_protocol") == "full"
        )
    else:
        qualified = version == 2 or (
            version == 7 and data.get("page_protocol") == "incremental"
        )
    if not qualified:
        raise ValueError(
            "Required page protocol cannot qualify retained acquisition; "
            "preserve it and complete it with its original acquisition policy",
        )
