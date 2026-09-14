"""Mechanical extraction from ``sessions.py`` for pane-retirement helpers.

This module exists only to keep ``agent_worktrees.sessions`` under its
grandfathered module-size baseline. The functions below were moved verbatim, with
no behavior change, from ``sessions.py``.
"""

from __future__ import annotations

import os
import platform


def _mux_bin(mux: str | None = None) -> str:
    """Resolve the multiplexer binary name (psmux on Windows, tmux elsewhere)."""
    if mux:
        return mux
    return "psmux" if platform.system() == "Windows" else "tmux"


def _mux_pane_pid(pane_id: str | None, *, mux: str | None = None) -> int | None:
    """Return the root pid of one mux pane, or ``None`` when unavailable."""
    if not pane_id:
        return None
    import subprocess

    mux_bin = _mux_bin(mux)
    try:
        r = subprocess.run(
            [
                mux_bin, "display-message", "-p", "-t", pane_id,
                "#{pane_pid}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return None
        pid = int(r.stdout.strip())
        return pid if pid > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _mux_pane_process_tree(
    pane_id: str | None, *, mux: str | None = None,
) -> set[int]:
    """Snapshot the exact process tree rooted at ``pane_id`` before teardown."""
    pane_pid = _mux_pane_pid(pane_id, mux=mux)
    if not pane_pid:
        return set()
    try:
        from . import reclaim

        table = reclaim.build_process_table()
        return {pane_pid, *reclaim.descendants_of(pane_pid, table)}
    except OSError:
        return {pane_pid}


def _retire_failed_successor(
    pane_id: str | None,
    process_tree: set[int],
    *,
    mux: str | None = None,
) -> dict:
    """Retire a failed successor pane and terminate its exact surviving tree."""
    import signal
    import time

    retire = (
        mux_retire_pane(pane_id, mux=mux)
        if pane_id else {"ok": True, "gone": True, "method": "no-pane"}
    )
    terminated: list[int] = []
    survivors: list[int] = []
    try:
        from . import locks, procs

        # Children first, pane root last. The snapshot is pane-specific, so this
        # cannot splash onto the predecessor or another pane in the same worktree.
        for pid in sorted(process_tree, reverse=True):
            if not locks.pid_alive(pid):
                continue
            if procs.terminate_pid(pid):
                terminated.append(pid)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            survivors = [
                pid for pid in sorted(process_tree) if locks.pid_alive(pid)
            ]
            if not survivors:
                break
            time.sleep(0.05)
        # procs.terminate_pid is SIGTERM on POSIX. Escalate the exact pane tree
        # after the bounded grace period; Windows already uses TerminateProcess.
        if survivors and platform.system() != "Windows":
            for pid in survivors:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                survivors = [
                    pid for pid in survivors if locks.pid_alive(pid)
                ]
                if not survivors:
                    break
                time.sleep(0.05)
    except OSError:
        survivors = sorted(process_tree)
    return {
        "retire": retire, "process_tree": sorted(process_tree), "terminated": terminated,
        "survivors": survivors, "ok": bool(retire.get("gone")) and not survivors,
    }


def _mux_pane_alive(pane_id: str, mux_bin: str) -> bool:
    """Whether ``pane_id`` still exists in any session/window."""
    import subprocess

    try:
        r = subprocess.run(
            [mux_bin, "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False
        return pane_id in r.stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _mux_pane_session_name(pane_id: str, mux_bin: str) -> str | None:
    """Return the mux session name containing ``pane_id``."""
    import subprocess

    try:
        r = subprocess.run(
            [mux_bin, "display-message", "-p", "-t", pane_id, "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return getattr(r, "stdout", "").strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mux_session_window_count(session_name: str, mux_bin: str) -> int | None:
    """Return the number of windows in ``session_name`` when the mux reports it."""
    import subprocess

    target = session_name if mux_bin == "psmux" else f"={session_name}"
    try:
        r = subprocess.run(
            [mux_bin, "list-windows", "-t", target, "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return len([line for line in getattr(r, "stdout", "").splitlines() if line.strip()])
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mux_last_window_guard(pane_id: str, mux_bin: str) -> dict | None:
    """Return guard context when retiring ``pane_id`` would close a wt session."""
    session_name = _mux_pane_session_name(pane_id, mux_bin)
    if not session_name or not session_name.startswith("wt-"):
        return None
    window_count = _mux_session_window_count(session_name, mux_bin)
    if window_count == 1:
        return {"session": session_name, "window_count": window_count}
    return None


def mux_retire_pane(
    pane_id: str,
    *,
    mux: str | None = None,
    settle_timeout: float = 6.0,
    poll_interval: float = 0.3,
    ctrl_c_gap: float = 0.6,
    escalate_after: float = 1.5,
    hard_kill_settle: float = 1.5,
) -> dict:
    """Retire a specific pane by asking its Copilot to quit cleanly.

    Copilot CLI exits on a **double Ctrl-C** ~600 ms apart (a single one does
    little) -- its native clean-quit path (cf. :func:`graceful_quit_mux_session`).
    Unlike that session-scoped helper, this targets one ``pane_id`` so it retires
    the OLD Copilot after a cutover without touching the successor (the session's
    new active pane). Falls back to ``kill-pane`` if it does not exit in time.

    **Escalation ladder (up to three Ctrl-C).** Two interrupts is the common
    case, but some Copilot states swallow the second (mid-render, a modal, a busy
    turn flushing state) -- so after the double-interrupt we wait a brief
    ``escalate_after`` window and, only if the pane is still alive, deliver a
    conditional **third** Ctrl-C before the hard ``kill-pane`` fallback. This
    mirrors :func:`graceful_quit_mux_session` (a76ab47 / #2614) so a stubborn old
    pane is retired cleanly (persisting session state) instead of being severed,
    which is the failure mode behind a lingering un-retired pane (#3946).

    Returns ``{ok, pane, gone, method}`` where ``method`` is ``already-gone``,
    ``graceful``, ``hard``, or ``failed``.
    """
    import subprocess
    import time

    mux_bin = _mux_bin(mux)

    def _send(keys: str) -> bool:
        try:
            r = subprocess.run(
                [mux_bin, "send-keys", "-t", pane_id, keys],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _gone_within(window: float) -> bool:
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            if not _mux_pane_alive(pane_id, mux_bin):
                return True
            time.sleep(poll_interval)
        return not _mux_pane_alive(pane_id, mux_bin)

    if not _mux_pane_alive(pane_id, mux_bin):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "already-gone"}

    guard = _mux_last_window_guard(pane_id, mux_bin)
    if guard:
        try:
            from . import activity

            activity.log_event(
                "handoff_retire_guard",
                source="python",
                old_pane=pane_id,
                reason="last-window-skip",
                method="guard",
                outcome="left-running",
                mux_session=guard.get("session"),
                window_count=guard.get("window_count"),
            )
        except Exception:
            pass
        return {
            "ok": True, "pane": pane_id, "gone": False,
            "method": "last-window-skip", "session": guard.get("session"),
        }

    _send("C-c")
    time.sleep(ctrl_c_gap)
    _send("C-c")

    # Brief window for the double-interrupt to land before escalating.
    escalate_at = min(max(escalate_after, 0.0), settle_timeout)
    if _gone_within(escalate_at):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}

    # Still alive after two -- conditional third, then wait out the budget.
    _send("C-c")
    if _gone_within(settle_timeout - escalate_at):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}

    # Graceful quit did not land -- hard-kill the pane.
    try:
        subprocess.run(
            [mux_bin, "kill-pane", "-t", pane_id],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    gone = _gone_within(max(hard_kill_settle, 0.0))
    return {
        "ok": gone, "pane": pane_id, "gone": gone,
        "method": "hard" if gone else "failed",
    }
