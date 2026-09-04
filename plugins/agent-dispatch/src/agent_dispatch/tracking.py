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
resolve runs on it over the SSH mesh (``ssh <machine> agent-bridge ...``
-- the machine name **is** its SSH alias, per the SSH-alias discipline
discipline), making a remote-dispatched task as observable as a local one. Still
best-effort: no ``ssh`` on PATH, an unreachable host, or a missing remote
``agent-bridge`` collapses to "no overlay", exactly like the local miss.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from typing import Any

from . import bridge_remote, remote_dispatch
from .procutil import (
    agent_bridge_launch_prefix,
    run_agent_worktrees_capture,
    run_background_capture,
    run_ssh_capture,
)

#: Sentinel distinguishing "local machine not yet computed" from a resolved
#: ``None`` (an unresolvable local identity is a valid, meaningful value).
_UNSET: Any = object()

#: Task states a worker actively holds -- only these have a live embodiment.
_LEASED = frozenset({"claimed", "started"})

#: Total wall-clock budget (seconds) for a `list`'s embodiment enrichment across
#: a whole batch. The overlay is display-only, so exceeding it degrades to
#: unenriched output rather than hanging (dotfiles #1704). Overridable via
#: ``$AGENT_DISPATCH_ENRICH_BUDGET_S``.
_ENRICH_BUDGET_S = 4.0


def _enrich_budget() -> float:
    """The embodiment-enrichment total budget (env-overridable, positive)."""
    raw = os.environ.get("AGENT_DISPATCH_ENRICH_BUDGET_S")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _ENRICH_BUDGET_S

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
    """True if the local ``agent-bridge`` runtime can be launched safely."""
    return agent_bridge_launch_prefix() is not None


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
    """Extract the machine (its SSH alias) from a
    ``"<machine>/<worktree>"`` owner.

    Mirrors :func:`worktree_from_owner`. Returns None when the owner is unset or
    not in ``machine/worktree`` form. The machine name is the target's
    SSH alias (8a's SSH-push invariant), so it doubles as the mesh address for a
    cross-machine live-session resolve.
    """
    if not owner or "/" not in owner:
        return None
    machine, _sep, _worktree = owner.partition("/")
    return machine or None


def _bridge_resolve_argv(worktree: str, *, machine: str | None) -> list[str] | None:
    """Build the argv that resolves a worktree handle to its live session.

    Local (``machine`` None): the installed ``agent-bridge`` module directly. Remote:
    ``ssh <machine> agent-bridge ...`` over the SSH mesh -- the machine
    name is its alias. Returns None when the required client (``agent-bridge``
    locally, or ``ssh`` for a remote) is not on PATH, so the caller degrades.
    """
    remote_argv = ["agent-bridge", "--json", "live-sessions", "resolve",
                   "--handle", worktree]
    if machine is None:
        prefix = agent_bridge_launch_prefix()
        if prefix is None:
            return None
        return [*prefix, *remote_argv[1:]]
    ssh = shutil.which("ssh")
    if ssh is None:
        return None
    remote_cmd = " ".join(shlex.quote(a) for a in remote_argv)
    # `machine` is the SSH alias (never a raw IP). BatchMode + a short
    # ConnectTimeout so an unreachable peer fails fast instead of hanging.
    return [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", machine, remote_cmd]


def _run_capture(
    argv: list[str], *, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    """Run a passive probe without allocating a headed Windows console."""
    if os.path.basename(argv[0]).casefold() in {"ssh", "ssh.exe"}:
        return run_ssh_capture(argv, timeout=timeout)
    return run_background_capture(argv, timeout=timeout)


def resolve_live_session(
    worktree: str, *, machine: str | None = None, timeout: float | None = None
) -> dict[str, Any] | None:
    """Resolve a worktree handle through Agent Bridge.

    Remote owners use the local Bridge carrier first and shell the remote
    ``agent-bridge`` binstub over SSH only when that optional capability is
    absent. Local owners use the local binstub directly. All failures collapse
    to ``None`` so display-only enrichment degrades cleanly.
    """
    if not worktree:
        return None
    if machine is not None:
        effective_timeout = timeout if timeout is not None else 6.0
        try:
            data = bridge_remote.LocalBridgeRemoteClient().resolve_live_session(
                machine,
                worktree,
                timeout=effective_timeout,
            )
            if not isinstance(data, dict) or not data:
                return None
            return data
        except bridge_remote.RemoteBridgeUnavailable:
            pass
        except bridge_remote.RemoteBridgeOperationError:
            return None
    argv = _bridge_resolve_argv(worktree, machine=machine)
    if argv is None:
        return None
    if timeout is None:
        # A remote resolve adds an SSH round-trip, so allow a little more headroom.
        timeout = 6.0 if machine else 3.0
    proc = _run_capture(argv, timeout=timeout)
    if proc is None:
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


def list_local_body_sessions(*, timeout: float = 3.0) -> list[dict[str, Any]]:
    """List local headless sessions; observation failures degrade to no rows."""
    prefix = agent_bridge_launch_prefix()
    if prefix is None:
        return []
    proc = _run_capture([*prefix, "--json", "sessions"], timeout=timeout)
    if proc is None:
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def embodiment_overlay(session: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a compact read-only overlay from a live-session dict, or None."""
    if not session:
        return None
    overlay = {k: session[k] for k in _OVERLAY_KEYS if session.get(k) is not None}
    return overlay or None


def session_activity(session: dict[str, Any] | None) -> str | None:
    """Map an agent-bridge session snapshot to the task activity vocabulary."""
    if not session:
        return None
    liveness = str(session.get("liveness") or "").lower()
    status = str(session.get("status") or "").lower()
    turn_state = str(session.get("turn_state") or "").lower()
    if liveness == "stalled":
        return "STALLED"
    if liveness == "active" or turn_state == "running":
        return "ACTIVE"
    if status == "idle" or liveness == "idle" or turn_state == "idle":
        return "IDLE"
    if status in {"starting", "running"} and liveness not in {"idle", "stalled"}:
        return "ACTIVE"
    return None


def enrich_local_body_tasks(tasks: Any, reservations: Any) -> Any:
    """Overlay local headless ACP sessions using spawned reservation handles.

    Headless bodies register in ``agent-bridge sessions``, not the interactive
    ``live-sessions`` registry used by :func:`enrich_tasks`. The coordinator's
    spawned reservation is the authoritative task -> ``local-body:<session-id>``
    join. One bridge list call enriches the whole board batch.
    """
    if not isinstance(tasks, list) or not isinstance(reservations, list):
        return tasks
    task_to_session: dict[str, str] = {}
    for res in reservations:
        if not isinstance(res, dict) or res.get("state") != "spawned":
            continue
        handle = str(res.get("session_handle") or "")
        if not handle.startswith("local-body:"):
            continue
        session_id = handle.removeprefix("local-body:")
        task_id = str(res.get("task_id") or "")
        if task_id and session_id:
            task_to_session[task_id] = session_id
    if not task_to_session:
        return tasks
    sessions = list_local_body_sessions()
    by_id = {
        str(row.get("session_id")): row
        for row in sessions
        if row.get("session_id")
    }
    out = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("embodiment"):
            out.append(task)
            continue
        session = by_id.get(task_to_session.get(str(task.get("id")), ""))
        overlay = embodiment_overlay(session)
        out.append({**task, "embodiment": overlay} if overlay else task)
    return out


#: The three liveness verdicts a GC reconcile acts on. ``LIVE`` and ``GONE`` are
#: *positive* answers from a working resolver; ``UNKNOWN`` means the resolver
#: could not answer (no CLI/ssh, unreachable bridge, timeout, error) -- the
#: caller must treat it as "can't tell", never as "gone".
LIVE = "live"
GONE = "gone"
UNKNOWN = "unknown"


def liveness_verdict(
    worktree: str | None,
    *,
    machine: str | None = None,
    owner_session_id: str | None = None,
    timeout: float | None = None,
) -> str:
    """Resolve a task owner's liveness to a **tri-state, identity-keyed** verdict.

    The safety crux GC depends on. It is keyed on the owner's **session
    identity**, not mere worktree occupancy -- because a worktree is reused across
    sessions, so "a session occupies the worktree" does not mean "*this task's*
    owner is alive." Two failure modes this closes: a reused worktree hiding a
    gone owner (false ``live``), and a claim-before-registration race requeuing a
    live owner (false ``gone``).

    Given the task's captured ``owner_session_id``:

    - :data:`LIVE` -- the resolver answered and the worktree's **current** live
      session **is** ``owner_session_id`` (the same owner is still there).
    - :data:`GONE` -- the resolver answered authoritatively and the worktree is
      **empty** (``{}``) or holds a **different** session id than
      ``owner_session_id`` (a new session reused the worktree; our owner is gone).
    - :data:`UNKNOWN` -- the resolver could not answer (no CLI/ssh, non-zero exit,
      timeout, unparseable/non-object output -- a possibly restarting/partial
      registry), **or** ``owner_session_id`` is not captured yet (the claim ->
      register window). GC leaves the task alone (degrade safe; never requeue on
      ignorance or an unattributable snapshot).

    Never raises. Mirrors :func:`resolve_live_session`'s transport (local
    ``agent-bridge``; a remote owner over ``ssh <machine> agent-bridge``).
    """
    if not worktree:
        return UNKNOWN
    argv = _bridge_resolve_argv(worktree, machine=machine)
    if argv is None:
        return UNKNOWN
    if timeout is None:
        timeout = 6.0 if machine else 3.0
    proc = _run_capture(argv, timeout=timeout)
    if proc is None:
        return UNKNOWN
    if proc.returncode != 0:
        return UNKNOWN  # bridge/ssh errored -- can't tell, not "gone"
    out = proc.stdout.strip()
    if not out:
        return UNKNOWN  # exit 0 but silent -- ambiguous, treat as can't-tell
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN
    # The resolver answered. Without a captured owner identity we cannot safely
    # attribute the worktree's state to this task's owner -> can't-tell.
    if owner_session_id is None:
        return UNKNOWN
    if not data:
        return GONE  # `{}` (CLI 404): the worktree is empty -> our owner is gone
    current = data.get("session_id") or data.get("id")
    if current is None:
        return UNKNOWN  # answered but unattributable
    return LIVE if current == owner_session_id else GONE


def live_worktrees(*, timeout: float = 5.0) -> set[str] | None:
    """The set of worktree ids currently **live** on this machine.

    Shells ``agent-worktrees list --json --tracking-status active`` -- the
    active-tracking, directory-present worktrees (a pruned/finalized/orphaned
    worktree is excluded). Used by the liveness GC to reap **unowned**
    proposed/queued tasks pinned to a worktree that no longer exists (see
    :meth:`agent_dispatch.queue.TaskQueue.reap_orphaned_targets`).

    Returns ``None`` on **any** failure (no CLI, non-zero exit, timeout, empty or
    unparseable output) so the caller degrades safe -- an unresolved probe reaps
    nothing, never on ignorance. Never raises.
    """
    proc = run_agent_worktrees_capture(
        "list", "--json", "--tracking-status", "active", timeout=timeout
    )
    if proc is None:
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    wts = data.get("worktrees", data) if isinstance(data, dict) else data
    if not isinstance(wts, list):
        return None
    return {
        str(w["id"]) for w in wts
        if isinstance(w, dict) and w.get("id")
    }


def enrich_task(
    task: Any,
    *,
    bridge_ok: bool | None = None,
    local: Any = _UNSET,
) -> Any:
    """Return ``task`` with an ``embodiment`` overlay when it is leased and its
    worktree resolves to a live session; otherwise return it unchanged.

    The overlay resolves against the *owner's* machine (Phase 8 Slice 8b): a
    local owner uses the local ``agent-bridge`` (gated on
    :func:`bridge_available`); a remote owner first uses the local Bridge carrier
    and resolves SSH only if that optional capability is absent. A batch caller
    (``list``) hoists local Bridge and machine-identity probes.
    """
    if not isinstance(task, dict) or task.get("status") not in _LEASED:
        return task
    owner = task.get("owner")
    worktree = worktree_from_owner(owner)
    if not worktree:
        return task
    machine = machine_from_owner(owner)
    if local is _UNSET:
        is_remote = remote_dispatch.is_peer_machine(machine)
    else:
        is_remote = (
            bool(machine)
            and bool(local)
            and machine.strip().casefold() != str(local).strip().casefold()
        )
    if is_remote:
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
    """Best-effort embodiment enrichment over a task or a list of tasks.

    Bounded by a **total** wall-clock budget (:func:`_enrich_budget`): the
    embodiment overlay is display-only, so once a batch has spent the budget
    resolving live sessions we stop probing and return the remaining leased tasks
    **unenriched** rather than let one slow/hanging bridge-or-ssh resolve wedge
    the whole ``list`` (dotfiles #1704). Each individual resolve is already
    per-call bounded (see :func:`resolve_live_session`); this caps the *aggregate*
    so total time is ~budget + at most one per-call timeout, independent of how
    many (esp. stale) leased tasks are in the lane.
    """
    if isinstance(tasks, list):
        if not any(
            isinstance(t, dict) and t.get("status") in _LEASED for t in tasks
        ):
            return tasks
        # Hoist the local Bridge and machine-identity probes once for the batch.
        bridge_ok = bridge_available()
        local = remote_dispatch.local_machine()
        deadline = time.monotonic() + _enrich_budget()
        out = []
        for t in tasks:
            # Only leased tasks incur a (bounded) resolve; a non-leased task passes
            # through enrich_task cheaply. Skip the probe for a leased task once
            # the batch budget is spent -> leave it unenriched (display-only).
            if (
                isinstance(t, dict)
                and t.get("status") in _LEASED
                and time.monotonic() >= deadline
            ):
                out.append(t)
                continue
            out.append(
                enrich_task(t, bridge_ok=bridge_ok, local=local)
            )
        return out
    return enrich_task(tasks)
