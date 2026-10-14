"""Resolve live Copilot bindings through exact locks or explicit fleet discovery."""

from __future__ import annotations

import platform
from collections.abc import Iterable

from . import locks, process_table_cache, sessions, tracking


def resolve_bound_copilots(
    *,
    session_id: str | None = None,
    worktree_id: str | None = None,
    worktree_path: str | None = None,
    table: dict[int, dict] | None = None,
    session_ids: Iterable[str] | None = None,
) -> list[dict]:
    """Resolve lock-backed live Copilots and their mux/bare/unknown homing.

    ``session_ids`` selects exact registered session directories without a
    session-state sweep. An empty registry returns immediately. A registered
    binding remains authoritative when a bare resume starts from another cwd.
    Without that selector this retains explicit fleet discovery, filtered by
    the session ID/prefix, resolved worktree ID or cwd under ``worktree_path``.
    Detached parent-continuations and dead/non-Copilot lock owners are excluded.
    """
    from . import reclaim

    if session_ids is None and table is None:
        table = process_table_cache.cached(reclaim.build_process_table)
    state_dir = sessions._session_state_dir()
    results: list[dict] = []
    if not state_dir.exists():
        return results
    if session_ids is None:
        entries = sorted(state_dir.iterdir())
    else:
        from .session_projection import _valid_session_id

        ids = sorted(set(session_ids))
        if any(not _valid_session_id(sid) for sid in ids):
            raise ValueError("Invalid registered session identity")
        if not ids:
            return results
        entries = [state_dir / sid for sid in ids]
    table = process_table_cache.cached(reclaim.build_process_table) if table is None else table
    posix = platform.system() != "Windows"
    pane_ttys: list = [None]

    for entry in entries:
        if not entry.is_dir():
            continue
        sid = entry.name
        if session_id and not (sid == session_id or sid.startswith(session_id)):
            continue
        live_pids = [
            pid for pid in reclaim._lock_pids(entry)
            if sessions._is_process_alive(pid)
            and sessions._is_copilot_process(pid)
        ]
        if not live_pids or sessions._is_detached_session(entry):
            continue
        cwd = reclaim._session_cwd(entry)
        if worktree_path and not reclaim._cwd_under(cwd, worktree_path):
            continue
        if session_ids is not None and worktree_id:
            wt_id = worktree_id
        else:
            wt_id = reclaim._resolve_worktree_id_for_cwd(cwd) if cwd else None
        if wt_id is None:
            try:
                wt_id = tracking.find_worktree_id_by_session(sid)
            except Exception:
                wt_id = None
        if worktree_id and wt_id != worktree_id:
            continue
        for pid in live_pids:
            homing = reclaim.homing_of(pid, table)
            if homing == "bare" and posix:
                if pane_ttys[0] is None:
                    from . import remux

                    pane_ttys[0] = remux.tmux_pane_ttys()
                if pane_ttys[0]:
                    from . import remux

                    tty = remux.process_tty(pid)
                    if tty and tty in pane_ttys[0]:
                        homing = "mux"
            results.append({
                "session_id": sid,
                "pid": pid,
                "start_time": locks.process_start_time(pid),
                "cwd": cwd,
                "worktree_id": wt_id,
                "homing": homing,
            })
    return results
