"""agent-dispatch -- a portable agent task-queue + coordinator.

This package currently ships the queue **engine** (:mod:`agent_dispatch.queue`):
a single-writer, WAL-mode SQLite task queue with an eight-state model,
capability-gated atomic claim, and lease recovery. The per-host coordinator
daemon and CLI land in a subsequent slice.
"""

from __future__ import annotations

from agent_dispatch.queue import Status, Task, TaskError, TaskQueue, worker_id_for

__all__ = ["Status", "Task", "TaskError", "TaskQueue", "worker_id_for"]


def _resolve_version() -> str:
    """Runtime version, resolved WITHOUT a hand-maintained constant.

    ``pyproject.toml`` is the single source of truth. At deploy time the
    installer stamps that version (plus git provenance) into ``_build_info.py``
    (mirrors agent-worktrees), so the running CLI / coordinator report it with
    no dependency on installed package metadata. Resolution order:

    1. ``_build_info.BUILD_INFO['version']`` -- the deploy-stamped value (the
       committed repo copy leaves this empty, so an un-stamped checkout falls
       through rather than reporting a stale literal);
    2. ``importlib.metadata`` -- the packaged version (present for any pip
       install, incl. editable/CI), still ultimately derived from pyproject;
    3. a ``dev`` sentinel for a bare source tree with neither.
    """
    try:
        from ._build_info import BUILD_INFO

        stamped = BUILD_INFO.get("version") or ""
    except Exception:
        stamped = ""
    if stamped:
        return stamped
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("agent-dispatch")
        except PackageNotFoundError:
            return "0.0.0+dev"
    except Exception:
        return "0.0.0+dev"


__version__ = _resolve_version()
