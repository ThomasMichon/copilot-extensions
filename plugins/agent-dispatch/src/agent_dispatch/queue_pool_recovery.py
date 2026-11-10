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
    """A renewed intentional wait fences an older recovery without erasing debt.

    Any outstanding resume intent of a pool-assigned task is withdrawn, even one
    that no longer waits for a lane (a detached wake may have re-taken it), so
    reconciliation never starts a recovery the owner did not ask for again.
    """
    assigned = conn.execute(
        "UPDATE task_pool_assignments SET recovery_revision=recovery_revision+1,"
        " recovery_requested_at=NULL, recovery_wake_id=NULL, recovery_hold_started_at=NULL"
        " WHERE task_id=? AND recovery_requested_at IS NOT NULL", (task_id,),
    ).rowcount
    assigned += conn.execute(
        "UPDATE task_pool_assignments SET lane_wait_since=NULL, lane_wait_message=NULL,"
        " lane_wait_wake=NULL WHERE task_id=? AND assignment_released=0", (task_id,),
    ).rowcount
    if assigned:
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
        held_at = assignment["recovery_hold_started_at"]
        delay = max(0.0, ts - held_at)
        startup_delay = delay if held_at < assignment["startup_deadline"] else 0.0
        wake = conn.execute(
            "SELECT delivered_at FROM wake_outbox WHERE id=?", (assignment["recovery_wake_id"],),
        ).fetchone()
        delivered = wake["delivered_at"] if wake is not None else None
        delivery_pause = (
            max(0.0, min(ts, delivered) - held_at)
            if delivered is not None and startup_delay else 0.0
        )
        if (
            assignment["reachable_at"] is None and delivered is not None
            and delivered < assignment["startup_deadline"] + delivery_pause
        ):
            decision_pause = (
                max(0.0, ts - max(held_at, delivered))
                if held_at < delivered + DECISION_SECONDS else 0.0
            )
            conn.execute(
                "UPDATE task_pool_assignments SET reachable_at=?, next_nudge_at=?,"
                " decision_deadline=? WHERE task_id=?",
                (delivered, delivered + NUDGE_SECONDS + decision_pause,
                 delivered + DECISION_SECONDS + decision_pause, task_id),
            )
            conn.execute(
                "UPDATE task_pool_assignments SET recovery_hold_started_at=NULL,"
                " startup_deadline=startup_deadline+? WHERE task_id=?", (startup_delay, task_id),
            )
            return
        decision_delay = (
            delay if assignment["decision_deadline"] is not None
            and held_at < assignment["decision_deadline"] else 0.0
        )
        conn.execute(
            "UPDATE task_pool_assignments SET recovery_hold_started_at=NULL,"
            " startup_deadline=startup_deadline+?, decision_deadline=decision_deadline+?,"
            " next_nudge_at=next_nudge_at+? WHERE task_id=?",
            (startup_delay, decision_delay, decision_delay, task_id),
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


def _current(
    conn: sqlite3.Connection, task: Task, assignment: sqlite3.Row, *, retain_request: bool = False,
) -> sqlite3.Row:
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
    from .queue_execution_custody import PoolLaneUnavailable
    try:
        require_pool_workspace(
            conn, task, reservation["worktree"], reservation["execution_machine"],
            driver=reservation["driver"],
        )
    except PoolLaneUnavailable:
        if not retain_request:
            raise
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


def _wait_for_lane(
    owner: Any, conn: sqlite3.Connection, task: Task, assignment: sqlite3.Row,
    ts: float, message: str | None, wake_requested: bool,
) -> bool:
    """Park a reactivation in request order until its pool has a free lane.

    The caller's wake choice is retained, so a start that frees up later wakes
    the owner only if some request for this reactivation asked for a wake.
    """
    from .queue_pool_placement import pool_lane_load
    own = conn.execute(
        "SELECT state FROM spawn_reservations WHERE key=?", (assignment["reservation_key"],),
    ).fetchone()
    identity = (assignment["registration_id"], assignment["machine"], assignment["env"])
    catalog = getattr(owner, "_pool_catalog_from_conn", None)
    if catalog is None:
        return False
    pool = _eligible_pool(catalog(conn, now=ts), identity)
    if pool is not None and (own is None or own["state"] != SpawnState.COLD):
        return False  # a body that never parked still holds its own lane
    since = assignment["lane_wait_since"]
    # A disabled pool or an unavailable catalog defers the start; it never
    # restarts intentionally suspended work outside its pool's authority.
    if pool is not None and pool_lane_load(
        conn, identity, ahead_of=(ts if since is None else since, task.id),
        cutoff=pool.max_concurrent,
    ) < pool.max_concurrent:
        return False
    if since is None:
        conn.execute(
            "UPDATE task_pool_assignments SET lane_wait_since=?, lane_wait_message=?,"
            " lane_wait_wake=MAX(COALESCE(lane_wait_wake, 0), ?) WHERE task_id=?",
            (ts, message, int(wake_requested), task.id),
        )
        conn.execute("UPDATE tasks SET resume_requested=1, updated_at=? WHERE id=?", (ts, task.id))
        owner._audit(
            conn, task.id, ts=ts, from_status=Status.SUSPENDED, to_status=Status.SUSPENDED,
            worker=task.owner, note=(
                "pool recovery waiting for a free lane" if pool is not None
                else "pool recovery waiting for its pool to be available"
            ),
        )
    else:
        conn.execute(
            "UPDATE task_pool_assignments SET lane_wait_message=COALESCE(?, lane_wait_message),"
            " lane_wait_wake=MAX(COALESCE(lane_wait_wake, 0), ?) WHERE task_id=?",
            (message, int(wake_requested), task.id),
        )
    return True


def lane_waiting(conn: sqlite3.Connection, task_id: str) -> bool:
    row = conn.execute(
        "SELECT lane_wait_since FROM task_pool_assignments WHERE task_id=?", (task_id,),
    ).fetchone()
    return row is not None and row["lane_wait_since"] is not None


def request_recovery(
    owner: Any, conn: sqlite3.Connection, task: Task, *, ts: float,
    wake_requested: bool, message: str | None = None,
) -> Task | None:
    """Return None only for a non-pool task; never turn delivery into STARTED."""
    assignment = recovery_assignment(conn, task.id)
    if assignment is None:
        return None
    _current(conn, task, assignment, retain_request=True)
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
    if _wait_for_lane(owner, conn, task, assignment, ts, message, wake_requested):
        return owner._fetch(conn, task.id)
    if message is None:
        message = assignment["lane_wait_message"]
    # begin_pool_episode() clears the saved choice; an earlier waiting request
    # that asked for a wake must still wake the owner.
    wake_requested = wake_requested or bool(assignment["lane_wait_wake"])
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


def _eligible_pool(snapshot: Any, identity: tuple[Any, ...]) -> Any:
    """The bound pool's definition only while the catalog is ready to vouch for it."""
    if not snapshot.ready:
        return None
    return next((d for d in snapshot.definitions if d.identity == identity), None)


def admit_cold_lane(owner: Any, row: sqlite3.Row) -> None:
    """Refuse restarting a parked pool body while its bound pool is full.

    Runs inside the reservation's write transaction for every route that
    restarts a cold body (legacy supervisor resume, detached-run completion or
    other non-recovery wakes). The lane is the reservation's immutable pool
    binding, so adopted accounting-only bindings are admitted too. A
    reactivation already admitted by recovery reconciliation skips the lane
    count, but every route defers while the bound pool is disabled or the
    catalog cannot vouch for it.
    The refusal is send-unresolved, so wake drains defer without spending an
    attempt and the legacy resume route retries later.
    """
    from .queue_execution_custody import PoolLaneUnavailable
    from .queue_pool_placement import pool_lane_load
    catalog = getattr(owner, "_pool_catalog_from_conn", None)
    if row["state"] != SpawnState.COLD or catalog is None:
        return
    with owner._connect() as probe:
        binding = probe.execute(
            "SELECT registration_id, machine, env FROM pool_reservation_bindings WHERE reservation_key=?",
            (row["key"],),
        ).fetchone()
        if binding is None:
            return
        identity = (binding["registration_id"], binding["machine"], binding["env"])
        pool = _eligible_pool(catalog(probe, now=owner._now(None)), identity)
        if pool is None:
            raise PoolLaneUnavailable("cold pool body's pool is disabled or its catalog unavailable")
        admitted = probe.execute(
            "SELECT 1 FROM task_pool_assignments WHERE reservation_key=? AND assignment_released=0"
            " AND recovery_requested_at IS NOT NULL", (row["key"],),
        ).fetchone()
        if admitted is not None:
            return
        load = pool_lane_load(
            probe, identity, cutoff=pool.max_concurrent,
            exclude_key=row["key"], exclude_waiter=row["task_id"],
        )
    if load >= pool.max_concurrent:
        raise PoolLaneUnavailable("cold pool body is waiting for a free lane")


def _require_wake_admissible(owner: Any, conn: sqlite3.Connection, wake: Any,
                             reservation_key: str) -> None:
    """Validate a wake as its delivery admission would, before it may take a lane.

    Runs under the reservation's write lock, so a superseded token, task or
    waiter incarnation, or a released reservation leaves the body cold.
    """
    from .queue_execution_wakes import RunWaiterWakeOperation, WakeOperation, wake_table
    table = wake_table(wake)
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (wake.id,)).fetchone()
    if (
        row is None or row["status"] != "delivering"
        or not wake.delivery_token or row["delivery_token"] != wake.delivery_token
        or row["delivery_expires_at"] is None or row["delivery_expires_at"] <= owner._now(None)
    ):
        raise TaskError("wake send requires the current unexpired delivery token")
    task = owner._fetch(conn, wake.task_id)
    current = (
        owner._wake_is_current(task, WakeOperation._from_row(row)) if table == "wake_outbox"
        else owner._run_waiter_wake_is_current(conn, task, RunWaiterWakeOperation._from_row(row))
    )
    if not current or task is None:
        raise TaskError("wake send task fence advanced")
    if getattr(wake, "pool_recovery_revision", None) is not None:
        assignment = recovery_assignment(conn, task.id)
        if (
            not recovery_wake(conn, task, wake.id)
            or assignment["recovery_revision"] != wake.pool_recovery_revision
            or assignment["reservation_key"] != reservation_key
        ):
            raise TaskError("suspended recovery wake incarnation changed")
    reservation = conn.execute(
        "SELECT release_requested, conversation_retired, state FROM spawn_reservations WHERE key=?",
        (reservation_key,),
    ).fetchone()
    if reservation is None or reservation["release_requested"] or reservation["conversation_retired"]:
        raise TaskError("wake reservation release or retirement advanced")


def _restart_cold(
    owner: Any, wake: Any, reservation: sqlite3.Row, *, clear_lane_wait: bool = False,
) -> None:
    """Validate the wake and take the lane in one write transaction.

    ``clear_lane_wait`` ends a queued reactivation's wait for the lane in that
    same transaction, so a newer resume recorded after it commits survives.
    """
    def validate(conn: sqlite3.Connection) -> None:
        _require_wake_admissible(owner, conn, wake, reservation["key"])

    def clear(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE task_pool_assignments SET lane_wait_since=NULL, lane_wait_message=NULL,"
            " lane_wait_wake=NULL WHERE reservation_key=? AND lane_wait_since IS NOT NULL",
            (reservation["key"],),
        )

    owner.record_spawn(
        reservation["key"], session_handle=reservation["session_handle"],
        worktree=reservation["worktree"], startup_complete=True,
        startup_revision=reservation["startup_revision"], cold_admission=validate,
        cold_restarted=clear if clear_lane_wait else None,
    )


def _admit_cold_wake(owner: Any, wake: Any) -> None:
    """Route any other wake of a parked pool body through lane admission.

    A detached-run completion or other wake for a cold body would otherwise run
    it without a lane. Reacquire the lane atomically (``record_spawn`` applies
    ``admit_cold_lane``), or defer the wake while the pool is full. A body with
    no recovery assignment (an adopted legacy binding) uses its latest attempt.
    """
    with owner._connect() as conn:
        assignment = recovery_assignment(conn, wake.task_id)
        reservation = conn.execute(
            "SELECT s.* FROM spawn_reservations s"
            " JOIN pool_reservation_bindings b ON b.reservation_key = s.key WHERE s.key=?",
            (assignment["reservation_key"],),
        ).fetchone() if assignment else conn.execute(
            "SELECT s.* FROM spawn_reservations s"
            " JOIN pool_reservation_bindings b ON b.reservation_key = s.key"
            " WHERE s.task_id=? AND s.attempt = ("
            "SELECT MAX(attempt) FROM spawn_reservations WHERE task_id=?)",
            (wake.task_id, wake.task_id),
        ).fetchone()
    if reservation is None or reservation["state"] != SpawnState.COLD:
        return
    # The body re-takes its lane; a queued reactivation no longer waits for one.
    _restart_cold(owner, wake, reservation, clear_lane_wait=assignment is not None)


def prepare_recovery_wake(owner: Any, wake: Any) -> None:
    """Reacquire a cold reservation through its existing publication owner."""
    if getattr(wake, "pool_recovery_revision", None) is None:
        _admit_cold_wake(owner, wake)
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
        _restart_cold(owner, wake, reservation)


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
    from .queue_execution_custody import PoolLaneUnavailable
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
                    request_recovery(
                        owner, conn, task, ts=ts,
                        # Only a resume that never recorded a choice keeps the legacy wake.
                        wake_requested=(
                            assignment["lane_wait_wake"] is None
                            or bool(assignment["lane_wait_wake"])
                        ),
                    )
                    changed += not lane_waiting(conn, task.id)
                    continue
                _current(conn, task, assignment)
            except PoolLaneUnavailable:
                continue
            except TaskError as exc:
                if assignment["recovery_requested_at"] is None:
                    # A waiter that cannot start must not keep holding its pool's lane;
                    # its recorded wake choice stays with the retained resume request.
                    changed += conn.execute(
                        "UPDATE task_pool_assignments SET lane_wait_since=NULL, lane_wait_message=NULL"
                        " WHERE task_id=? AND lane_wait_since IS NOT NULL", (task.id,),
                    ).rowcount
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
