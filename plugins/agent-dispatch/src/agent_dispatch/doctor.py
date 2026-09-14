"""agent-dispatch doctor (Boundary I / ThomasMichon/copilot-extensions#2577):
distinguish a genuinely *orphaned* held/suspended task from ordinary in-flight
work or merely ambiguous liveness, and offer one narrowly-scoped, explicit
repair.

Background: ``agent-dispatch health``'s ``liveness_gc`` loop already
classifies every held task's liveness, but a hibernating task (suspended,
its session deliberately torn down while a detached waiter blocks on some
external event -- see :mod:`agent_dispatch.hibernation`) is *correctly*
ambiguous there: an absent live session is the intended, healthy state of
that pattern, not evidence of death. The only signal strong enough to know a
hibernating task can *never* self-resolve is that its expected worktree is
now provably gone -- reclaimed by agent-worktrees, e.g. by the exact
finalize-vs-suspended-task race #2584 fixes going forward, or by any other
means (a manual ``rm``, an operator ``cleanup``). This module checks for
*that* specific, confirmed condition and, only there, offers to unbind the
task and return it to ``queued`` so a fresh attempt can proceed.

Every other case -- a merely stale lease, an indeterminate agent-worktrees
lookup, a task with no reservation to check -- is surfaced for visibility
but never auto-repaired. This mirrors the design constraint from #2577's
discussion: never re-create/re-queue on a guess, only on confirmed
deletion, and never race a second attempt against one still legitimately
in flight.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .procutil import agent_worktrees_launch_prefix, no_window_kwargs

if TYPE_CHECKING:
    from .client import DispatchClient

#: agent-worktrees tracking statuses that mean "this worktree is no longer a
#: live, resumable checkout" -- confirmed-gone for doctor purposes. A record
#: that agent-worktrees no longer tracks *at all* (pruned so thoroughly even
#: the tracking file is gone) is folded into the same bucket by
#: :func:`resolve_worktree` reporting ``{"status": "absent"}``.
GONE_STATUSES = frozenset({"finalized", "orphaned", "absent"})

#: How long a ``started`` task's lease may sit expired, with no reported
#: activity, before doctor calls it out as ``stale_lease``. Purely advisory
#: (never auto-repaired): only a confirmed-gone worktree justifies an
#: automatic repair.
DEFAULT_STALE_LEASE_GRACE_SECONDS = 3600.0

#: Task statuses doctor examines. ``queued``/``proposed`` have no owner or
#: reservation to diagnose; terminal statuses are out of scope entirely.
EXAMINED_STATUSES = ("claimed", "started", "suspended")

#: The one verdict :func:`repair` will act on automatically.
REPAIRABLE_VERDICT = "orphaned_worktree_gone"


@dataclass(frozen=True)
class Diagnosis:
    """One task's doctor verdict."""

    task_id: str
    status: str
    verdict: str  # "healthy" | "orphaned_worktree_gone" | "stale_lease" | "unknown"
    detail: str
    worktree_id: str | None = None
    reservation_key: str | None = None
    owner: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "verdict": self.verdict,
            "detail": self.detail,
            "worktree_id": self.worktree_id,
            "reservation_key": self.reservation_key,
            "owner": self.owner,
        }


def resolve_worktree(worktree_id: str, *, timeout: float = 15.0) -> dict | None:
    """Best-effort resolve one worktree's agent-worktrees tracking record.

    Returns ``None`` when the CLI is unavailable or the call fails outright
    -- an *indeterminate* result :func:`diagnose` must never treat as gone.
    An empty/absent record (agent-worktrees no longer tracks this worktree at
    all) reports back as ``{"status": "absent"}``, itself a confirmed-gone
    signal (see :data:`GONE_STATUSES`).
    """
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
            [*prefix, "list", "--json", "--all", "--worktree-id", worktree_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if result.returncode != 0 or not isinstance(payload, dict):
        return None
    worktrees = payload.get("worktrees")
    if not worktrees:
        return {"status": "absent"}
    return worktrees[0]


def diagnose(
    task: dict[str, Any],
    *,
    now: float | None = None,
    stale_lease_grace_seconds: float = DEFAULT_STALE_LEASE_GRACE_SECONDS,
    resolve: Callable[[str], dict | None] = resolve_worktree,
) -> Diagnosis:
    """Diagnose one held/suspended task dict (as returned by ``list``/``show``)."""
    now = time.time() if now is None else now
    task_id = task.get("id", "")
    status = task.get("status", "")
    owner = task.get("owner")
    reservation = task.get("spawn_reservation") or {}
    worktree_id = reservation.get("worktree")
    reservation_key = reservation.get("key")

    def result(verdict: str, detail: str) -> Diagnosis:
        return Diagnosis(
            task_id=task_id,
            status=status,
            verdict=verdict,
            detail=detail,
            worktree_id=worktree_id,
            reservation_key=reservation_key,
            owner=owner,
        )

    if worktree_id:
        wt = resolve(worktree_id)
        if wt is not None:
            wt_status = wt.get("status")
            if wt_status in GONE_STATUSES:
                return result(
                    REPAIRABLE_VERDICT,
                    f"worktree {worktree_id!r} is {wt_status} -- this task can never "
                    "resume there; safe to unbind and re-queue",
                )

    if status == "started":
        lease_expires_at = task.get("lease_expires_at")
        activity = task.get("activity")
        if lease_expires_at and not activity:
            stale_for = now - lease_expires_at
            if stale_for > stale_lease_grace_seconds:
                return result(
                    "stale_lease",
                    f"lease expired {stale_for:.0f}s ago with no reported activity -- "
                    "likely dead, but the worktree itself could not be confirmed gone; "
                    "not auto-repaired (verify manually, e.g. `agent-bridge status`)",
                )

    if worktree_id is None:
        return result("unknown", "no spawn reservation/worktree on record to check")

    return result("healthy", "no orphan signal found")


def repair(diagnosis: Diagnosis, client: DispatchClient, *, reason: str) -> dict[str, Any]:
    """Execute the one supported repair for a confirmed-orphaned task: fail its
    stale reservation (freeing the exclusive key for a fresh attempt) and
    unbind + re-queue the task itself. A no-op for every other verdict, by
    design -- see the module docstring for why this is the only condition
    doctor will act on automatically."""
    if diagnosis.verdict != REPAIRABLE_VERDICT:
        return {
            "task_id": diagnosis.task_id,
            "action": "skipped",
            "reason": f"verdict is {diagnosis.verdict!r}, not repairable",
        }
    actions: dict[str, Any] = {"task_id": diagnosis.task_id}
    if diagnosis.reservation_key:
        try:
            fail_result = client.fail_spawn(diagnosis.reservation_key, detail=reason)
            actions["reservation"] = {"state": fail_result.get("state")}
        except Exception as exc:
            actions["reservation"] = {"error": str(exc)}
    owner = diagnosis.owner or ""
    try:
        if diagnosis.status == "suspended":
            task = client.release(diagnosis.task_id, owner, reason=reason)
        else:
            task = client.yield_task(diagnosis.task_id, owner, note=reason)
        actions["task"] = {"status": task.get("status")}
    except Exception as exc:
        actions["task"] = {"error": str(exc)}
    return actions
