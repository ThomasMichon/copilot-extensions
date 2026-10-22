"""Final-send custody on the existing durable wake rows and delivery tokens."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace

from .queue_common import RunWaiterWakeOperation, WakeOperation
from .queue_execution_custody import (
    SendOutcome, execution_scope_qualified, require_custody_scope,
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
        + " AND NOT EXISTS (SELECT 1 FROM execution_send_custody c"
        " JOIN spawn_reservations r ON r.key = c.reservation_key"
        " JOIN tasks retained ON retained.id = c.task_id"
        f" JOIN tasks current ON current.id = {outbox}.task_id"
        " WHERE c.state IN ('pending', 'unknown') AND ("
        + execution_lineage_match_sql("retained", "current")
        + " OR r.exclusive_key = current.exclusive_key))"
    )


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
            return row
    return None


class QueueExecutionWakesMixin:
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
        conn.commit()

    def defer_execution_wake(self, wake: Wake, *, detail: str) -> Wake:
        """A pre-transport custody refusal does not spend a delivery attempt."""
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
                    " last_error = ?, updated_at = ? WHERE id = ?",
                    (detail, ts, wake.id),
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
                self._wake_is_current(task, WakeOperation._from_row(row))
                if table == "wake_outbox"
                else self._run_waiter_wake_is_current(conn, task, RunWaiterWakeOperation._from_row(row))
            )
            if not current or task is None:
                raise TaskError("wake send task fence advanced")
            require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
            if row["execution_send"] is not None:
                raise TaskError("settled wake send is observation only; retain its transport history")
            if isinstance(wake, RunWaiterWakeOperation) and wake.sender == CLAIM_RELEASE_SENDER:
                raise TaskError("claim-release bookkeeping must not request execution custody")
            require_execution_admission(conn, task.id)
            if provenance is None:
                if execution_scope_qualified(conn, task.repo, task.source):
                    raise TaskError("qualified wake send requires coordinator provenance")
                return WakeSendAdmission(None, True)
            binding, runtime_id = provenance
            key = _scope_key(binding.scope)
            self._require_execution_scope(conn, binding.scope)
            self._require_execution_producer(
                conn, binding.scope, binding.source, binding.producer_generation
            )
            if (task.repo, task.source) != (binding.scope.repository, binding.source):
                raise TaskError("wake send belongs to another producer scope")
            require_custody_scope(conn, key, task.repo, task.source)
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
                    "producer_generation": binding.producer_generation,
                    "outbox": table, "wake_id": wake.id, "delivery_token": wake.delivery_token,
                    "task_generation": task.generation, "owner_session_id": wake.owner_session_id,
                },
                sort_keys=True, separators=(",", ":"),
            )
            conn.execute(
                f"UPDATE {table} SET execution_scope_key = ?, execution_send = ?,"
                " execution_send_state = 'pending', execution_send_detail = '' WHERE id = ?",
                (key, identity, wake.id),
            )
            conn.commit()
        return WakeSendAdmission(identity, True)

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
            conn.commit()
