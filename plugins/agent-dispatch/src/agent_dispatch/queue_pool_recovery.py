"""Affirmative suspended recovery on the existing assignment and wake outbox."""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from .queue_common import Task
from .queue_pool_startup import (
    COOLDOWN_SECONDS, DECISION_SECONDS, NUDGE_SECONDS, STARTUP_ATTEMPTS, begin_pool_episode,
)
from .queue_records import SpawnState, Status, TaskError


def recovery_assignment(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id=? AND assignment_released=0",
        (task_id,),
    ).fetchone()


def pause_recovery(conn: sqlite3.Connection, task_id: str) -> None:
    """A renewed intentional wait fences an older recovery without erasing debt."""
    changed = conn.execute(
        "UPDATE task_pool_assignments SET recovery_revision=recovery_revision+1,"
        " recovery_requested_at=NULL, recovery_wake_id=NULL, recovery_hold_started_at=NULL"
        " WHERE task_id=? AND recovery_requested_at IS NOT NULL", (task_id,),
    ).rowcount
    if changed:
        conn.execute("UPDATE tasks SET resume_requested=0 WHERE id=?", (task_id,))


def hold_recovery(conn: sqlite3.Connection, task_id: str, *, held: bool, ts: float) -> None:
    """Pause only this policy's clocks; holds spend no attempt or cooldown."""
    assignment = recovery_assignment(conn, task_id)
    if assignment is None or assignment["recovery_requested_at"] is None:
        return
    if held:
        conn.execute(
            "UPDATE task_pool_assignments SET recovery_hold_started_at="
            "COALESCE(recovery_hold_started_at, ?) WHERE task_id=?", (ts, task_id),
        )
    elif assignment["recovery_hold_started_at"] is not None:
        delay = max(0.0, ts - assignment["recovery_hold_started_at"])
        wake = conn.execute(
            "SELECT delivered_at FROM wake_outbox WHERE id=?", (assignment["recovery_wake_id"],),
        ).fetchone()
        if (
            assignment["reachable_at"] is None and wake is not None
            and wake["delivered_at"] is not None
            and wake["delivered_at"] < assignment["startup_deadline"] + delay
        ):
            delivered = wake["delivered_at"]
            decision_pause = max(0.0, ts - max(assignment["recovery_hold_started_at"], delivered))
            conn.execute(
                "UPDATE task_pool_assignments SET reachable_at=?, next_nudge_at=?,"
                " decision_deadline=? WHERE task_id=?",
                (delivered, delivered + NUDGE_SECONDS + decision_pause,
                 delivered + DECISION_SECONDS + decision_pause, task_id),
            )
            conn.execute(
                "UPDATE task_pool_assignments SET recovery_hold_started_at=NULL,"
                " startup_deadline=startup_deadline+? WHERE task_id=?", (delay, task_id),
            )
            return
        conn.execute(
            "UPDATE task_pool_assignments SET recovery_hold_started_at=NULL,"
            " startup_deadline=startup_deadline+?, decision_deadline=decision_deadline+?,"
            " next_nudge_at=next_nudge_at+? WHERE task_id=?", (delay, delay, delay, task_id),
        )


def _decision_deadline(conn: sqlite3.Connection, assignment: sqlite3.Row) -> float | None:
    if assignment["decision_deadline"] is not None:
        return assignment["decision_deadline"]
    wake = conn.execute(
        "SELECT delivered_at FROM wake_outbox WHERE id=?", (assignment["recovery_wake_id"],),
    ).fetchone()
    if wake is not None and wake["delivered_at"] is not None and (
        wake["delivered_at"] < assignment["startup_deadline"]
    ):
        return wake["delivered_at"] + DECISION_SECONDS
    return assignment["startup_deadline"]


def _current(conn: sqlite3.Connection, task: Task, assignment: sqlite3.Row) -> sqlite3.Row:
    reservation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (assignment["reservation_key"],),
    ).fetchone()
    from .ordinary_native_bindings import reservation_native_identity
    from .queue_execution_handoff import require_execution_admission
    from .queue_pool_placement import pool_worker_matches, require_pool_workspace

    if (
        reservation is None or reservation["task_id"] != task.id
        or assignment["task_generation"] != task.generation
        or task.status != Status.SUSPENDED or not task.owner or not task.owner_session_id
        or task.hold_reason is not None or task.awaiting_steer
        or reservation["state"] not in {SpawnState.SPAWNED, SpawnState.COLD}
        or not reservation["startup_complete"] or reservation["startup_fenced"]
        or reservation["release_requested"] or reservation["conversation_retired"]
        or reservation["cooling_token"] is not None or reservation["conclusion_state"] == "pending"
        or not pool_worker_matches(task.owner, assignment["machine"], reservation["worktree"])
        or reservation_native_identity(conn, reservation) != task.owner_session_id
    ):
        raise TaskError("suspended pool recovery requires its current unheld owner and retained native")
    require_execution_admission(conn, task.id)
    require_pool_workspace(
        conn, task, reservation["worktree"], reservation["execution_machine"],
        driver=reservation["driver"],
    )
    return reservation


def _enqueue(owner: Any, conn: sqlite3.Connection, task: Task, ts: float, revision: int,
             message: str | None = None) -> None:
    prompt = (
        f"Re-read your suspended task {task.id} in this same native conversation and workspace. "
        "Preserve its original coordinator routing flags. Take any pending durable steering. "
        f"Affirm recovery only while it is still SUSPENDED at generation {task.generation}, "
        f"using `agent-dispatch start {task.id} --pool-generation {task.generation} "
        f"--pool-recovery-revision {revision} --owner-session-id {task.owner_session_id}`. "
        "Prompt delivery is not acceptance. Do not claim a replacement task, change native identity, "
        "release another owner, or work after the ownership or recovery fence changes."
    )
    wake = owner._enqueue_wake(
        conn, task, message=prompt + (f"\n\n{message}" if message else ""), ts=ts,
        pool_recovery_revision=revision,
    )
    conn.execute(
        "UPDATE task_pool_assignments SET recovery_wake_id=? WHERE task_id=?", (wake.id, task.id),
    )


def request_recovery(
    owner: Any, conn: sqlite3.Connection, task: Task, *, ts: float,
    wake_requested: bool, message: str | None = None,
) -> Task | None:
    """Return None only for a non-pool task; never turn delivery into STARTED."""
    assignment = recovery_assignment(conn, task.id)
    if assignment is None:
        return None
    _current(conn, task, assignment)
    if not math.isfinite(ts):
        raise TaskError("pool recovery requires finite time")
    continuing = task.resume_requested and assignment["recovery_requested_at"] is not None
    if continuing and (not wake_requested or assignment["recovery_wake_id"] is not None):
        return task
    from .queue_execution_custody import require_execution_send_reconciliation
    require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
    if conn.execute(
        "SELECT 1 FROM wake_outbox WHERE task_id=? AND status='delivering' LIMIT 1", (task.id,),
    ).fetchone():
        raise TaskError("original owner wake is still delivering; retain its custody")
    if continuing:
        if ts >= _decision_deadline(conn, assignment):
            raise TaskError("pool recovery deadline expired before the requested wake")
        _enqueue(owner, conn, task, ts, assignment["recovery_revision"], message)
        return owner._fetch(conn, task.id)
    begin_pool_episode(conn, task.id, ts, assignment["startup_timeout"])
    revision = assignment["recovery_revision"] + 1
    conn.execute(
        "UPDATE task_pool_assignments SET recovery_revision=?, recovery_requested_at=?,"
        " recovery_wake_id=NULL WHERE task_id=?", (revision, ts, task.id),
    )
    conn.execute(
        "UPDATE tasks SET resume_requested=1, wake_operation_id=NULL, wake_status=NULL,"
        " updated_at=? WHERE id=?", (ts, task.id),
    )
    task = owner._fetch(conn, task.id)
    if wake_requested:
        _enqueue(owner, conn, task, ts, revision, message)
    owner._audit(
        conn, task.id, ts=ts, from_status=Status.SUSPENDED, to_status=Status.SUSPENDED,
        worker=task.owner, note=f"pool recovery requested: revision {revision}",
    )
    return owner._fetch(conn, task.id)


def require_recovery_start(
    conn: sqlite3.Connection, task: Task, revision: int | None,
    native: object, generation: int | None, ts: float,
) -> None:
    """Bind worker affirmation to this recovery, not an earlier start request."""
    assignment = recovery_assignment(conn, task.id)
    if revision is None:
        if assignment is not None and task.status == Status.SUSPENDED:
            raise TaskError("suspended pool start requires its current pool recovery revision")
        return
    if (
        assignment is None or type(revision) is not int or revision < 1
        or assignment["recovery_revision"] != revision or generation != task.generation
        or native != task.owner_session_id
    ):
        raise TaskError("pool recovery affirmation incarnation changed")
    if task.status == Status.STARTED and assignment["recovery_requested_at"] is None:
        return
    _current(conn, task, assignment)
    deadline = _decision_deadline(conn, assignment)
    if (
        not task.resume_requested or assignment["recovery_requested_at"] is None
        or deadline is None or not math.isfinite(ts) or ts >= deadline
    ):
        raise TaskError("pool recovery is not requested or its decision deadline expired")


def recovery_wake(conn: sqlite3.Connection, task: Task, wake_id: str) -> bool:
    assignment = recovery_assignment(conn, task.id)
    return bool(
        assignment is not None and assignment["task_generation"] == task.generation
        and assignment["recovery_requested_at"] is not None
        and assignment["recovery_wake_id"] == wake_id
        and task.status == Status.SUSPENDED and task.resume_requested
        and task.hold_reason is None and not task.awaiting_steer
    )


def prepare_recovery_wake(owner: Any, wake: Any) -> None:
    """Reacquire a cold reservation through its existing publication owner."""
    if getattr(wake, "pool_recovery_revision", None) is None:
        return
    with owner._connect() as conn:
        task = owner._fetch(conn, wake.task_id)
        if task is None or not recovery_wake(conn, task, wake.id):
            raise TaskError("suspended recovery wake incarnation changed")
        assignment = recovery_assignment(conn, task.id)
        if assignment["recovery_revision"] != wake.pool_recovery_revision:
            raise TaskError("suspended recovery wake revision changed")
        reservation = _current(conn, task, assignment)
    if reservation["state"] == SpawnState.COLD:
        owner.record_spawn(
            reservation["key"], session_handle=reservation["session_handle"],
            worktree=reservation["worktree"], startup_complete=True,
            startup_revision=reservation["startup_revision"],
        )


def exhaust_recovery(owner: Any, conn: sqlite3.Connection, task: Task,
                     assignment: sqlite3.Row, ts: float, reason: str) -> None:
    """Fence late acceptance and sends; retirement still owns capacity release."""
    reservation = _current(conn, task, assignment)
    stage = min(assignment["cooldown_stage"] + 1, len(COOLDOWN_SECONDS))
    conn.execute(
        "UPDATE task_pool_assignments SET cooldown_stage=?, exhausted_at=?, exhaustion_reason=?"
        " WHERE task_id=?", (stage, ts, reason, task.id),
    )
    conn.execute(
        "UPDATE tasks SET status=?, owner=NULL, owner_session_id=NULL, generation=generation+1,"
        " updated_at=?, not_before=?, lease_expires_at=NULL, resume_requested=0,"
        " monitor_kind=NULL, monitor_not_before=NULL, activity=NULL, activity_updated_at=? WHERE id=?",
        (Status.QUEUED, ts, max(task.not_before, ts + COOLDOWN_SECONDS[stage - 1]), ts, task.id),
    )
    conn.execute(
        "UPDATE spawn_reservations SET state=?, release_requested=1, startup_fenced=1,"
        " release_disposition='failed', conclusion_state=COALESCE(conclusion_state,'pending'),"
        " detail=?, updated_at=? WHERE key=?",
        (SpawnState.RELEASING, f"pool recovery exhausted: {reason}", ts, reservation["key"]),
    )
    owner._record_attachment(
        conn, task.id, ts=ts, old_session_id=task.owner_session_id, new_session_id=None,
        worktree_id=reservation["worktree"], machine=reservation["execution_machine"],
        detach_reason=f"pool recovery exhausted: {reason}",
    )
    owner._audit(
        conn, task.id, ts=ts, from_status=Status.SUSPENDED, to_status=Status.QUEUED,
        worker=task.owner, note=f"pool recovery exhausted: {reason}",
    )


def reconcile_recoveries(owner: Any, *, now: float | None = None) -> int:
    """Share the coordinator's existing suspended-monitor cadence, not a new timer."""
    ts = owner._now(now)
    changed = 0
    with owner._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT a.* FROM task_pool_assignments a JOIN tasks t ON t.id=a.task_id"
            " WHERE a.assignment_released=0"
            " AND t.status=? AND t.resume_requested=1 AND t.hold_reason IS NULL"
            " AND t.awaiting_steer=0", (Status.SUSPENDED,),
        ).fetchall()
        for assignment in rows:
            task = owner._fetch(conn, assignment["task_id"])
            try:
                if assignment["recovery_requested_at"] is None:
                    request_recovery(owner, conn, task, ts=ts, wake_requested=True)
                    changed += 1
                    continue
                _current(conn, task, assignment)
            except TaskError as exc:
                if assignment["recovery_requested_at"] is None:
                    detail = f"legacy suspended recovery retained: {exc}"
                    if assignment["outcome_detail"] != detail:
                        reservation = conn.execute(
                            "SELECT startup_revision FROM spawn_reservations WHERE key=?",
                            (assignment["reservation_key"],),
                        ).fetchone()
                        if reservation is not None:
                            conn.execute(
                                "UPDATE task_pool_assignments SET outcome_kind='startup_unknown',"
                                " outcome_detail=?, outcome_revision=?, outcome_at=? WHERE task_id=?",
                                (detail, reservation["startup_revision"], ts, task.id),
                            )
                            owner._audit(
                                conn, task.id, ts=ts, from_status=task.status, to_status=task.status,
                                worker=task.owner, note=detail,
                            )
                continue
            wake = conn.execute(
                "SELECT * FROM wake_outbox WHERE id=?", (assignment["recovery_wake_id"],),
            ).fetchone()
            deadline = assignment["decision_deadline"] or assignment["startup_deadline"]
            if (
                wake is not None and (
                    wake["status"] == "delivered" or wake["execution_send_state"] == "sent"
                )
                and wake["delivered_at"] is not None
                and assignment["reachable_at"] is None and wake["delivered_at"] < deadline
            ):
                delivered = wake["delivered_at"]
                conn.execute(
                    "UPDATE task_pool_assignments SET reachable_at=?, next_nudge_at=?,"
                    " decision_deadline=? WHERE task_id=?",
                    (delivered, delivered + NUDGE_SECONDS, delivered + DECISION_SECONDS, task.id),
                )
                changed += 1
                assignment = recovery_assignment(conn, task.id)
                deadline = assignment["decision_deadline"]
            failures = assignment["startup_attempts"] - assignment["episode_start_attempt"]
            failed = wake is not None and wake["status"] == "failed"
            exhausted_attempts = (
                failed and assignment["reachable_at"] is None and failures >= STARTUP_ATTEMPTS
            )
            if ts >= deadline or exhausted_attempts:
                exhaust_recovery(owner, conn, task, assignment, ts, (
                    "recovery_attempts_exhausted" if exhausted_attempts
                    else "recovery_decision_timeout" if assignment["reachable_at"] is not None
                    else "recovery_startup_timeout"
                ))
                changed += 1
            elif wake is not None and (
                wake["status"] == "delivered"
                or failed and assignment["reachable_at"] is not None
            ):
                if ts >= assignment["next_nudge_at"]:
                    _enqueue(owner, conn, task, ts, assignment["recovery_revision"])
                    conn.execute(
                        "UPDATE task_pool_assignments SET next_nudge_at=? WHERE task_id=?",
                        (ts + NUDGE_SECONDS, task.id),
                    )
                    changed += 1
            elif failed and assignment["reachable_at"] is None:
                conn.execute(
                    "UPDATE task_pool_assignments SET startup_attempts=startup_attempts+1,"
                    " startup_deadline=? WHERE task_id=?", (ts + assignment["startup_timeout"], task.id),
                )
                _enqueue(owner, conn, task, ts, assignment["recovery_revision"])
                changed += 1
        conn.commit()
    if changed:
        owner._notify_wake()
        owner._notify_owned_transition()
    return changed
