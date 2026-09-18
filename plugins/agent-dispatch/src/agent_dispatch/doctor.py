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

**Reservation-history liveness (Phase 9 /
``efforts/active/agent-dispatch-embody-supervisor``, aperture-labs#7133):**
the coordinator only ever attaches a task's *latest* spawn reservation (see
``coordinator.py``'s ``result["spawn_reservation"] = asdict(latest)``), so
the checks above -- like an operator reading ``owner``/``owner_session_id``
by hand -- only ever see the most recent attempt. A live 2026-09-18 incident
showed the real gap: a *later* attempt can die while an *earlier* attempt's
session is still alive and fully resumable, and nothing re-checks that
earlier attempt once a newer reservation shadows it. ``diagnose`` optionally
accepts that task's full reservation history (``DispatchClient.
list_reservations(task_id=...)``) and, when given, checks every attempt's
actual embody-session liveness **by session id** (not the task's ``owner``
worker_id/lane label -- that distinction is what tripped up the incident's
own manual diagnosis) before falling through to the worktree/lease checks
above. Finding a live earlier attempt is the single highest-priority verdict
this module reports, since it is both the most actionable ("resume that
session") and the one existing tooling was structurally blind to.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .procutil import agent_worktrees_launch_prefix, no_window_kwargs
from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle

if TYPE_CHECKING:
    from .client import DispatchClient

#: Tri-state embody-session liveness verdicts (mirror ``tracking.LIVE/GONE/
#: UNKNOWN``); duplicated here so this module takes no import dependency on
#: the resolver, matching the rest of the engine's degrade-safe convention.
SESSION_LIVE = "live"
SESSION_GONE = "gone"
SESSION_UNKNOWN = "unknown"

#: The verdict reported when an earlier spawn attempt's session is confirmed
#: live but shadowed by a later, dead/unknown attempt -- the Phase 9 gap.
EARLIER_ATTEMPT_LIVE_VERDICT = "earlier_attempt_live"

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
    # "healthy" | "orphaned_worktree_gone" | "stale_lease" | "unknown" |
    # "earlier_attempt_live"
    verdict: str
    detail: str
    worktree_id: str | None = None
    reservation_key: str | None = None
    owner: str | None = None
    #: Set only for :data:`EARLIER_ATTEMPT_LIVE_VERDICT`: the earlier attempt
    #: number and its confirmed-live embody session id, the two facts an
    #: operator needs to actually recover it (``agent-bridge resume
    #: <live_session_id>``, then reattach a fresh task).
    live_attempt: int | None = None
    live_session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "verdict": self.verdict,
            "detail": self.detail,
            "worktree_id": self.worktree_id,
            "reservation_key": self.reservation_key,
            "owner": self.owner,
            "live_attempt": self.live_attempt,
            "live_session_id": self.live_session_id,
        }


def _default_local_session_verdict(session_id: str) -> str:
    from . import embody

    return embody.local_body_verdict(session_id)


def _default_fleet_session_verdict(host: str, session_id: str) -> str:
    from . import embody

    return embody.fleet_body_verdict(host, session_id)


def _reservation_session_liveness(
    reservations: list[dict[str, Any]],
    *,
    local_verdict: Callable[[str], str],
    fleet_verdict: Callable[[str, str], str],
) -> Diagnosis | None:
    """Walk a task's full spawn-reservation history, newest attempt last, and
    report an :data:`EARLIER_ATTEMPT_LIVE_VERDICT` diagnosis iff the *latest*
    attempt's session is not confirmed live but an *earlier* attempt's is.

    Returns ``None`` when there is nothing actionable here (no reservations,
    the latest attempt is itself live, or no attempt has a live session) --
    the caller falls through to the ordinary worktree/lease checks in that
    case. Never raises: a liveness probe failure is :data:`SESSION_UNKNOWN`,
    never treated as death, mirroring the supervisor's own liveness GC.
    """
    if not reservations:
        return None
    ordered = sorted(reservations, key=lambda r: r.get("attempt") or 0)
    latest = ordered[-1]

    def _session_verdict(res: dict[str, Any]) -> tuple[str, int | None, str | None]:
        handle = res.get("session_handle")
        fleet = _parse_fleet_body_handle(handle)
        local_sid = _parse_local_body_handle(handle)
        if fleet is not None:
            host, sid = fleet
            return fleet_verdict(host, sid), res.get("attempt"), sid
        if local_sid is not None:
            return local_verdict(local_sid), res.get("attempt"), local_sid
        return SESSION_UNKNOWN, res.get("attempt"), None

    latest_verdict, _, _ = _session_verdict(latest)
    if latest_verdict == SESSION_LIVE:
        return None  # the current attempt is itself alive -- nothing shadowed.

    for res in reversed(ordered[:-1]):
        verdict, attempt, session_id = _session_verdict(res)
        if verdict == SESSION_LIVE and session_id is not None:
            return Diagnosis(
                task_id=str(latest.get("task_id") or ""),
                status="",  # filled in by the caller, which has the task row
                verdict=EARLIER_ATTEMPT_LIVE_VERDICT,
                detail=(
                    f"attempt {attempt} session {session_id!r} is live and "
                    f"resumable, but attempt {latest.get('attempt')} (the task's "
                    "current tracked reservation) is "
                    f"{'unknown' if latest_verdict == SESSION_UNKNOWN else 'gone'} "
                    "-- resume the earlier session (`agent-bridge resume "
                    f"{session_id}`), then reattach a fresh task to it"
                ),
                reservation_key=latest.get("key"),
                live_attempt=attempt,
                live_session_id=session_id,
            )
    return None


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
    reservations: list[dict[str, Any]] | None = None,
    local_session_verdict: Callable[[str], str] = _default_local_session_verdict,
    fleet_session_verdict: Callable[[str, str], str] = _default_fleet_session_verdict,
) -> Diagnosis:
    """Diagnose one held/suspended task dict (as returned by ``list``/``show``).

    ``reservations`` is optional and, when given (that task's full
    ``DispatchClient.list_reservations(task_id=...)`` history, any order),
    checked **first**: if an earlier attempt's embody session is confirmed
    live while the task's current (latest) reservation is not, this reports
    :data:`EARLIER_ATTEMPT_LIVE_VERDICT` immediately -- the Phase 9 gap this
    module exists to close. Omitting ``reservations`` (the default) leaves
    every other check and the return shape byte-for-byte unchanged, so
    existing callers/tests are unaffected.
    """
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

    if reservations:
        history_diagnosis = _reservation_session_liveness(
            reservations,
            local_verdict=local_session_verdict,
            fleet_verdict=fleet_session_verdict,
        )
        if history_diagnosis is not None:
            # `_reservation_session_liveness` doesn't know this task's status
            # (it only sees reservations) -- stamp it in from the task row.
            return Diagnosis(
                task_id=task_id or history_diagnosis.task_id,
                status=status,
                verdict=history_diagnosis.verdict,
                detail=history_diagnosis.detail,
                worktree_id=worktree_id,
                reservation_key=history_diagnosis.reservation_key or reservation_key,
                owner=owner,
                live_attempt=history_diagnosis.live_attempt,
                live_session_id=history_diagnosis.live_session_id,
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


def diagnose_many(
    client: DispatchClient,
    tasks: list[dict[str, Any]],
    *,
    check_live_sessions: bool = False,
    stale_lease_seconds: float = DEFAULT_STALE_LEASE_GRACE_SECONDS,
    repair_orphaned: bool = False,
) -> dict[str, Any]:
    """Diagnose (and optionally repair) a batch of tasks -- the shared body
    behind the ``agent-dispatch doctor`` CLI command, extracted so the CLI
    wrapper itself stays a thin arg-resolution shim (see
    ``__main__._cmd_doctor``).

    ``check_live_sessions`` opts into fetching each task's full
    ``list_reservations(task_id=...)`` history and probing per-attempt
    session liveness (see :func:`_reservation_session_liveness`) -- one
    extra HTTP call plus one-or-more bridge probes per task, so it stays
    opt-in rather than the doctor sweep's default behavior.

    Returns the JSON-serializable payload the CLI emits directly:
    ``examined`` / ``diagnoses`` (+ ``repaired`` when ``repair_orphaned``).
    """
    diagnoses = []
    for t in tasks:
        reservations = (
            client.list_reservations(task_id=t.get("id"), limit=1000)
            if check_live_sessions
            else None
        )
        diagnoses.append(
            diagnose(
                t,
                stale_lease_grace_seconds=stale_lease_seconds,
                # An explicit, call-time module-attribute lookup (not
                # diagnose's own default parameter, which -- like any Python
                # default -- binds once at def-time): this is what makes
                # `monkeypatch.setattr(doctor, "resolve_worktree", ...)`
                # actually take effect for callers of this function.
                resolve=resolve_worktree,
                reservations=reservations,
                local_session_verdict=_default_local_session_verdict,
                fleet_session_verdict=_default_fleet_session_verdict,
            )
        )
    payload: dict[str, Any] = {
        "examined": len(diagnoses),
        "diagnoses": [d.as_dict() for d in diagnoses],
    }
    if repair_orphaned:
        payload["repaired"] = [
            repair(d, client, reason=f"agent-dispatch doctor: {d.detail}")
            for d in diagnoses
            if d.verdict == REPAIRABLE_VERDICT
        ]
    return payload
