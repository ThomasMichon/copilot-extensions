"""Durable claim-episode budgets owned by the existing pool assignment."""

from __future__ import annotations

from dataclasses import dataclass
import math
import json
import sqlite3
from typing import Protocol

from .queue_common import Task
from .queue_records import SpawnState, Status, TaskError

STARTUP_SECONDS = 600
STARTUP_ATTEMPTS = 3
NUDGE_SECONDS = 300
DECISION_SECONDS = 1800
COOLDOWN_SECONDS = (3600, 10800, 21600, 43200)


def migrate_pool_startup(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_pool_assignments)")}
    additions = {
        "startup_attempts": "INTEGER NOT NULL DEFAULT 0",
        "episode_start_attempt": "INTEGER NOT NULL DEFAULT 0",
        "cooldown_stage": "INTEGER NOT NULL DEFAULT 0",
        "startup_deadline": "REAL",
        "startup_timeout": "REAL NOT NULL DEFAULT 600",
        "reachable_at": "REAL",
        "next_nudge_at": "REAL",
        "decision_deadline": "REAL",
        "exhausted_at": "REAL",
        "exhaustion_reason": "TEXT",
        "outcome_kind": "TEXT",
        "outcome_detail": "TEXT",
        "outcome_revision": "INTEGER",
        "outcome_at": "REAL",
        "retry_invocation": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE task_pool_assignments ADD COLUMN {name} {declaration}")


def validate_startup_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise TaskError("pool startup requires a positive finite startup timeout")


def begin_pool_episode(
    conn: sqlite3.Connection, task_id: str, ts: float, timeout: float = STARTUP_SECONDS,
) -> None:
    validate_startup_timeout(timeout)
    if not math.isfinite(ts + timeout):
        raise TaskError("pool startup deadline must be finite")
    conn.execute(
        "UPDATE task_pool_assignments SET episode_start_attempt=startup_attempts,"
        " startup_attempts=startup_attempts+1, startup_timeout=?, startup_deadline=?, reachable_at=NULL,"
        " next_nudge_at=NULL, decision_deadline=NULL, exhausted_at=NULL,"
        " exhaustion_reason=NULL, outcome_kind=NULL, outcome_detail=NULL,"
        " outcome_revision=NULL, outcome_at=NULL, retry_invocation=NULL WHERE task_id=?",
        (timeout, ts + timeout, task_id),
    )


def advance_pool_startup(conn: sqlite3.Connection, key: str, ts: float) -> None:
    assignment = conn.execute(
        "SELECT a.*, t.status FROM task_pool_assignments a"
        " JOIN tasks t ON t.id=a.task_id WHERE a.reservation_key=?", (key,),
    ).fetchone()
    if assignment is None:
        return
    if assignment["status"] in {Status.STARTED, Status.SUSPENDED}:
        return
    task_row = conn.execute("SELECT * FROM tasks WHERE id=?", (assignment["task_id"],)).fetchone()
    task = Task._from_row(task_row)
    if (
        not math.isfinite(ts) or task.status != Status.CLAIMED or task.owner is not None
        or task.hold_reason is not None or task.generation != assignment["task_generation"]
    ):
        raise TaskError("pool startup attempt requires its current unheld claim")
    from .queue_execution_handoff import require_execution_admission
    require_execution_admission(conn, task.id)
    if assignment["startup_deadline"] is None:
        raise TaskError("pool startup budget requires reconciliation of the legacy claim")
    if (
        assignment["exhausted_at"] is not None or assignment["reachable_at"] is not None
        or ts >= assignment["startup_deadline"]
        or assignment["startup_attempts"] - assignment["episode_start_attempt"] >= STARTUP_ATTEMPTS
    ):
        raise TaskError("pool startup episode cannot admit another attempt")
    deadline = ts + assignment["startup_timeout"]
    if not math.isfinite(deadline):
        raise TaskError("pool startup deadline must be finite")
    conn.execute(
        "UPDATE task_pool_assignments SET startup_attempts=startup_attempts+1,"
        " startup_deadline=?, outcome_kind=NULL, outcome_detail=NULL,"
        " outcome_revision=NULL, outcome_at=NULL WHERE reservation_key=?", (deadline, key),
    )


def reset_pool_startup(conn: sqlite3.Connection, task_id: str) -> None:
    conn.execute(
        "UPDATE task_pool_assignments SET startup_attempts=0, episode_start_attempt=0,"
        " cooldown_stage=0, startup_deadline=NULL, reachable_at=NULL,"
        " next_nudge_at=NULL, decision_deadline=NULL, exhausted_at=NULL,"
        " exhaustion_reason=NULL, outcome_kind=NULL, outcome_detail=NULL,"
        " outcome_revision=NULL, outcome_at=NULL WHERE task_id=?", (task_id,),
    )


def require_pool_decision(conn: sqlite3.Connection, task: Task, ts: float) -> None:
    if task.status != Status.CLAIMED:
        return
    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id=?", (task.id,),
    ).fetchone()
    if assignment is None:
        return
    if not math.isfinite(ts) or assignment["startup_deadline"] is None:
        raise TaskError("pool startup budget requires reconciliation of the legacy claim")
    deadline = assignment["decision_deadline"]
    if deadline is None:
        deadline = assignment["startup_deadline"]
    if assignment["exhausted_at"] is not None or ts >= deadline:
        raise TaskError("pool start decision deadline expired")


@dataclass(frozen=True)
class PoolStartupObservation:
    task_id: str
    reservation_key: str
    generation: int
    startup_attempts: int
    episode_start_attempt: int
    cooldown_stage: int
    startup_deadline: float | None
    startup_timeout: float
    reachable_at: float | None
    next_nudge_at: float | None
    decision_deadline: float | None
    exhausted_at: float | None
    exhaustion_reason: str | None
    outcome_kind: str | None
    outcome_detail: str | None
    outcome_revision: int | None
    outcome_at: float | None
    retry_invocation: dict | None


class _QueueOwner(Protocol):
    def _connect(self) -> sqlite3.Connection: ...
    def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None: ...
    def _now(self, now: float | None) -> float: ...
    def _audit(
        self, conn: sqlite3.Connection, task_id: str, *, ts: float,
        from_status: str, to_status: str, worker: str | None, note: str,
    ) -> None: ...


def _observation(row: sqlite3.Row) -> PoolStartupObservation:
    return PoolStartupObservation(
        task_id=row["task_id"], reservation_key=row["reservation_key"],
        generation=row["task_generation"], startup_attempts=row["startup_attempts"],
        episode_start_attempt=row["episode_start_attempt"], cooldown_stage=row["cooldown_stage"],
        startup_deadline=row["startup_deadline"], reachable_at=row["reachable_at"],
        startup_timeout=row["startup_timeout"],
        next_nudge_at=row["next_nudge_at"], decision_deadline=row["decision_deadline"],
        exhausted_at=row["exhausted_at"], exhaustion_reason=row["exhaustion_reason"],
        outcome_kind=row["outcome_kind"], outcome_detail=row["outcome_detail"],
        outcome_revision=row["outcome_revision"], outcome_at=row["outcome_at"],
        retry_invocation=json.loads(row["retry_invocation"]) if row["retry_invocation"] else None,
    )


def _current(
    owner: _QueueOwner, conn: sqlite3.Connection, key: str,
    generation: int, startup_revision: int, ts: float,
    task_id: str | None = None,
) -> tuple[Task, sqlite3.Row, sqlite3.Row]:
    if (
        type(generation) is not int or generation < 0
        or type(startup_revision) is not int or startup_revision < 1
        or not math.isfinite(ts)
    ):
        raise TaskError("pool startup requires finite time and the current incarnation")
    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE reservation_key=?", (key,),
    ).fetchone()
    reservation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (key,),
    ).fetchone()
    task = owner._fetch(conn, assignment["task_id"]) if assignment is not None else None
    if (
        assignment is None or reservation is None or task is None
        or task_id is not None and task.id != task_id
        or assignment["task_generation"] != generation or task.generation != generation
        or reservation["task_id"] != task.id
        or reservation["startup_revision"] != startup_revision
        or task.status != Status.CLAIMED or task.owner is not None
        or task.hold_reason is not None or assignment["exhausted_at"] is not None
        or assignment["startup_deadline"] is None
        or reservation["state"] not in {SpawnState.RESERVING, SpawnState.SPAWNED}
        or reservation["release_requested"] or reservation["startup_fenced"]
        or reservation["cooling_token"] is not None
    ):
        raise TaskError("pool startup assignment changed, held or requires reconciliation")
    from .queue_execution_handoff import require_execution_admission
    require_execution_admission(conn, task.id)
    return task, assignment, reservation


class PoolStartupMixin:
    def record_pool_startup_outcome(
        self: _QueueOwner, key: str, *, task_id: str, generation: int, startup_revision: int,
        outcome_kind: str, detail: str, native_id: str | None = None, now: float | None = None,
    ) -> PoolStartupObservation:
        """Record a caller outcome, distinct from a worker decline or native absence."""
        if outcome_kind not in {"startup_failed", "startup_rejected", "startup_unknown"} or (
            not isinstance(detail, str) or not detail.strip()
        ):
            raise TaskError("pool startup outcome requires a classified failure and explanatory detail")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ts = self._now(now)
            task, assignment, reservation = _current(
                self, conn, key, generation, startup_revision, ts, task_id,
            )
            if assignment["retry_invocation"]:
                raise TaskError("pool retry outcome requires its negotiated completion operation")
            from .ordinary_native_bindings import reservation_native_identity
            if reservation_native_identity(conn, reservation) != native_id:
                raise TaskError("pool startup outcome native identity changed")
            if assignment["reachable_at"] is not None:
                raise TaskError("reachable worker decisions are not startup failure outcomes")
            if assignment["outcome_revision"] == startup_revision:
                if assignment["outcome_kind"] == outcome_kind and assignment["outcome_detail"] == detail:
                    return _observation(assignment)
                if assignment["outcome_kind"] != "startup_unknown":
                    raise TaskError("completed startup outcome cannot be replaced")
            elif reservation["startup_complete"]:
                raise TaskError("completed startup requires its original caller outcome")
            conn.execute(
                "UPDATE task_pool_assignments SET outcome_kind=?, outcome_detail=?,"
                " outcome_revision=?, outcome_at=? WHERE reservation_key=?",
                (outcome_kind, detail, startup_revision, ts, key),
            )
            if outcome_kind != "startup_unknown":
                conn.execute(
                    "UPDATE spawn_reservations SET startup_complete=1, updated_at=? WHERE key=?", (ts, key),
                )
            self._audit(
                conn, task.id, ts=ts, from_status=Status.CLAIMED, to_status=Status.CLAIMED,
                worker=reservation["reserved_by"], note=f"pool startup outcome: {outcome_kind}: {detail}",
            )
            row = conn.execute(
                "SELECT * FROM task_pool_assignments WHERE reservation_key=?", (key,),
            ).fetchone()
            conn.commit()
            return _observation(row)

    def pool_startup_snapshot(self: _QueueOwner, task_id: str) -> dict[str, object] | None:
        """Read budget, reservation and task from one coherent queue snapshot."""
        from dataclasses import asdict
        from .queue_records import SpawnReservation
        from .ordinary_native_bindings import reservation_native_identity
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM task_pool_assignments WHERE task_id=?", (task_id,),
            ).fetchone()
            if row is None:
                return None
            reservation = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (row["reservation_key"],),
            ).fetchone()
            task = self._fetch(conn, task_id)
            if reservation is None or task is None or reservation["task_id"] != task_id:
                raise TaskError("pool startup snapshot requires its retained task and reservation")
            return {
                "budget": asdict(_observation(row)),
                "reservation": asdict(SpawnReservation._from_row(reservation)),
                "task": asdict(task),
                "native_id": reservation_native_identity(conn, reservation),
            }

    def pool_startup(self: _QueueOwner, task_id: str) -> PoolStartupObservation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_pool_assignments WHERE task_id=?", (task_id,),
            ).fetchone()
            return _observation(row) if row is not None else None

    def observe_pool_reachable(
        self: _QueueOwner, key: str, *, generation: int, startup_revision: int,
        native_id: str, now: float | None = None,
        task_id: str | None = None,
    ) -> PoolStartupObservation:
        """Record the supervisor's exact reachable worker, never accept its task."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ts = self._now(now)
            task, assignment, reservation = _current(
                self, conn, key, generation, startup_revision, ts, task_id,
            )
            if not reservation["startup_complete"]:
                raise TaskError("pool reachability requires a completed current startup")
            if assignment["outcome_kind"] is not None:
                raise TaskError("classified startup outcomes cannot establish worker reachability")
            from .queue_pool_placement import require_pool_start
            worker = f"{assignment['machine']}/{reservation['worktree']}"
            require_pool_start(conn, task, worker, native_id, generation)
            if assignment["reachable_at"] is None:
                if ts >= assignment["startup_deadline"]:
                    raise TaskError("pool startup deadline expired before reachability")
                conn.execute(
                    "UPDATE task_pool_assignments SET reachable_at=?, next_nudge_at=?,"
                    " decision_deadline=? WHERE reservation_key=?",
                    (ts, ts + NUDGE_SECONDS, ts + DECISION_SECONDS, key),
                )
            row = conn.execute(
                "SELECT * FROM task_pool_assignments WHERE reservation_key=?", (key,),
            ).fetchone()
            conn.commit()
            return _observation(row)

    def take_pool_nudge(
        self: _QueueOwner, key: str, *, generation: int, startup_revision: int,
        native_id: str, now: float | None = None,
        task_id: str | None = None,
    ) -> PoolStartupObservation | None:
        """Claim one due supervision slot; this is not a delivery receipt."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ts = self._now(now)
            task, assignment, reservation = _current(
                self, conn, key, generation, startup_revision, ts, task_id,
            )
            from .queue_pool_placement import require_pool_start
            require_pool_start(
                conn, task, f"{assignment['machine']}/{reservation['worktree']}",
                native_id, generation,
            )
            if assignment["reachable_at"] is None:
                raise TaskError("pool nudge requires the current reachable worker")
            if ts >= assignment["decision_deadline"]:
                raise TaskError("pool nudge decision deadline expired")
            if ts < assignment["next_nudge_at"]:
                return None
            conn.execute(
                "UPDATE task_pool_assignments SET next_nudge_at=? WHERE reservation_key=?",
                (ts + NUDGE_SECONDS, key),
            )
            row = conn.execute(
                "SELECT * FROM task_pool_assignments WHERE reservation_key=?", (key,),
            ).fetchone()
            conn.commit()
            return _observation(row)

    def exhaust_pool_startup(
        self: _QueueOwner, key: str, *, generation: int, startup_revision: int,
        now: float | None = None,
        task_id: str | None = None,
        reason: str = "deadline",
    ) -> Task:
        """End an expired claim without asserting absence or releasing capacity."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ts = self._now(now)
            task, assignment, reservation = _current(
                self, conn, key, generation, startup_revision, ts, task_id,
            )
            deadline = assignment["decision_deadline"]
            if deadline is None:
                deadline = assignment["startup_deadline"]
            if reason not in {"deadline", "attempts"}:
                raise TaskError("pool startup exhaustion requires a deadline or attempt budget")
            if reason == "attempts" and (
                assignment["reachable_at"] is not None
                or not reservation["startup_complete"]
                or assignment["outcome_revision"] != startup_revision
                or assignment["outcome_kind"] not in {"startup_failed", "startup_rejected"}
                or assignment["startup_attempts"] - assignment["episode_start_attempt"] < STARTUP_ATTEMPTS
            ):
                raise TaskError("pool startup attempts are not exhausted by completed failures")
            if reason == "deadline" and ts < deadline:
                raise TaskError("pool startup decision or startup deadline has not expired")
            reason = ("startup_attempts_exhausted" if reason == "attempts" else
                      "decision_timeout" if assignment["reachable_at"] is not None else "startup_timeout")
            stage = min(assignment["cooldown_stage"] + 1, len(COOLDOWN_SECONDS))
            not_before = max(task.not_before, ts + COOLDOWN_SECONDS[stage - 1])
            conn.execute(
                "UPDATE task_pool_assignments SET cooldown_stage=?, exhausted_at=?,"
                " exhaustion_reason=? WHERE reservation_key=?", (stage, ts, reason, key),
            )
            conn.execute(
                "UPDATE tasks SET status=?, generation=generation+1, updated_at=?,"
                " not_before=?, claimed_at=NULL, lease_expires_at=NULL,"
                " activity=NULL, activity_updated_at=? WHERE id=?",
                (Status.QUEUED, ts, not_before, ts, task.id),
            )
            # A pending original caller must still be able to publish its late
            # current-revision return to RELEASING for normal retirement.
            conn.execute(
                "UPDATE spawn_reservations SET state=?, release_requested=1,"
                " release_disposition='failed', conclusion_state=COALESCE(conclusion_state,'pending'),"
                " startup_fenced=CASE WHEN startup_complete THEN 1 ELSE startup_fenced END,"
                " detail=?, updated_at=? WHERE key=?",
                (SpawnState.RELEASING, f"pool claim exhausted: {reason}", ts, key),
            )
            self._audit(
                conn, task.id, ts=ts, from_status=Status.CLAIMED, to_status=Status.QUEUED,
                worker=reservation["reserved_by"], note=f"pool claim exhausted: {reason}",
            )
            result = self._fetch(conn, task.id)
            assert result is not None
            conn.commit()
            return result
