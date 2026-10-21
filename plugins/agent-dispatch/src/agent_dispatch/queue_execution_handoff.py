"""Durable admission closure in the existing queue, not handoff acknowledgement.

Only a trusted coordinator may resolve the qualified scope to a managed
producer source. There is deliberately no HTTP/CLI entry point or destination
activation: runtime enrollment, final launch fencing and effects remain gates.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from .execution_effect_adapter import EffectRevocation, ExecutionScope
from .queue_records import TaskError


class ExecutionAdmissionClosed(TaskError):
    """An existing execution transition prevents another admission."""


@dataclass(frozen=True)
class ExecutionClosure:
    request: EffectRevocation
    source: str
    producer_generation: int
    created_at: float
    replayed: bool


def _scope_key(scope: ExecutionScope) -> str:
    if not isinstance(scope, ExecutionScope):
        raise TaskError("execution closure requires an owner-resolved scope")
    return json.dumps(asdict(scope), sort_keys=True, separators=(",", ":"))


def closed_execution(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    """Read under the caller's admission transaction."""
    return conn.execute(
        "SELECT c.* FROM execution_admission_closures c"
        " JOIN tasks t ON t.repo = c.repo AND t.source = c.source"
        " WHERE t.id = ?",
        (task_id,),
    ).fetchone()


def require_execution_admission(conn: sqlite3.Connection, task_id: str) -> None:
    row = closed_execution(conn, task_id)
    if row is not None:
        raise ExecutionAdmissionClosed(
            f"execution admission closed for task {task_id!r}"
            f" by operation {row['operation_id']!r}; handoff is not acknowledged"
        )


class QueueExecutionHandoffMixin:
    @staticmethod
    def _require_execution_scope(conn: sqlite3.Connection, scope: ExecutionScope) -> None:
        identity = conn.execute(
            "SELECT identity FROM workspace_queue_identity WHERE singleton = 1"
        ).fetchone()
        if identity is None or identity["identity"] != scope.coordinator:
            raise TaskError("execution scope belongs to another coordinator")

    def _require_execution_producer(
        self,
        conn: sqlite3.Connection,
        scope: ExecutionScope,
        source: str,
        expected_producer_generation: int,
    ) -> None:
        if type(expected_producer_generation) is not int or expected_producer_generation < 1:
            raise TaskError("expected producer generation must be positive")
        repo, _ = self._validate_producer_scope(scope.repository, source)
        if repo != scope.repository:
            raise TaskError("execution scope repository must be canonical")
        producer = self._producer_scope_row(conn, repo=repo, source=source)
        if producer is None or producer["current_generation"] != expected_producer_generation:
            raise TaskError("execution closure requires the current managed producer")

    @staticmethod
    def _migrate_execution_handoff(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS execution_admission_closures ("
            " scope_key TEXT NOT NULL UNIQUE,"
            " repo TEXT NOT NULL,"
            " source TEXT NOT NULL,"
            " operation_id TEXT NOT NULL,"
            " request TEXT NOT NULL,"
            " producer_generation INTEGER NOT NULL,"
            " created_at REAL NOT NULL,"
            " PRIMARY KEY(repo, source))"
        )

    def close_execution_admission(
        self,
        request: EffectRevocation,
        *,
        source: str,
        expected_producer_generation: int,
        now: float | None = None,
    ) -> ExecutionClosure:
        """Persist a first automatic-to-manual closure with atomic queue guards.

        This internal construction surface accepts an owner-resolved scope, not
        arbitrary caller labels. Producer and execution generations are distinct.
        It never retires a producer, transfers a task or acknowledges effects.
        """
        if not isinstance(request, EffectRevocation):
            raise TaskError("execution closure requires a qualified revocation")
        if (request.retiring_mode, request.destination_mode, request.generation) != (
            "automatic",
            "manual",
            1,
        ):
            raise TaskError("only initial admission closure is implemented; no handback")
        if type(expected_producer_generation) is not int or expected_producer_generation < 1:
            raise TaskError("expected producer generation must be positive")
        repo, source = self._validate_producer_scope(request.scope.repository, source)
        if repo != request.scope.repository:
            raise TaskError("execution scope repository must be canonical")
        key = _scope_key(request.scope)
        encoded = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_execution_scope(conn, request.scope)
            row = conn.execute(
                "SELECT * FROM execution_admission_closures"
                " WHERE (repo = ? AND source = ?) OR scope_key = ?",
                (repo, source, key),
            ).fetchone()
            if row is not None:
                if (
                    row["scope_key"] != key
                    or row["source"] != source
                    or row["request"] != encoded
                    or row["producer_generation"] != expected_producer_generation
                ):
                    raise TaskError("execution closure conflicts with the retained operation")
                result = ExecutionClosure(
                    request, source, row["producer_generation"], row["created_at"], True
                )
            else:
                self._require_execution_producer(
                    conn, request.scope, source, expected_producer_generation
                )
                from .queue_execution_custody import require_custody_scope

                require_custody_scope(conn, key, repo, source)
                ts = self._now(now)
                conn.execute(
                    "INSERT INTO execution_admission_closures"
                    " (scope_key, repo, source, operation_id, request,"
                    " producer_generation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        repo,
                        source,
                        request.operation_id,
                        encoded,
                        expected_producer_generation,
                        ts,
                    ),
                )
                result = ExecutionClosure(request, source, expected_producer_generation, ts, False)
            conn.commit()
        return result

    def execution_closure_inventory(self, scope: ExecutionScope) -> dict[str, object]:
        """Read a complete queue-consistent inventory; never infer quiescence.

        Terminal tasks can retain an unresolved reservation, so reservation and
        wake enumeration is independent of task status and has no result limit.
        """
        key = _scope_key(scope)
        with self._connect() as conn:
            conn.execute("BEGIN")
            closure = conn.execute(
                "SELECT * FROM execution_admission_closures WHERE scope_key = ?", (key,)
            ).fetchone()
            if closure is None:
                raise TaskError("no execution closure for this qualified scope")
            params = (closure["repo"], closure["source"])
            tasks = conn.execute(
                "SELECT id, status, owner, owner_session_id, generation, wake_status"
                " FROM tasks WHERE repo = ? AND source = ? ORDER BY id",
                params,
            ).fetchall()
            reservations = conn.execute(
                "SELECT r.* FROM spawn_reservations r JOIN tasks t ON t.id = r.task_id"
                " WHERE t.repo = ? AND t.source = ? ORDER BY r.task_id, r.attempt",
                params,
            ).fetchall()
            wakes = conn.execute(
                "SELECT w.* FROM wake_outbox w JOIN tasks t ON t.id = w.task_id"
                " WHERE t.repo = ? AND t.source = ? ORDER BY w.task_id, w.wake_seq",
                params,
            ).fetchall()
            run_waiter_wakes = conn.execute(
                "SELECT w.* FROM run_waiter_wakes w JOIN tasks t ON t.id = w.task_id"
                " WHERE t.repo = ? AND t.source = ?"
                " ORDER BY w.task_id, w.waiter_generation, w.id",
                params,
            ).fetchall()
            sends = conn.execute(
                "SELECT * FROM execution_send_custody"
                " WHERE scope_key = ? ORDER BY created_at, send_id",
                (key,),
            ).fetchall()
            conn.commit()
        return {
            "request": json.loads(closure["request"]),
            "source": closure["source"],
            "producer_generation": closure["producer_generation"],
            "state": "closing",
            "acknowledged": False,
            "inventory_complete": True,
            "tasks": [dict(row) for row in tasks],
            "reservations": [dict(row) for row in reservations],
            "wakes": [dict(row) for row in wakes],
            "run_waiter_wakes": [dict(row) for row in run_waiter_wakes],
            "sends": [dict(row) for row in sends],
            "blockers": [
                "runtime enrollment and final launch barrier not implemented",
                "legacy execution associations not qualified",
                "adopter effect barrier not observed",
            ],
        }
