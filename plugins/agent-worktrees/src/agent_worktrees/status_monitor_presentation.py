"""Publish managed native presentation without entering legacy mux effects."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import os
import time
from typing import Any

from agent_remote_driver.mux_creation import is_psmux

from . import config as cfg, config_cache, push_timeout
from . import status_bar_cli, status_monitor_native, status_monitor_runtime
from .closure_descriptor import interpret_descriptor_payload
from .monitor_projection_cache import ProjectedSegments, SegmentOwner
from .mux_state_projection import render_projected_segment
from .observation_progress import inventory_scope


@dataclass
class _ComputedProject:
    by_id: dict[str, dict[str, Any]]
    by_path: dict[str, dict[str, Any] | None]
    current: bool
    paths_by_id: dict[str, str]


class PresentationProgress:
    """Service presentation between observations without another worker."""

    def __init__(
        self, interval: float = 30.0, *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = interval
        self.clock = clock
        self._next = 0.0
        self._callback: Callable[[], None] | None = None
        self._running = False

    @contextmanager
    def bind(self, callback: Callable[[], None]) -> Iterator[None]:
        previous = self._callback
        self._callback = callback
        try:
            yield
        finally:
            self._callback = previous

    def run(self, callback: Callable[[], None], *, force: bool = False) -> None:
        if self._running or (not force and self.clock() < self._next):
            return
        self._running = True
        try:
            callback()
        finally:
            self._next = self.clock() + self.interval
            self._running = False

    def __call__(self) -> None:
        if self._callback is not None:
            self.run(self._callback)


def _native_entry(entry: dict[str, Any], observed: tuple[int, str]) -> dict[str, Any] | None:
    if not all(
        isinstance(entry.get(field), str) and entry[field]
        for field in ("project", "worktree_id", "worktree_path", "mux_session")
    ):
        return None
    _, incarnation = observed
    if incarnation == entry.get("session_incarnation"):
        return entry
    return None


def run_monitor_sweep(
    sweep: Callable[..., int], mux_bin: str | None, token: str, prefix: str,
    ctx_done: set[str], *, progress: PresentationProgress | None = None,
    state_snapshot: Callable[[str], dict[str, Any]] | None = None,
    content_paths: dict[str, str | None] | None = None,
    refresh_content: Callable[[Callable[[str], None]], None] | None = None,
    **options: Any,
) -> int:
    """Keep native presentation progressing during the original roster pass."""
    from . import __main__ as core

    if not mux_bin:
        served = sweep(mux_bin, token, prefix, ctx_done, **options)
        if refresh_content is not None:
            refresh_content(lambda path: None)
        return served
    cache = options.get("managed_mux_cache")
    segments: SegmentOwner | None = options.get("segment_cache")

    def computed_segment(
        project: str | None, path: str, worktree_id: str | None,
        snapshots: dict[str, _ComputedProject],
    ) -> str:
        if not project:
            cached = segments.get_cached(path) if segments is not None else None
            return cached if isinstance(cached, str) else render_projected_segment(
                None, current=False,
            )
        if project not in snapshots:
            reply = state_snapshot(project) if state_snapshot is not None else {}
            rows = reply.get("worktrees")
            projection = reply.get("projection")
            by_id = {
                row["id"]: row for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            } if isinstance(rows, list) else {}
            by_path: dict[str, dict[str, Any] | None] = {}
            paths_by_id: dict[str, str] = {}
            for indexed in by_id.values():
                indexed_path = indexed.get("path")
                if isinstance(indexed_path, str) and indexed_path:
                    indexed_path = os.path.normcase(os.path.realpath(indexed_path))
                    by_path[indexed_path] = None if indexed_path in by_path else indexed
                    paths_by_id[indexed["id"]] = indexed_path
            snapshots[project] = _ComputedProject(by_id, by_path, (
                reply.get("ok") is True
                and isinstance(projection, dict)
                and projection.get("quality") == "current"
                and not reply.get("availability")
            ), paths_by_id)
        snapshot = snapshots[project]
        path_key = os.path.normcase(os.path.realpath(path))
        if worktree_id is not None:
            row = snapshot.by_id.get(worktree_id)
        else:
            row = snapshot.by_path.get(path_key)
            if row is None and path_key in snapshot.by_path:
                return render_projected_segment(None, current=False)
        row_path = snapshot.paths_by_id.get(row["id"]) if row is not None else None
        if row_path is not None and row_path != path_key:
            return render_projected_segment(None, current=False)
        if row is not None and snapshot.current and row.get("state") != "active":
            return render_projected_segment(row, current=True)
        cached = segments.get_cached(path) if segments is not None else None
        if isinstance(cached, str):
            return cached
        if (
            row is not None and row_path == path_key and row.get("state") == "active"
            and interpret_descriptor_payload(row.get("closure"))["supported"]
        ):
            advisory = getattr(segments, "get_advisory", None)
            if callable(advisory):
                completed = advisory(project, path, row["id"], row.get("creation_nonce"))
                if isinstance(completed, str):
                    return completed
        if segments is not None and segments.observation_blocked(project, path):
            return segments.pending_segment_for_path(path)
        if row is not None and row.get("state") == "active":
            return render_projected_segment({"title": row.get("title")}, current=False)
        return render_projected_segment(row, current=snapshot.current)

    if not is_psmux(mux_bin):
        snapshots: dict[str, _ComputedProject] = {}

        def read_registered(path: str) -> str:
            project = cfg.active_project()
            if content_paths is not None:
                content_paths[path] = project
            return computed_segment(project, path, None, snapshots)

        served = sweep(
            mux_bin, token, prefix, ctx_done, **dict(options, segment_cache=ProjectedSegments(
                segments, read_registered,
            )),
        )
        if refresh_content is not None:
            refresh_content(lambda path: None)
        return served
    progress = progress if progress is not None else PresentationProgress(
        interval=float(options.get("interval", 30.0)),
    )
    published = options.get("published")
    incarnations = options.get("incarnations")
    lock = options.get("project_lock")
    recheck: Callable[[Any, str], object] = core._status_monitor_recheck
    governance_deferred: type[Exception] = core._StatusMonitorGovernanceDeferred
    settings_scope: Callable[..., Any] = config_cache.project_scope
    ready_entries: dict[str, dict[str, Any]] = {}
    ready_inventory: dict[str, tuple[int, str]] = {}
    ready_by_path: dict[str, dict[str, dict[str, Any]]] = {}
    completion_snapshots: dict[str, _ComputedProject] = {}

    def publish_session(
        session: str, original: dict[str, Any], observed: tuple[int, str],
        snapshots: dict[str, _ComputedProject],
    ) -> None:
        recheck(options.get("governance"), "pre-mutation:discover-connected-mux")
        entry = _native_entry(original, observed)
        if entry is None:
            return
        project, worktree_id, path = (
            entry.get("project"), entry.get("worktree_id"), entry.get("worktree_path"),
        )
        if (
            not isinstance(project, str) or not project
            or not isinstance(worktree_id, str) or not worktree_id
            or not isinstance(path, str) or not path
        ):
            return
        if content_paths is not None:
            content_paths[path] = project
        if incarnations is not None:
            status_monitor_runtime._monitor_update_session_incarnations(
                incarnations, {}, set(), {session: entry}, ctx_done, published,
            )
        priority = options.get("lifecycle_priority")
        # A roster progress callback can already hold the shared project lock.
        if priority is not None and priority.is_set():
            return
        with lock if lock is not None else nullcontext():
            previous_project = cfg.active_project()
            try:
                cfg.set_active_project(project)
                with settings_scope(options.get("config_cache_for_project"), project):
                    context = None
                    try:
                        if session not in ctx_done:
                            context = status_bar_cli._render_status_context(path, plain=False)
                        segment = computed_segment(project, path, worktree_id, snapshots)
                    except push_timeout.ObservationCleanupPending:
                        if segments is None:
                            raise
                        segment = segments.pending_segment_for_path(path)
            finally:
                cfg.set_active_project(previous_project)
        if not isinstance(segment, str):
            return
        if priority is not None and priority.is_set():
            return
        values = {"@aw_seg": segment}
        if context is not None:
            values["@aw_ctx"] = context
        result = status_monitor_native.publish_session_status(
            entry=entry, values=values, published=published,
            before_publish=lambda: recheck(options.get("governance"), "pre-mutation:publish-status"),
        )
        if result is not None and result.get("context_published"):
            ctx_done.add(session)

    def present(live: dict[str, tuple[int, str]]) -> None:
        entries, _, _ = status_monitor_runtime._monitor_managed_session_union(cache, {})
        original_entries = entries
        entries = {
            session: entry for session, entry in entries.items()
            if session in live and _native_entry(entry, live[session]) is not None
        }
        discovered = status_monitor_native.discover_entries(live, set(entries))
        for session, entry in discovered.items():
            original = original_entries.get(session)
            prior_incarnation = incarnations.get(session) if incarnations is not None else None
            if prior_incarnation is None and original is not None:
                prior_incarnation = original.get("session_incarnation")
            if original is not None and prior_incarnation != entry["session_incarnation"]:
                ctx_done.discard(session)
                if published is not None:
                    for key in [key for key in published if key[0] == session]:
                        published.pop(key, None)
        entries.update(discovered)
        for session in list(ready_entries):
            if session not in live or _native_entry(ready_entries[session], live[session]) is None:
                ready_entries.pop(session)
                ready_inventory.pop(session, None)
        ready_entries.update(entries)
        ready_inventory.update({session: live[session] for session in entries})
        ready_by_path.clear()
        for session, entry in ready_entries.items():
            ready_by_path.setdefault(entry["worktree_path"], {})[session] = entry
        completion_snapshots.clear()
        publish_entries(entries, live, {})

    def publish_entries(
        entries: dict[str, dict[str, Any]], live: dict[str, tuple[int, str]],
        snapshots: dict[str, _ComputedProject],
    ) -> None:
        for session, original in entries.items():
            if session not in live:
                continue
            try:
                publish_session(session, original, live[session], snapshots)
            except governance_deferred:
                raise
            except push_timeout.ObservationCleanupError as error:
                project, path = original.get("project"), original.get("worktree_path")
                if not isinstance(project, str) or not isinstance(path, str):
                    raise
                if segments is None:
                    raise
                segments.retain_unclaimed_cleanup(project, path, error)
            except Exception:
                continue

    def refresh() -> None:
        live = core._monitor_list_sessions(mux_bin)
        if live is not None:
            present(live)

    catalog_observer = options.get("catalog_observer")
    with inventory_scope() as inventory, progress.bind(refresh):
        def observe(names: set[str]) -> None:
            live = inventory.completed
            if catalog_observer is not None:
                catalog_observer(names)
            if live is not None:
                progress.run(lambda: present(live), force=True)

        served = sweep(mux_bin, token, prefix, ctx_done, **dict(options, catalog_observer=observe))
        if refresh_content is not None:
            completion_snapshots.clear()
            refresh_content(lambda path: publish_entries(
                ready_by_path.get(path, {}), ready_inventory, completion_snapshots,
            ))
        return served
