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
behavior), and ``show``/``list`` render exactly as before.

**Cross-machine (Phase 8 Slice 8b).** The overlay resolves against the *owner's*
machine, not just the local one. An SSH-pushed dispatch (8a) lives on the
target's coordinator and embodies on the target's bridge, so its ``owner`` names
a *remote* machine. When that machine is not the local one, the live-session
resolve runs on it over the facility SSH mesh (``ssh <machine> agent-bridge ...``
-- the machine name **is** its facility alias, per the ``facility-ssh``
discipline), making a remote-dispatched task as observable as a local one. Still
best-effort: no ``ssh`` on PATH, an unreachable host, or a missing remote
``agent-bridge`` collapses to "no overlay", exactly like the local miss.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from typing import Any

from . import remote_dispatch

#: Sentinel distinguishing "local machine not yet computed" from a resolved
#: ``None`` (an unresolvable local identity is a valid, meaningful value).
_UNSET: Any = object()

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


def machine_from_owner(owner: str | None) -> str | None:
    """Extract the machine (its facility SSH alias) from a
    ``"<machine>/<worktree>"`` owner.

    Mirrors :func:`worktree_from_owner`. Returns None when the owner is unset or
    not in ``machine/worktree`` form. The machine name is the target's facility
    SSH alias (8a's SSH-push invariant), so it doubles as the mesh address for a
    cross-machine live-session resolve.
    """
    if not owner or "/" not in owner:
        return None
    machine, _sep, _worktree = owner.partition("/")
    return machine or None


def _bridge_resolve_argv(worktree: str, *, machine: str | None) -> list[str] | None:
    """Build the argv that resolves a worktree handle to its live session.

    Local (``machine`` None): the ``agent-bridge`` binstub directly. Remote:
    ``ssh <machine> agent-bridge ...`` over the facility SSH mesh -- the machine
    name is its alias. Returns None when the required client (``agent-bridge``
    locally, or ``ssh`` for a remote) is not on PATH, so the caller degrades.
    """
    remote_argv = ["agent-bridge", "--json", "live-sessions", "resolve",
                   "--handle", worktree]
    if machine is None:
        exe = shutil.which("agent-bridge")
        if exe is None:
            return None
        return [exe, *remote_argv[1:]]
    ssh = shutil.which("ssh")
    if ssh is None:
        return None
    remote_cmd = " ".join(shlex.quote(a) for a in remote_argv)
    # `machine` is the facility SSH alias (never a raw IP). BatchMode + a short
    # ConnectTimeout so an unreachable peer fails fast instead of hanging.
    return [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", machine, remote_cmd]


def resolve_live_session(
    worktree: str, *, machine: str | None = None, timeout: float | None = None
) -> dict[str, Any] | None:
    """Resolve a worktree handle to its live session via the agent-bridge CLI.

    Shells ``agent-bridge --json live-sessions resolve --handle <worktree>`` --
    the same shell-the-binstub pattern agent-dispatch uses for spawn, keeping the
    plugin decoupled (no cross-plugin import, no bridge URL/token discovery). When
    ``machine`` names a *remote* host, the same command runs **on that host** over
    the facility SSH mesh (Phase 8 Slice 8b). All failure modes (no CLI/ssh,
    non-zero exit, timeout, empty/invalid JSON, no live session) collapse to None
    so the caller degrades cleanly.
    """
    if not worktree:
        return None
    argv = _bridge_resolve_argv(worktree, machine=machine)
    if argv is None:
        return None
    if timeout is None:
        # A remote resolve adds an SSH round-trip, so allow a little more headroom.
        timeout = 6.0 if machine else 3.0
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            argv,
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
    task: Any,
    *,
    bridge_ok: bool | None = None,
    ssh_ok: bool | None = None,
    local: Any = _UNSET,
) -> Any:
    """Return ``task`` with an ``embodiment`` overlay when it is leased and its
    worktree resolves to a live session; otherwise return it unchanged.

    The overlay resolves against the *owner's* machine (Phase 8 Slice 8b): a
    local owner uses the local ``agent-bridge`` (gated on
    :func:`bridge_available`); a remote owner resolves over the SSH mesh (gated
    on :func:`~remote_dispatch.ssh_available`). A batch caller (``list``) hoists
    the one-time ``bridge_available`` / ``ssh_available`` / ``local_machine``
    probes so a lane of tasks makes at most one of each.
    """
    if not isinstance(task, dict) or task.get("status") not in _LEASED:
        return task
    owner = task.get("owner")
    worktree = worktree_from_owner(owner)
    if not worktree:
        return task
    machine = machine_from_owner(owner)
    if local is _UNSET:
        local = remote_dispatch.local_machine()
    is_remote = bool(machine) and bool(local) and machine != local
    if is_remote:
        if ssh_ok is None:
            ssh_ok = remote_dispatch.ssh_available()
        if not ssh_ok:
            return task
        session = resolve_live_session(worktree, machine=machine)
    else:
        if bridge_ok is None:
            bridge_ok = bridge_available()
        if not bridge_ok:
            return task
        session = resolve_live_session(worktree)
    overlay = embodiment_overlay(session)
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
        # Hoist the environment probes once for the whole batch: the local bridge
        # (local owners), ssh (remote owners), and this machine's identity (to
        # tell local from remote).
        bridge_ok = bridge_available()
        ssh_ok = remote_dispatch.ssh_available()
        local = remote_dispatch.local_machine()
        return [
            enrich_task(t, bridge_ok=bridge_ok, ssh_ok=ssh_ok, local=local)
            for t in tasks
        ]
    return enrich_task(tasks)
