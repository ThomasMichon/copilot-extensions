"""Best-effort bridge from a ``tracking_write`` verb handler (running inside
the resident status-monitor process) to that same process's live
segment-cache/wake-event, for an immediate (push) status-bar refresh instead
of waiting up to ``--interval`` seconds for the next periodic sweep.

``cmd_status_monitor`` (``status_monitor_cli.py``) calls :func:`bind` once at
startup with its own ``segment_cache``/wake ``threading.Event``. A verb
handler that runs IN-PROCESS inside the daemon when it serves a request (e.g.
``tracking_disposition_write.py``'s ``apply_status_disposition``, invoked via
``tracking_write.dispatch``) calls :func:`notify` after a successful write so
OTHER sessions' status bars refresh on the monitor's very next loop iteration
instead of waiting out the full backstop interval. A no-op before
:func:`bind` runs (e.g. the in-process fallback path with no resident daemon
reachable, or a bare unit test) -- always best-effort, never raises.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

_wake_event: threading.Event | None = None
_segment_cache = None
_changed: Callable[[str | None], None] | None = None


def bind(
    wake_event: threading.Event, segment_cache,
    changed: Callable[[str | None], None] | None = None,
) -> None:
    """Register this process's resident monitor state (called once, at
    ``cmd_status_monitor`` startup). ``changed`` receives every notified path
    (``None`` meaning "all") so the resident can republish the affected
    project's current roster on its next sweep."""
    global _wake_event, _segment_cache, _changed
    _wake_event = wake_event
    _segment_cache = segment_cache
    _changed = changed


def reset() -> None:
    """Clear any bound state (test isolation; also safe as a defensive
    no-op if a future shutdown path wants to un-bind explicitly)."""
    global _wake_event, _segment_cache, _changed
    _wake_event = None
    _segment_cache = None
    _changed = None


def notify(path: str | None) -> None:
    """Invalidate ``path``'s cached segment and wake the sweep loop early.

    Best-effort; a no-op when unbound. Never raises -- a verb transaction's
    own success must never depend on this purely-advisory side effect.
    """
    try:
        if path and _segment_cache is not None:
            _segment_cache.invalidate(path)
    except Exception:
        pass
    try:
        if _changed is not None:
            _changed(path or None)
    except Exception:
        pass
    try:
        if _wake_event is not None:
            _wake_event.set()
    except Exception:
        pass


def notify_ended(registry: dict[str, str], sessions, live) -> None:
    """:func:`notify` each registered worktree whose mux session left ``live``.

    The sweep calls this before pruning ``sessions`` from ``registry``, so a
    mux deactivation republishes its project's roster without waiting for
    demand. A session still live (handed to the manager) has not ended.
    """
    for sess in sessions:
        path = registry.get(sess)
        if path and sess not in live:
            notify(path)


def changed_path_project(path: str, *, session_projects, segment_cache) -> str | None:
    """Map a notified worktree path to its project without changing the active one.

    Uses only in-memory caches and the projects registry (no Git), so a state
    change event never spawns work or touches Windows observation custody.
    ``None`` means unresolved; the runtime then republishes every project it
    serves rather than dropping the event.
    """
    from pathlib import Path

    from . import status_updater_cli

    project = session_projects.get(os.path.normcase(os.path.realpath(path)))
    if project:
        return project
    lookup = getattr(segment_cache, "project_for_observation_path", None)
    project = lookup(path) if lookup is not None else None
    if project:
        return project
    candidate = Path(path)
    anchors = [candidate]
    suffix = ".worktrees"
    if candidate.parent.name.endswith(suffix) and len(candidate.parent.name) > len(suffix):
        anchors.insert(0, candidate.parent.with_name(candidate.parent.name[: -len(suffix)]))
    for anchor in anchors:
        project = status_updater_cli._reverse_lookup_project(anchor)
        if project:
            return project
    return None


def project_resolver(session_projects, segment_cache) -> Callable[[str], str | None]:
    """Resolver for ``WorktreesRuntime.take_changed_projects`` in the sweep loop.

    State-change events republish their project on the next sweep without
    recording demand, so they never hold off idle retirement.
    """
    return lambda path: changed_path_project(
        path, session_projects=session_projects, segment_cache=segment_cache,
    )
