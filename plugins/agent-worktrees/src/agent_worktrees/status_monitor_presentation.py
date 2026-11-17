"""Publish managed native presentation without entering legacy mux effects."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
import time
from typing import Any

from agent_remote_driver.mux_creation import is_psmux

from . import config as cfg, config_cache, mux_status_link, push_timeout
from . import status_bar_cli, status_monitor_runtime, tracking
from .monitor_projection_cache import ProjectedSegments, SegmentOwner
from .mux_state_projection import render_projected_segment
from .observation_progress import inventory_scope


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


def _native_entry(
    entry: dict[str, Any], mux_bin: str, cache: Any, observed: tuple[int, str],
) -> dict[str, Any] | None:
    if not all(
        isinstance(entry.get(field), str) and entry[field]
        for field in ("project", "worktree_id", "worktree_path", "mux_session")
    ):
        return None
    clients, incarnation = observed
    if incarnation == entry.get("session_incarnation"):
        return entry
    if not clients:
        return None
    result = mux_status_link.push_discovery_via_daemon({
        "project": entry["project"], "worktree_id": entry["worktree_id"],
        "worktree_path": entry["worktree_path"], "mux_session": entry["mux_session"],
        "mux_bin": mux_bin, "session_incarnation": incarnation,
        "attached_clients": clients,
    })
    if not result or not result.get("applied"):
        return None
    cache.apply_observation(result["entry"])
    current = cache.get(entry["project"], entry["worktree_id"])
    return current if (
        isinstance(current, dict) and current.get("live")
        and current.get("mux_session") == entry["mux_session"]
        and current.get("session_incarnation") == incarnation
    ) else None


def run_monitor_sweep(
    sweep: Callable[..., int], mux_bin: str | None, token: str, prefix: str,
    ctx_done: set[str], *, progress: PresentationProgress | None = None,
    state_snapshot: Callable[[str], dict[str, Any]] | None = None, **options: Any,
) -> int:
    """Keep native presentation progressing during the original roster pass."""
    from . import __main__ as core

    if not mux_bin:
        return sweep(mux_bin, token, prefix, ctx_done, **options)
    cache = options.get("managed_mux_cache")
    segments: SegmentOwner | None = options.get("segment_cache")

    def computed_segment(
        project: str | None, path: str, worktree_id: str | None,
        snapshots: dict[str, tuple[dict[str, dict[str, Any]], bool]],
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
            snapshots[project] = ({
                row["id"]: row for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            } if isinstance(rows, list) else {}, (
                reply.get("ok") is True
                and isinstance(projection, dict)
                and projection.get("quality") == "current"
                and not reply.get("availability")
            ))
        rows_by_id, current = snapshots[project]
        if worktree_id is not None:
            row = rows_by_id.get(worktree_id)
        else:
            matches = [row for row in rows_by_id.values() if row.get("path") == path]
            row = matches[0] if len(matches) == 1 else None
        if row is not None and current:
            return render_projected_segment(row, current=True)
        cached = segments.get_cached(path) if segments is not None else None
        if isinstance(cached, str):
            return cached
        if segments is not None and segments.observation_blocked(project, path):
            return segments.pending_segment_for_path(path)
        return render_projected_segment(row, current=current)

    if not is_psmux(mux_bin) or cache is None:
        snapshots: dict[str, tuple[dict[str, dict[str, Any]], bool]] = {}
        return sweep(
            mux_bin, token, prefix, ctx_done, **dict(options, segment_cache=ProjectedSegments(
                segments, lambda path: computed_segment(cfg.active_project(), path, None, snapshots),
            )),
        )
    progress = progress if progress is not None else PresentationProgress(
        interval=float(options.get("interval", 30.0)),
    )
    published = options.get("published")
    incarnations = options.get("incarnations")
    lock = options.get("project_lock")
    recheck: Callable[[Any, str], object] = core._status_monitor_recheck
    governance_deferred: type[Exception] = core._StatusMonitorGovernanceDeferred
    settings_scope: Callable[..., Any] = config_cache.project_scope

    def publish_session(
        session: str, original: dict[str, Any], observed: tuple[int, str],
        snapshots: dict[str, tuple[dict[str, dict[str, Any]], bool]],
    ) -> None:
        recheck(options.get("governance"), "pre-mutation:discover-connected-mux")
        entry = _native_entry(original, mux_bin, cache, observed)
        if entry is None:
            return
        if entry.get("session_incarnation") != original.get("session_incarnation"):
            ctx_done.discard(session)
            if published is not None:
                for key in [key for key in published if key[0] == session]:
                    published.pop(key, None)
        project, worktree_id, path = (
            entry.get("project"), entry.get("worktree_id"), entry.get("worktree_path"),
        )
        if (
            not isinstance(project, str) or not project
            or not isinstance(worktree_id, str) or not worktree_id
            or not isinstance(path, str) or not path
        ):
            return
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
        values = {"@aw_updater": token, "@aw_updater_prefix": prefix, "@aw_seg": segment}
        if context is not None:
            values["@aw_ctx"] = context
        result = mux_status_link.publish_managed_session_status(
            managed_mux_cache=cache, project=project, path=path, session_name=session,
            worktree_id=worktree_id, values=values, published=published, prefix=prefix, token=token,
            resolve_worktree_id=tracking.find_worktree_id_by_cwd,
            before_publish=lambda: recheck(options.get("governance"), "pre-mutation:publish-status"),
        )
        if result is not None and result.get("context_published"):
            ctx_done.add(session)

    def present(live: dict[str, tuple[int, str]]) -> None:
        entries, _, _ = status_monitor_runtime._monitor_managed_session_union(cache, {})
        snapshots: dict[str, tuple[dict[str, dict[str, Any]], bool]] = {}
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

        return sweep(mux_bin, token, prefix, ctx_done, **dict(options, catalog_observer=observe))
