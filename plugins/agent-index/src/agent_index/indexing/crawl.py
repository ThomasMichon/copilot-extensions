"""Acquire source changes without mixing discovery with indexing mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_index.indexing.manifest import CrawlManifest, CrawlStats, DeletedFile
from agent_index.sources.delta import SourceDelta

if TYPE_CHECKING:
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.indexing.state import IndexState
    from agent_index.sources.base import SourceConnector


def collect_source_changes(
    source_name: str,
    *,
    state: IndexState,
    path_index: PathIndex,
    full: bool,
    source_aware: bool,
    progress_cb: ProgressCallback | None = None,
    connector_kwargs: dict[str, object] | None = None,
    connector: SourceConnector | None = None,
) -> CrawlManifest:
    """Prefer explicit deltas; retain full reconciliation for legacy providers."""
    from agent_index.sources import get_connector

    if connector is None:
        connector = get_connector(source_name, **(connector_kwargs or {}))
    canonical = connector.source_name if source_aware else None
    if source_aware and (not isinstance(canonical, str) or not canonical):
        raise RuntimeError("connector has no canonical stored family")
    cancel_check = progress_cb.check_cancelled if progress_cb else None
    src_state = state.sources.get(source_name)
    last_commit = src_state.last_commit if src_state else None
    if canonical is not None and state.stored_families.get(source_name) != canonical:
        last_commit = None

    discover_delta = getattr(connector, "discover_delta", None)
    if not full and callable(discover_delta):
        delta = discover_delta(last_commit, cancel_check=cancel_check)
        if not isinstance(delta, SourceDelta):
            raise ValueError("Connector returned an incompatible delta result")
        delta.validate(connector.source_name)
        if cancel_check:
            cancel_check()
        return CrawlManifest(
            source=source_name,
            upserts=delta.upserts,
            deletions=delta.deletions,
            commit=delta.commit,
            stored_family=canonical,
            accounting_family=connector.source_name,
            stats=CrawlStats(
                files_scanned=len(delta.upserts) + len(delta.deletions),
                files_changed=len(delta.upserts),
                files_deleted=len(delta.deletions),
            ),
        )

    commit = connector.current_commit()
    stored_by_source = path_index.get_paths_by_family(connector.source_name)
    if full:
        upserts = connector.discover(cancel_check=cancel_check)
        if cancel_check:
            cancel_check()
        current_by_source: dict[str, set[str]] = {}
        for entry in upserts:
            current_by_source.setdefault(entry.source, set()).add(entry.path)
    else:
        current_by_source = connector.list_paths(cancel_check=cancel_check)
        if cancel_check:
            cancel_check()
        upserts = connector.discover_changed(last_commit, cancel_check=cancel_check)
        if cancel_check:
            cancel_check()

    deletions = [
        DeletedFile(source=source, path=path)
        for source, stored_paths in stored_by_source.items()
        for path in stored_paths - current_by_source.get(source, set())
    ]
    scanned = sum(len(paths) for paths in current_by_source.values())
    return CrawlManifest(
        source=source_name,
        upserts=upserts,
        deletions=deletions,
        commit=commit,
        stored_family=canonical,
        accounting_family=connector.source_name,
        stats=CrawlStats(
            files_scanned=scanned,
            files_changed=len(upserts),
            files_deleted=len(deletions),
            files_unchanged=scanned - len(upserts),
        ),
    )
