"""Final-send custody on the existing durable wake rows and delivery tokens."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .queue import TaskQueue

from .queue_common import RunWaiterWakeOperation, Task, WakeOperation
from .queue_execution_custody import (
    ExecutionSendUnresolved, SendOutcome, execution_scope_qualified, require_custody_scope,
    require_execution_send_reconciliation,
)
from .queue_execution_handoff import _scope_key, require_execution_admission
from .queue_execution_lineage import execution_lineage_match_sql
from .queue_execution_scopes import ExecutionScopeBinding
from .queue_records import TaskError
from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER

Wake = WakeOperation | RunWaiterWakeOperation
TABLES = ("wake_outbox", "run_waiter_wakes")


def wake_table(wake: Wake) -> str:
    if isinstance(wake, WakeOperation):
        return TABLES[0]
    if isinstance(wake, RunWaiterWakeOperation):
        return TABLES[1]
    raise TaskError("wake send requires an existing outbox operation")


@dataclass(frozen=True)
class WakeSendAdmission:
    identity: str | None
    send_required: bool


def _known_native_retry(row: sqlite3.Row, wake: Wake, task: Task) -> bool:
    return _retryable_native_refusal(row, wake, task, observing_delivery=False)


def _retryable_native_refusal(
    row: sqlite3.Row, wake: Wake, task: Task, *, observing_delivery: bool
) -> bool:
    if row["execution_send_state"] != "not_sent":
        return False
    try:
        previous = json.loads(row["execution_send"])
        refusal = json.loads(row["execution_send_detail"])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(previous, dict) and isinstance(refusal, dict)
        and previous.get("outbox") == wake_table(wake)
        and previous.get("wake_id") == wake.id
        and type(previous.get("task_generation")) is int
        and previous["task_generation"] == task.generation
        and isinstance(wake.owner_session_id, str) and bool(wake.owner_session_id.strip())
        and previous.get("owner_session_id") == wake.owner_session_id
        and isinstance(previous.get("delivery_token"), str)
        and bool(previous["delivery_token"])
        # A retry needs a new token. Readiness also sees the original token
        # between recording a retryable refusal and returning it to pending.
        and (previous["delivery_token"] != wake.delivery_token
             or (observing_delivery and row["status"] == "delivering"))
        and refusal.get("schema") == "dispatch-native-prompt-refusal"
        and type(refusal.get("version")) is int and refusal["version"] == 1
        and (
            refusal.get("outcome") == "refused_busy" and refusal.get("reason") == "busy"
            or refusal.get("outcome") == "refused_unavailable"
            and refusal.get("reason") == "native_send_not_attempted"
            and type(refusal.get("native_send_refusal_version")) is int
            and refusal["native_send_refusal_version"] == 1
            and refusal.get("admission_status") == "not_attempted"
        )
        and refusal.get("retryable") is True
        and refusal.get("invocation_key") == wake.id
        and refusal.get("native_session_id") == wake.owner_session_id
        and isinstance(refusal.get("bridge_session_id"), str)
        and bool(refusal["bridge_session_id"].strip())
    )


def wake_custody_clear_sql(outbox: str) -> str:
    if outbox not in TABLES:
        raise TaskError("unknown execution wake outbox")
    wake_clear = " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {table} w"
        " JOIN tasks retained ON retained.id = w.task_id"
        f" JOIN tasks current ON current.id = {outbox}.task_id"
        " WHERE w.execution_send_state IN ('pending', 'unknown') AND "
        + execution_lineage_match_sql("retained", "current") + ")"
        for table in TABLES
    )
    return (
        wake_clear
        + f" AND NOT EXISTS (SELECT 1 FROM spawn_reservations cooling"
        f" WHERE cooling.task_id = {outbox}.task_id AND cooling.cooling_token IS NOT NULL)"
        + " AND NOT EXISTS (SELECT 1 FROM spawn_reservations releasing"
        f" WHERE releasing.task_id = {outbox}.task_id"
        " AND releasing.attempt = (SELECT MAX(attempt) FROM spawn_reservations latest"
        " WHERE latest.task_id = releasing.task_id)"
        " AND (releasing.release_requested = 1 OR releasing.conversation_retired = 1"
        " OR releasing.state = 'releasing'))"
        + " AND NOT EXISTS (SELECT 1 FROM execution_send_custody c"
        " JOIN spawn_reservations r ON r.key = c.reservation_key"
        " JOIN tasks retained ON retained.id = c.task_id"
        f" JOIN tasks current ON current.id = {outbox}.task_id"
        " WHERE c.state IN ('pending', 'unknown') AND ("
        + execution_lineage_match_sql("retained", "current")
        + " OR r.exclusive_key = current.exclusive_key))"
    )


def _require_no_owner_wake(
    queue: TaskQueue, conn: sqlite3.Connection, task: Task, *, operation: str, protect_pending: bool,
) -> None:
    """The existing outbox excludes retirement racing or following a native wake."""
    for table in TABLES:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE task_id = ? AND ("
            "status IN ('pending', 'delivering', 'delivered', 'failed')"
            " OR execution_send_state IN ('pending', 'unknown'))", (task.id,),
        ).fetchall()
        for row in rows:
            if table == "run_waiter_wakes" and row["sender"] == CLAIM_RELEASE_SENDER:
                continue
            if row["execution_send_state"] in {"pending", "unknown"} or row["status"] == "delivering":
                raise TaskError(f"wake delivery excludes physical {operation}")
            if (
                row["status"] == "pending" and not protect_pending
                and row["execution_send_state"] != "sent" and row["attempts"] == 0
            ):
                continue
            current = (
                queue._wake_is_current(task, WakeOperation._from_row(row), conn=conn)
                if table == "wake_outbox" else queue._run_waiter_wake_is_current(
                    conn, task, RunWaiterWakeOperation._from_row(row),
                )
            )
            if current:
                raise TaskError(f"current owner wake excludes physical {operation}")


def require_no_wake_for_cooling(queue: TaskQueue, conn: sqlite3.Connection, task: Task) -> None:
    _require_no_owner_wake(queue, conn, task, operation="cooling", protect_pending=True)


def require_no_wake_for_release(queue: TaskQueue, conn: sqlite3.Connection, reservation: sqlite3.Row) -> None:
    task = queue._fetch(conn, reservation["task_id"])
    if task is not None:
        _require_no_owner_wake(queue, conn, task, operation="release", protect_pending=False)


def unresolved_wake_send(
    conn: sqlite3.Connection, task_id: str, *,
    exclusive_key: str | None = None, creation_prefix: str | None = None,
) -> sqlite3.Row | None:
    for table in TABLES:
        row = conn.execute(
            f"SELECT w.id, w.execution_send_state FROM {table} w"
            " JOIN tasks t ON t.id = w.task_id"
            " JOIN tasks current ON current.id = ?"
            " LEFT JOIN spawn_reservations r ON r.task_id = t.id"
            " WHERE w.execution_send_state IN ('pending', 'unknown')"
            " AND (" + execution_lineage_match_sql("t", "current") + " OR t.exclusive_key = ?"
            " OR (? IS NOT NULL AND r.creation_key LIKE ?)) LIMIT 1",
            (
                task_id, exclusive_key, creation_prefix,
                creation_prefix + "%" if creation_prefix is not None else None,
            ),
        ).fetchone()
        if row is not None:
            return cast(sqlite3.Row, row)
    return None


def reservation_wake_accountable(conn: sqlite3.Connection, reservation: sqlite3.Row) -> bool:
    """A wake targets a native/workspace, independently of reservation lineage."""
    from .ordinary_native_bindings import reservation_native_identity

    native = reservation_native_identity(conn, reservation)
    for table in TABLES:
        wakes = conn.execute(
            f"SELECT w.* FROM {table} w WHERE execution_send_state IN ('pending', 'unknown')"
            " AND (w.task_id = ? OR w.owner_session_id = ?"
            " OR substr(w.owner, instr(w.owner, '/') + 1) = ?)",
            (reservation["task_id"], native, reservation["worktree"]),
        ).fetchall()
        for wake in wakes:
            if _wake_targets_reservation(wake, reservation, native):
                return True
    return False


def _wake_targets_reservation(wake: Any, reservation: Any, native: str | None) -> bool:
    from .bridge_remote import normalize_host
    if native and wake["owner_session_id"]:
        if native != wake["owner_session_id"]:
            return False
    else:
        worktree = wake["owner"].partition("/")[2]
        if reservation["worktree"] and worktree:
            if reservation["worktree"] != worktree:
                return False
        elif wake["task_id"] != reservation["task_id"]:
            return False
    host = reservation["execution_machine"]
    wake_host = wake["owner"].partition("/")[0]
    return not (host and wake_host and normalize_host(host) != normalize_host(wake_host))


class ReservationWakeSnapshot:
    """Index unresolved wakes once; preserve the exact per-native matching rule."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.by_task: dict[str, list[Any]] = {}
        self.by_native: dict[str, list[Any]] = {}
        self.by_worktree: dict[str, list[Any]] = {}
        self.unidentified_task: dict[str, list[Any]] = {}
        self.unidentified_worktree: dict[str, list[Any]] = {}
        self.results: dict[tuple[Any, ...], bool] = {}
        for table in TABLES:
            for wake in conn.execute(
                f"SELECT * FROM {table} WHERE execution_send_state IN ('pending', 'unknown')",
            ):
                self.by_task.setdefault(wake["task_id"], []).append(wake)
                worktree = wake["owner"].partition("/")[2]
                self.by_worktree.setdefault(worktree, []).append(wake)
                if wake["owner_session_id"]:
                    self.by_native.setdefault(wake["owner_session_id"], []).append(wake)
                else:
                    self.unidentified_task.setdefault(wake["task_id"], []).append(wake)
                    self.unidentified_worktree.setdefault(worktree, []).append(wake)

    def accountable(self, reservation: Any) -> bool:
        from .ordinary_native_bindings import reservation_native_identity
        native = reservation_native_identity(self.conn, reservation)
        key = (reservation["task_id"], native, reservation["worktree"], reservation["execution_machine"])
        if key not in self.results:
            candidates = (
                self.unidentified_task.get(reservation["task_id"], [])
                + self.by_native.get(native, [])
                + self.unidentified_worktree.get(reservation["worktree"], [])
            ) if native else (
                self.by_task.get(reservation["task_id"], [])
                + self.by_worktree.get(reservation["worktree"], [])
            )
            self.results[key] = any(
                _wake_targets_reservation(wake, reservation, native) for wake in candidates
            )
        return self.results[key]


class QueueExecutionWakesMixin:
    if TYPE_CHECKING:
        def _connect(self) -> sqlite3.Connection: ...
        def _now(self, now: float | None) -> float: ...
        def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None: ...

    @staticmethod
    def _migrate_execution_wakes(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        for table in TABLES:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name in (
                "execution_scope_key", "execution_send", "execution_send_state",
                "execution_send_detail",
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_unresolved_send"
                f" ON {table}(execution_send_state,task_id)"
                " WHERE execution_send_state IN ('pending', 'unknown')"
            )
        from .provisional_driver_work import migrate
        migrate(conn)
        conn.commit()

    def defer_execution_wake(
        self, wake: Wake, *, detail: str, retry_after: float | None = None,
    ) -> Wake:
        """A pre-transport custody refusal does not spend a delivery attempt.

        ``retry_after`` reschedules the wake (``not_before``) so a drain moves on
        to other due wakes instead of reclaiming this one immediately.
        """
        table = wake_table(wake)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (wake.id,)).fetchone()
            if row is None or (
                row["status"] != "delivering" or row["delivery_token"] != wake.delivery_token
            ):
                raise TaskError("wake deferral requires the current delivery token")
            if row["execution_send"] is None:
                ts = self._now(None)
                conn.execute(
                    f"UPDATE {table} SET status = 'pending', attempts = MAX(0, attempts - 1),"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " not_before = MAX(not_before, ?), last_error = ?, updated_at = ? WHERE id = ?",
                    (ts + (retry_after or 0.0), detail, ts, wake.id),
                )
                if table == "wake_outbox":
                    conn.execute(
                        "UPDATE tasks SET wake_status = 'pending' WHERE id = ? AND wake_operation_id = ?",
                        (wake.task_id, wake.id),
                    )
            result = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (wake.id,)).fetchone()
            conn.commit()
        return WakeOperation._from_row(result) if table == "wake_outbox" else RunWaiterWakeOperation._from_row(result)

    def admit_execution_wake(
        self,
        wake: Wake,
        *,
        provenance: tuple[ExecutionScopeBinding, str] | None,
        now: float | None = None,
        before_commit: Callable[[sqlite3.Connection, Task, str], None] | None = None,
        no_resume: bool = False,
    ) -> WakeSendAdmission:
        """Only fresh admission permits transport; settled acceptance permits cleanup."""
        table = wake_table(wake)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ts = self._now(now)
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (wake.id,)).fetchone()
            if (
                row is None or row["status"] != "delivering"
                or not wake.delivery_token or row["delivery_token"] != wake.delivery_token
                or row["delivery_expires_at"] is None or row["delivery_expires_at"] <= ts
            ):
                raise TaskError("wake send requires the current unexpired delivery token")
            current_wake = (
                WakeOperation._from_row(row)
                if table == "wake_outbox" else RunWaiterWakeOperation._from_row(row)
            )
            if replace(
                wake,
                execution_send_state=current_wake.execution_send_state,
                execution_send_detail=current_wake.execution_send_detail,
            ) != current_wake:
                raise TaskError("wake send does not match its retained outbox operation")
            if row["execution_send_state"] == "sent":
                return WakeSendAdmission(row["execution_send"], False)
            task = self._fetch(conn, wake.task_id)
            current = (
                cast("TaskQueue", self)._wake_is_current(task, WakeOperation._from_row(row), conn=conn)
                if table == "wake_outbox"
                else cast("TaskQueue", self)._run_waiter_wake_is_current(conn, task, RunWaiterWakeOperation._from_row(row))
            )
            if not current or task is None:
                raise TaskError("wake send task fence advanced")
            if conn.execute(
                "SELECT 1 FROM spawn_reservations WHERE task_id = ?"
                " AND cooling_token IS NOT NULL LIMIT 1", (task.id,),
            ).fetchone():
                raise ExecutionSendUnresolved("spawn cooling remains unresolved")
            reservation = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt DESC LIMIT 1",
                (task.id,),
            ).fetchone()
            if reservation and (
                reservation["release_requested"] or reservation["conversation_retired"]
                or reservation["state"] == "releasing"
            ):
                raise TaskError("wake reservation release or retirement advanced")
            require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
            previous_send = row["execution_send"]
            if previous_send is not None and not _known_native_retry(row, current_wake, task):
                raise TaskError("settled wake send is observation only; retain its transport history")
            if previous_send is not None and json.loads(previous_send).get("no_resume", False) is not no_resume:
                raise TaskError("wake retry cannot change its original native send mode")
            if isinstance(wake, RunWaiterWakeOperation) and wake.sender == CLAIM_RELEASE_SENDER:
                raise TaskError("claim-release bookkeeping must not request execution custody")
            require_execution_admission(
                conn, task.id, owner_wake=current_wake if table == "wake_outbox" else None,
            )
            from .queue_relevance_delivery import closeout_wake
            local_closeout = isinstance(current_wake, WakeOperation) and closeout_wake(conn, current_wake)
            local_recovery = isinstance(wake, WakeOperation) and wake.pool_recovery_revision is not None
            assignment = None
            if isinstance(wake, WakeOperation) and local_recovery:
                from .queue_pool_recovery import recovery_assignment, recovery_wake
                assignment = recovery_assignment(conn, task.id)
                if (
                    assignment is None or not recovery_wake(conn, task, wake.id)
                    or assignment["recovery_revision"] != wake.pool_recovery_revision
                ):
                    raise TaskError("pool recovery send incarnation changed")
                from .retained_admission import require_retained
                if reservation is None or reservation["key"] != assignment["reservation_key"]:
                    raise TaskError("pool recovery reservation changed before send")
                require_retained(cast("TaskQueue", self), conn, task, reservation, resume=True)
            if provenance is None:
                if task.repo is not None and task.source is not None and execution_scope_qualified(conn, task.repo, task.source):
                    raise TaskError("qualified wake send requires coordinator provenance")
                if not local_recovery and not local_closeout:
                    if previous_send is not None:
                        raise TaskError("retained wake retry cannot drop its original send accounting")
                    if before_commit is not None:
                        raise TaskError("independent retention requires a qualified send")
                    return WakeSendAdmission(None, True)
                if not local_closeout and assignment is None:
                    raise TaskError("local recovery wake has no assignment")
                fields = {
                    "protocol": "task_relevance_closeout/v1" if local_closeout else "pool_recovery_wake/v1",
                    **({"no_resume": True} if no_resume else {}),
                    "reservation_key": (
                        reservation["key"] if reservation else None
                    ) if local_closeout else cast(sqlite3.Row, assignment)["reservation_key"],
                    "recovery_revision": wake.pool_recovery_revision if isinstance(wake, WakeOperation) else None,
                    "outbox": table, "wake_id": wake.id, "delivery_token": wake.delivery_token,
                    "task_generation": task.generation, "owner_session_id": wake.owner_session_id,
                }
                if local_closeout:
                    fields["closeout_steer_id"] = conn.execute(
                        "SELECT steer_id FROM task_relevance_closeouts WHERE task_id=?", (task.id,),
                    ).fetchone()["steer_id"]
                identity = json.dumps(fields, sort_keys=True, separators=(",", ":"))
                self._retain_busy_retry(conn, task, current_wake, row)
                conn.execute(
                    f"UPDATE {table} SET execution_send=?, execution_send_state='pending',"
                    " execution_send_detail='' WHERE id=?", (identity, wake.id),
                )
                if before_commit is not None:
                    before_commit(conn, task, identity)
                conn.commit()
                return WakeSendAdmission(identity, True)
            binding, runtime_id = provenance
            key = _scope_key(binding.scope)
            cast("TaskQueue", self)._require_execution_scope(conn, binding.scope)
            cast("TaskQueue", self)._require_execution_producer(
                conn, binding.scope, binding.source, binding.producer_generation
            )
            if (task.repo, task.source) != (binding.scope.repository, binding.source):
                raise TaskError("wake send belongs to another producer scope")
            require_custody_scope(conn, key, binding.scope.repository, binding.source)
            runtime = conn.execute(
                "SELECT 1 FROM execution_runtime_enrollments"
                " WHERE scope_key = ? AND runtime_id = ? AND repo = ? AND source = ?"
                " AND producer_generation = ? AND role = 'coordinator'",
                (key, runtime_id, task.repo, task.source, binding.producer_generation),
            ).fetchone()
            if runtime is None:
                raise TaskError("fresh wake send requires a matching enrolled runtime")
            identity = json.dumps(
                {
                    "scope": asdict(binding.scope), "runtime_id": runtime_id,
                    **({"no_resume": True} if no_resume else {}),
                    "producer_generation": binding.producer_generation,
                    "outbox": table, "wake_id": wake.id, "delivery_token": wake.delivery_token,
                    "task_generation": task.generation, "owner_session_id": wake.owner_session_id,
                },
                sort_keys=True, separators=(",", ":"),
            )
            self._retain_busy_retry(conn, task, current_wake, row)
            conn.execute(
                f"UPDATE {table} SET execution_scope_key = ?, execution_send = ?,"
                " execution_send_state = 'pending', execution_send_detail = '' WHERE id = ?",
                (key, identity, wake.id),
            )
            if before_commit is not None:
                before_commit(conn, task, identity)
            conn.commit()
        return WakeSendAdmission(identity, True)

    def _retain_busy_retry(self, conn: sqlite3.Connection, task: Task, wake: Wake, row: sqlite3.Row) -> None:
        if row["execution_send"] is None:
            return
        cast("TaskQueue", self)._audit(
            conn, task.id, ts=self._now(None), from_status=task.status,
            to_status=task.status, worker=wake.owner,
            note="wake known-not-sent retry: " + json.dumps({
                "version": 1, "outbox": wake_table(wake), "wake_id": wake.id,
                "identity": json.loads(row["execution_send"]),
                "refusal": json.loads(row["execution_send_detail"]),
                "next_delivery_token": wake.delivery_token,
            }, sort_keys=True, separators=(",", ":")),
        )

    def settle_execution_wake(
        self,
        wake: Wake,
        admission: WakeSendAdmission,
        *,
        outcome: SendOutcome,
        detail: str,
    ) -> None:
        """A retained send token may settle late, never grant a replacement send."""
        if admission.identity is None:
            raise TaskError("wake send has no retained custody")
        if outcome not in {"sent", "not_sent", "unknown"} or not detail.strip():
            raise TaskError("wake settlement requires an explicit transport outcome and detail")
        identity = json.loads(admission.identity)
        if identity["delivery_token"] != wake.delivery_token:
            raise TaskError("wake settlement requires its original delivery token")
        table = wake_table(wake)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (wake.id,)).fetchone()
            if row is None or row["execution_send"] != admission.identity:
                raise TaskError("wake send has no matching retained custody")
            if row["execution_send_state"] in {"sent", "not_sent"}:
                if (row["execution_send_state"], row["execution_send_detail"]) != (outcome, detail):
                    raise TaskError("wake send outcome conflicts with its settled result")
                return
            conn.execute(
                f"UPDATE {table} SET execution_send_state = ?, execution_send_detail = ? WHERE id = ?",
                (outcome, detail, wake.id),
            )
            if isinstance(wake, WakeOperation) and wake.pool_recovery_revision is not None and outcome == "sent":
                conn.execute(
                    "UPDATE wake_outbox SET delivered_at=COALESCE(delivered_at, ?) WHERE id=?",
                    (self._now(None), wake.id),
                )
            conn.commit()
