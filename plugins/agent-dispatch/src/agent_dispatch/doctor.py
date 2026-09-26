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

**Reservation-history liveness (ThomasMichon/copilot-extensions#2884):** the
coordinator only ever attaches a task's *latest* spawn reservation (see
``coordinator.py``'s ``result["spawn_reservation"] = asdict(latest)``), so
the checks above -- like an operator reading ``owner``/``owner_session_id``
by hand -- only ever see the most recent attempt. A *later* attempt can die
while an *earlier* attempt's session is still alive and fully resumable, and
nothing re-checks that earlier attempt once a newer reservation shadows it.
``diagnose`` optionally accepts that task's full reservation history
(``DispatchClient.list_reservations(task_id=...)``) and, when given, checks
every attempt's actual embody-session liveness **by session id** (not the
task's ``owner`` worker_id/lane label, which reflects only the latest
attempt) before falling through to the worktree/lease checks above. Finding
a live earlier attempt is the single highest-priority verdict this module
reports, since it is both the most actionable ("resume that session") and
the one existing tooling was structurally blind to.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .procutil import agent_worktrees_launch_prefix, no_window_kwargs
from .queue_records import Status
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
#: live but shadowed by a later, dead/unknown attempt (see #2884).
EARLIER_ATTEMPT_LIVE_VERDICT = "earlier_attempt_live"

#: The verdict reported when a task's reservation-history fetch itself may be
#: incomplete (returned exactly :data:`_RESERVATION_HISTORY_LIMIT` rows --
#: the list API has no cursor, so this is the signal that more history could
#: exist beyond the page). Reported instead of guessing from a partial page,
#: since a truncated read could hide the very live earlier attempt this
#: check exists to find.
RESERVATION_HISTORY_TRUNCATED_VERDICT = "reservation_history_truncated"

#: Requested page size for a task's full spawn-reservation history. The
#: ``list_reservations`` API has no cursor/offset, only ``limit`` -- so this
#: is not true pagination, just a page large enough that no real task (bounded
#: by the engine's own max-attempts dead-lettering, ordinarily single digits)
#: could plausibly exceed it. If a task somehow *does* return exactly this
#: many rows, :data:`RESERVATION_HISTORY_TRUNCATED_VERDICT` is reported rather
#: than silently analyzing a possibly-incomplete page.
_RESERVATION_HISTORY_LIMIT = 100_000

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
    #: number, its confirmed-live embody session id, and (for a fleet body
    #: only) the pool host it's live on -- the facts an operator needs to
    #: actually recover it (``agent-bridge resume <live_session_id>``, run on
    #: ``live_host`` when set, then reattach a fresh task).
    live_attempt: int | None = None
    live_session_id: str | None = None
    live_host: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "verdict": self.verdict,
            "detail": self.detail,
            "worktree_id": self.worktree_id,
            "reservation_key": self.reservation_key,
            "owner": self.owner,
        }
        # Only present when this diagnosis actually carries live-attempt data
        # (verdict == EARLIER_ATTEMPT_LIVE_VERDICT) -- every other verdict's
        # JSON shape is unchanged from before reservation-history liveness
        # existed, preserving exact-consumer compatibility.
        if self.live_attempt is not None:
            out["live_attempt"] = self.live_attempt
            out["live_session_id"] = self.live_session_id
            out["live_host"] = self.live_host
        return out


def _default_local_session_verdict(session_id: str) -> str:
    from . import embody

    return embody.local_body_verdict(session_id)


def _default_fleet_session_verdict(host: str, session_id: str) -> str:
    from . import embody

    return embody.fleet_body_verdict(host, session_id)


def session_handle_verdict(
    session_handle: str | None,
    *,
    local_verdict: Callable[[str], str] = _default_local_session_verdict,
    fleet_verdict: Callable[[str, str], str] = _default_fleet_session_verdict,
) -> tuple[str, str | None, str | None]:
    """Decode one reservation ``session_handle`` and probe its liveness.

    Returns ``(verdict, session_id, host)``: ``host`` is set only for a
    decoded ``fleet-body:`` handle. An undecodable handle (a worktree-backed
    embody, or none at all) reports :data:`SESSION_UNKNOWN` with no session
    id/host -- never treated as death. Shared by the reservation-history walk
    below and any other caller (e.g. the CLI's abandon-liveness guard)
    that needs one handle's plain current-attempt liveness verdict.
    """
    fleet = _parse_fleet_body_handle(session_handle)
    if fleet is not None:
        host, sid = fleet
        return fleet_verdict(host, sid), sid, host
    local_sid = _parse_local_body_handle(session_handle)
    if local_sid is not None:
        return local_verdict(local_sid), local_sid, None
    return SESSION_UNKNOWN, None, None


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

    def _session_verdict(
        res: dict[str, Any],
    ) -> tuple[str, int | None, str | None, str | None]:
        verdict, sid, host = session_handle_verdict(
            res.get("session_handle"), local_verdict=local_verdict, fleet_verdict=fleet_verdict
        )
        return verdict, res.get("attempt"), sid, host

    latest_verdict, _, _, _ = _session_verdict(latest)
    if latest_verdict == SESSION_LIVE:
        return None  # the current attempt is itself alive -- nothing shadowed.

    for res in reversed(ordered[:-1]):
        verdict, attempt, session_id, host = _session_verdict(res)
        if verdict == SESSION_LIVE and session_id is not None:
            where = f" on host {host!r}" if host else ""
            return Diagnosis(
                task_id=str(latest.get("task_id") or ""),
                status="",  # filled in by the caller, which has the task row
                verdict=EARLIER_ATTEMPT_LIVE_VERDICT,
                detail=(
                    f"attempt {attempt} session {session_id!r} is live and "
                    f"resumable{where}, but attempt {latest.get('attempt')} "
                    "(the task's current tracked reservation) is "
                    f"{'unknown' if latest_verdict == SESSION_UNKNOWN else 'gone'} "
                    f"-- resume the earlier session (`agent-bridge resume "
                    f"{session_id}`{f' on {host}' if host else ''}), then "
                    "reattach a fresh task to it"
                ),
                reservation_key=latest.get("key"),
                live_attempt=attempt,
                live_session_id=session_id,
                live_host=host,
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
    :data:`EARLIER_ATTEMPT_LIVE_VERDICT` immediately -- the reservation-
    history gap this module exists to close (see #2884). Omitting
    ``reservations`` (the default) leaves every other check and the return
    shape byte-for-byte unchanged, so existing callers/tests are unaffected.
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
                live_host=history_diagnosis.live_host,
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
    doctor will act on automatically.

    A task diagnosed ``orphaned_worktree_gone`` that has *already concluded*
    (completed/confirmed/abandoned/dead-lettered -- see
    :data:`agent_dispatch.queue_records.Status.CONCLUDED`) is a distinct,
    narrower case: the task itself must never be re-queued (``yield_task``/
    ``release`` on an already-concluded task is an invalid transition), but its
    stale spawn reservation can still be left permanently stuck in
    ``releasing``, fencing its ``exclusive_key`` forever with nothing left to
    ever revisit it (copilot-extensions#3025). ``resolve_worktree`` having
    already reported this worktree gone (the same positive evidence
    :data:`REPAIRABLE_VERDICT` is built on) is exactly the independent
    absence proof ``fail_spawn(force=True, confirmed_absent=True)`` requires,
    so this path clears only the reservation and explicitly skips the task
    transition rather than attempting and swallowing an invalid one."""
    if diagnosis.verdict != REPAIRABLE_VERDICT:
        return {
            "task_id": diagnosis.task_id,
            "action": "skipped",
            "reason": f"verdict is {diagnosis.verdict!r}, not repairable",
        }
    actions: dict[str, Any] = {"task_id": diagnosis.task_id}
    task_is_terminal = diagnosis.status in Status.CONCLUDED
    if diagnosis.reservation_key:
        try:
            fail_result = client.fail_spawn(
                diagnosis.reservation_key,
                detail=reason,
                force=task_is_terminal,
                confirmed_absent=task_is_terminal,
            )
            actions["reservation"] = {"state": fail_result.get("state")}
        except Exception as exc:
            actions["reservation"] = {"error": str(exc)}
    if task_is_terminal:
        actions["task"] = {
            "skipped": f"status {diagnosis.status!r} is already terminal -- only "
            "the stale reservation was cleared, no task transition attempted"
        }
        return actions
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
        reservations = None
        truncated = False
        if check_live_sessions:
            reservations = client.list_reservations(
                task_id=t.get("id"), limit=_RESERVATION_HISTORY_LIMIT
            )
            truncated = len(reservations) >= _RESERVATION_HISTORY_LIMIT
        if truncated:
            diagnoses.append(
                Diagnosis(
                    task_id=str(t.get("id") or ""),
                    status=str(t.get("status") or ""),
                    verdict=RESERVATION_HISTORY_TRUNCATED_VERDICT,
                    detail=(
                        f"this task's reservation history returned "
                        f"{_RESERVATION_HISTORY_LIMIT} rows (the page limit) -- "
                        "it may be incomplete, so the earlier-attempt-liveness "
                        "check was skipped rather than risk missing a live "
                        "session; investigate this task's attempt count "
                        "directly"
                    ),
                    owner=t.get("owner"),
                )
            )
            continue
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
            # `--task` intentionally fetches a task of *any* status (unlike
            # the repo/label sweep, which is pre-filtered to
            # EXAMINED_STATUSES). A concluded task (completed/confirmed/
            # abandoned/dead_letter) whose old worktree happens to resolve as
            # gone is now also handed to `repair()` -- which, for exactly
            # that concluded-status case, clears only the stale reservation
            # (never attempting the invalid `yield_task`/`release` task
            # transition; see `repair()`'s own concluded-status branch,
            # copilot-extensions#3025). Any other non-examined,
            # non-concluded status (e.g. `queued`/`proposed`, which have no
            # owner/reservation to repair in the first place) is still
            # excluded.
            if d.verdict == REPAIRABLE_VERDICT
            and (d.status in EXAMINED_STATUSES or d.status in Status.CONCLUDED)
        ]
    return payload
