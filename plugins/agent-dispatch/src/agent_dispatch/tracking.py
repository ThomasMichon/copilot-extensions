"""Best-effort embodiment tracking: overlay a leased task's live CLI-session
status from the agent-bridge live-session registry.

The join key already exists -- a claimed task's ``owner`` is
``"<machine>/<worktree>"`` (see :func:`queue.worker_id_for`), and agent-bridge's
live-session registry is keyed by ``worktree_id``. So we resolve the worktree
handle to its live session and surface a **read-only** liveness overlay, making a
CLI-embodied task as trackable as a headless one (closes
``visions/agent-fabric`` behavior *lifetime-decides-embodiment*: a durable CLI
body is "trackable by task coordination").

Derived on read -- agent-dispatch persists no session state and gains no
heartbeat writer. Purely best-effort: if ``agent-bridge`` is absent or
unreachable, tracking is simply unavailable (the *discover-and-degrade*
behavior), and ``show``/``list`` render exactly as before. Same-machine only:
the owner worktree must resolve against the local bridge; cross-machine tracking
rides the future bridge-to-bridge transport.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

#: Task states a worker actively holds -- only these have a live embodiment.
_LEASED = frozenset({"claimed", "started"})

#: Fields carried into the compact read-only overlay.
_OVERLAY_KEYS = (
    "session_id",
    "worktree_id",
    "driven_by",
    "status",
    "turn_state",
    "liveness",
    "updated_at",
)


def bridge_available() -> bool:
    """True if the ``agent-bridge`` CLI is on PATH."""
    return shutil.which("agent-bridge") is not None


def worktree_from_owner(owner: str | None) -> str | None:
    """Extract the worktree handle from a ``"<machine>/<worktree>"`` owner.

    Mirrors :func:`queue.worker_id_for`. Returns None when the owner is unset or
    not in ``machine/worktree`` form.
    """
    if not owner or "/" not in owner:
        return None
    _machine, _sep, worktree = owner.partition("/")
    return worktree or None


def resolve_live_session(
    worktree: str, *, timeout: float = 3.0
) -> dict[str, Any] | None:
    """Resolve a worktree handle to its live session via the agent-bridge CLI.

    Shells ``agent-bridge --json live-sessions resolve --handle <worktree>`` --
    the same shell-the-binstub pattern agent-dispatch uses for spawn, keeping the
    plugin decoupled (no cross-plugin import, no bridge URL/token discovery). All
    failure modes (no CLI, non-zero exit, timeout, empty/invalid JSON, no live
    session) collapse to None so the caller degrades cleanly.
    """
    exe = shutil.which("agent-bridge")
    if exe is None or not worktree:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "--json", "live-sessions", "resolve", "--handle", worktree],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data:
        return None
    return data


def embodiment_overlay(session: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a compact read-only overlay from a live-session dict, or None."""
    if not session:
        return None
    overlay = {k: session[k] for k in _OVERLAY_KEYS if session.get(k) is not None}
    return overlay or None


def enrich_task(
    task: Any, *, bridge_ok: bool | None = None
) -> Any:
    """Return ``task`` with an ``embodiment`` overlay when it is leased and its
    worktree resolves to a live session; otherwise return it unchanged.

    ``bridge_ok`` lets a batch caller (``list``) hoist the one-time
    ``bridge_available()`` check so a lane full of tasks makes at most one
    ``which`` call and only resolves the (few) leased tasks.
    """
    if not isinstance(task, dict) or task.get("status") not in _LEASED:
        return task
    if bridge_ok is None:
        bridge_ok = bridge_available()
    if not bridge_ok:
        return task
    worktree = worktree_from_owner(task.get("owner"))
    if not worktree:
        return task
    overlay = embodiment_overlay(resolve_live_session(worktree))
    if overlay is None:
        return task
    return {**task, "embodiment": overlay}


def enrich_tasks(tasks: Any) -> Any:
    """Best-effort embodiment enrichment over a task or a list of tasks."""
    if isinstance(tasks, list):
        if not any(
            isinstance(t, dict) and t.get("status") in _LEASED for t in tasks
        ):
            return tasks
        bridge_ok = bridge_available()
        return [enrich_task(t, bridge_ok=bridge_ok) for t in tasks]
    return enrich_task(tasks)
