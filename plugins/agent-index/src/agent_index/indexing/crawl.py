"""Acquire source changes without mixing discovery with indexing mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_index.indexing.manifest import CrawlManifest, CrawlStats, DeletedFile
from agent_index.sources.delta import SourceDelta

if TYPE_CHECKING:
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.change_hints import ChangeHints
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
    source_scope: str | None = None,
    change_hints: ChangeHints | None = None,
) -> CrawlManifest:
    """Prefer explicit deltas; retain full reconciliation for legacy providers."""
    from agent_index.sources import get_connector
    from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope

    if connector is None:
        connector = get_connector(source_name, **(connector_kwargs or {}))
    canonical = connector.source_name if source_aware else None
    if source_aware and (not isinstance(canonical, str) or not canonical):
        raise RuntimeError("connector has no canonical stored family")
    scope = recovery_scope(source_scope or source_name, connector)
    state.require_acquisition_scope(source_name, scope, full=full)
    cancel_check = progress_cb.check_cancelled if progress_cb else None
    src_state = state.sources.get(source_name)
    last_commit = src_state.last_commit if src_state else None
    if (
        change_hints is None and canonical is not None
        and state.stored_families.get(source_name) != canonical
    ):
        last_commit = None

    discover_delta = getattr(connector, "discover_delta", None)
    discover_page = getattr(connector, "discover_delta_page", None)
    discover_snapshot = getattr(connector, "discover_snapshot_page", None)
    journal = PendingCrawls(path_index)
    refresh = journal.refresh_candidates(source_name, scope, connector.source_name)
    if refresh is not None and change_hints is None and not full:
        from agent_index.indexing.poll_refresh import require_refresh_support

        require_refresh_support(discover_delta)
    track_refresh = False
    if change_hints is not None and getattr(connector, "hint_poll_refresh", False):
        from agent_index.indexing.poll_refresh import require_refresh_support

        require_refresh_support(discover_delta)
        track_refresh = True
    pending = journal.read_payload(source_name, scope)
    pages_selected = (
        not full and change_hints is None and callable(discover_page)
        and 1 in getattr(discover_page, "acquisition_page_versions", ())
    )
    snapshot_versions = getattr(discover_snapshot, "snapshot_page_versions", ())
    if full and callable(discover_snapshot) and not isinstance(
        snapshot_versions, (tuple, list, set, frozenset),
    ):
        raise ValueError("Invalid connector snapshot-page versions")
    snapshots_selected = (
        full and change_hints is None and callable(discover_snapshot)
        and any(
            type(version) is int and version == 1
            for version in snapshot_versions
        )
    )
    if pending is not None and not (pages_selected or snapshots_selected):
        raise ValueError("Complete pending paged acquisition before changing acquisition mode")
    if snapshots_selected and callable(discover_snapshot):
        from agent_index.indexing.snapshot_crawl import acquire_snapshot

        return acquire_snapshot(
            source_name, family=connector.source_name, stored_family=canonical,
            scope=scope, last_commit=last_commit, journal=journal, discover=discover_snapshot,
            refresh=refresh, cancel_check=cancel_check,
        )
    if pages_selected and callable(discover_page):
        if refresh is not None:
            raise ValueError("Selected paged connector cannot refresh outstanding hinted candidates")
        from agent_index.indexing.paged_crawl import acquire_pages

        return acquire_pages(
            source_name, family=connector.source_name, stored_family=canonical,
            scope=scope, last_commit=last_commit, journal=journal, discover=discover_page,
            cancel_check=cancel_check,
        )
    if change_hints is not None:
        from agent_index.indexing.change_hints_contract import validate_scope
        from agent_index.indexing.hinted_crawl import require_hint_connector

        validate_scope(source_name, full, change_hints)
        discover = require_hint_connector(connector, change_hints)
        delta = discover(change_hints, last_commit, cancel_check=cancel_check)
        if not isinstance(delta, SourceDelta) or delta.commit != last_commit:
            raise ValueError("Hinted acquisition must preserve the polling cursor")
    elif not full and callable(discover_delta) and getattr(connector, "delta_available", True):
        if refresh is not None:
            from agent_index.indexing.poll_refresh import require_refresh_support

            discover = require_refresh_support(discover_delta)
            delta = discover(
                last_commit, cancel_check=cancel_check, refresh_paths=tuple(sorted(refresh.paths)),
                refresh_commits=tuple(sorted(refresh.history)),
            )
        else:
            delta = discover_delta(last_commit, cancel_check=cancel_check)
        if not isinstance(delta, SourceDelta):
            raise ValueError("Connector returned an incompatible delta result")
    else:
        if refresh is not None and not full:
            raise ValueError("Refresh hinted candidates with their original capable connector")
        delta = None
    if delta is not None:
        delta.validate(connector.source_name)
        if cancel_check:
            cancel_check()
        from agent_index.indexing.poll_refresh import retained_candidates

        return CrawlManifest(
            source=source_name,
            upserts=delta.upserts,
            deletions=delta.deletions,
            commit=delta.commit,
            stored_family=canonical,
            accounting_family=connector.source_name,
            poll_refresh=(
                retained_candidates(delta, connector.source_name) if track_refresh
                else refresh if change_hints is None else None
            ),
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
    if refresh is not None:
        deleted = {(item.source, item.path) for item in deletions}
        for source, candidates in (
            (connector.source_name, refresh.paths),
            (connector.source_name + ":commits", refresh.history),
        ):
            deletions.extend(
                DeletedFile(source, path) for path in candidates
                if path not in current_by_source.get(source, set()) and (source, path) not in deleted
            )
    scanned = sum(len(paths) for paths in current_by_source.values())
    return CrawlManifest(
        source=source_name,
        upserts=upserts,
        deletions=deletions,
        commit=commit,
        stored_family=canonical,
        accounting_family=connector.source_name,
        poll_refresh=refresh,
        stats=CrawlStats(
            files_scanned=scanned,
            files_changed=len(upserts),
            files_deleted=len(deletions),
            files_unchanged=scanned - len(upserts),
        ),
    )
