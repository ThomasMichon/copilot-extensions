"""Producer for the session-state lattice **bridge-lock** (#4272).

A bridge-owned Copilot session runs with ``cwd=home`` (it is not launched inside
the worktree), so it is invisible to the worktree picker's mux + registered-
session scans -- the #1416 blind spot. This module marks such a session's
liveness as a small file the picker reads cheaply: it shells to
``agent-worktrees session-lock write/remove`` (the same binstub agent-bridge
already uses for ``resolve`` / ``head-session``), which drops a provable-liveness
``bridge.lock`` -- keyed on the Copilot child's pid + start-time and carrying the
bound worktree id -- beside Copilot's own ``inuse.<pid>.lock``.

Best-effort throughout: never raises into the session flow, and a **missed
removal is harmless** -- the lock is provable-liveness, so a reader ignores it the
moment the child pid dies (a future ``doctor`` sweep tidies stale files).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess

from agent_procutil import no_window_flags

from .agent_registry import _agent_worktrees_bin

log = logging.getLogger(__name__)

# The CLI just writes/removes a tiny file -- fast. Bound the wait so a wedged
# binstub can never stall a session launch.
_TIMEOUT_S = 8.0


def _child_env() -> dict[str, str]:
    """Scrub our venv markers so the binstub runs in its own interpreter -- the
    same guard ``worktree_head`` / ``agent_registry`` use before shelling out."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PYTHONPATH")
    }


def _creationflags() -> int:
    return no_window_flags()


async def write(session_id: str, worktree_id: str | None, child_pid: int | None) -> None:
    """Mark a bridge-owned session ACTIVE for the picker (async, best-effort).

    No-ops unless all three are present (a remote/far-side child has no local
    pid to prove, and an unattributed session has nothing to mark). Awaits the
    short CLI so the write is durable before the session is announced, but never
    raises -- a missing binstub / non-zero exit / timeout is swallowed.
    """
    if not (session_id and worktree_id and child_pid):
        return
    exe = _agent_worktrees_bin()
    if not exe:
        log.debug("agent-worktrees binstub not found -- bridge-lock write skipped")
        return
    argv = [
        exe, "session-lock", "write",
        "--session", session_id,
        "--worktree", worktree_id,
        "--pid", str(child_pid),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=_child_env(),
            creationflags=_creationflags(),
        )
        await asyncio.wait_for(proc.wait(), timeout=_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never break a launch
        log.debug("bridge-lock write failed for %s: %s", session_id, exc)


def remove_sync(session_id: str) -> None:
    """Clear a session's bridge-lock (sync, fire-and-forget) at teardown.

    Called from the synchronous reap path, so it must not block the loop:
    spawns the CLI and does **not** wait. Purely tidiness -- a lingering lock is
    already ignored by the reader once the child pid dies -- so any failure
    (no binstub, spawn error) is silently fine.
    """
    if not session_id:
        return
    exe = _agent_worktrees_bin()
    if not exe:
        return
    argv = [exe, "session-lock", "remove", "--session", session_id]
    try:
        subprocess.Popen(  # noqa: S603 -- fixed argv, exe via shutil.which
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=_child_env(),
            creationflags=_creationflags(),
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort tidiness only
        log.debug("bridge-lock remove spawn failed for %s: %s", session_id, exc)
