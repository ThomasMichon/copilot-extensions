"""Retain an exact proposed result without submitting or releasing its owner."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from .assignment_workspace import AssignmentWorkspace
from .queue_common import CompletionOutcome, StructuredResult, Task, _task_transition_spec
from .queue_execution_handoff import require_execution_admission
from .queue_records import TaskError
from .queue_task_relationships import latest_relationship, require_relationship


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise TaskError(
            f"submission preparation requires a nonempty {name} up to {limit} characters"
        )
    return value


def _latest(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, submission_preparation FROM task_events"
        " WHERE task_id=? AND submission_preparation IS NOT NULL ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def require_unprepared_submission(conn: sqlite3.Connection, task_id: str) -> None:
    if _latest(conn, task_id) is not None:
        raise TaskError(
            "task has a retained submission preparation;"
            " authoritative readiness is not yet available"
        )


def migrate_submission_preparation(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_events)")}
    if "submission_preparation" not in columns:
        try:
            conn.execute("ALTER TABLE task_events ADD COLUMN submission_preparation TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_submission_preparation ON task_events(task_id, id)"
        " WHERE submission_preparation IS NOT NULL"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_request_retained"
        " BEFORE UPDATE ON task_events WHEN OLD.submission_preparation IS NOT NULL AND ("
        " NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id OR NEW.worker IS NOT OLD.worker"
        " OR NEW.from_status IS NOT OLD.from_status OR NEW.to_status IS NOT OLD.to_status"
        " OR NEW.ts IS NOT OLD.ts OR NEW.note IS NOT OLD.note"
        " OR NEW.submission_preparation IS NULL"
        " OR NOT json_valid(NEW.submission_preparation)"
        " OR json_extract(NEW.submission_preparation, '$.version')"
        " IS NOT json_extract(OLD.submission_preparation, '$.version')"
        " OR json_extract(NEW.submission_preparation, '$.request')"
        " IS NOT json_extract(OLD.submission_preparation, '$.request'))"
        " BEGIN SELECT RAISE(ABORT, 'submission preparation request is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_event_retained"
        " BEFORE DELETE ON task_events WHEN OLD.submission_preparation IS NOT NULL"
        " BEGIN SELECT RAISE(ABORT, 'submission preparation event is retained'); END"
    )
    # Install the replacement before removing the stricter rollback-era trigger.
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_completion_guard_v2"
        " BEFORE UPDATE ON tasks"
        " WHEN NEW.status IN ('submitted','completed')"
        " AND OLD.status NOT IN ('submitted','completed')"
        " AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND e.submission_preparation IS NOT NULL)"
        " AND NOT EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND json_extract(e.submission_preparation, '$.advancement.state')='admitting'"
        " AND json_extract(e.submission_preparation, '$.advancement.version')=1"
        " AND NEW.status='submitted' AND NEW.require_verification=1"
        " AND OLD.hold_reason IS NULL AND OLD.awaiting_steer=0"
        " AND NEW.owner IS NULL AND NEW.lease_expires_at IS NULL"
        " AND OLD.owner=json_extract(e.submission_preparation, '$.request.worker_id')"
        " AND NEW.completed_by=OLD.owner"
        " AND NEW.id=json_extract(e.submission_preparation, '$.request.task_id')"
        " AND NEW.generation=OLD.generation"
        " AND NEW.generation=json_extract(e.submission_preparation, '$.request.generation')"
        " AND NEW.owner_session_id=OLD.owner_session_id"
        " AND NEW.owner_session_id="
        " json_extract(e.submission_preparation, '$.request.owner_session_id')"
        " AND NEW.completed_at=json_extract(e.submission_preparation, '$.advancement.at')"
        " AND NEW.result IS"
        " json_extract(e.submission_preparation, '$.advancement.encoded_result')"
        " AND NEW.result_ref IS json_extract(e.submission_preparation, '$.request.result_ref'))"
        " BEGIN SELECT RAISE(ABORT,"
        " 'submission preparation requires authoritative readiness'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_consume_admission"
        " AFTER UPDATE OF status ON tasks"
        " WHEN NEW.status='submitted' AND OLD.status NOT IN ('submitted','completed')"
        " BEGIN UPDATE task_events SET submission_preparation="
        " json_set(submission_preparation, '$.advancement.state', 'submitted')"
        " WHERE task_id=NEW.id"
        " AND json_extract(submission_preparation, '$.advancement.state')='admitting'; END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_advancement_retained"
        " BEFORE UPDATE ON task_events"
        " WHEN json_extract(OLD.submission_preparation, '$.advancement.state')='submitted'"
        " AND json_extract(NEW.submission_preparation, '$.advancement')"
        " IS NOT json_extract(OLD.submission_preparation, '$.advancement')"
        " BEGIN SELECT RAISE(ABORT, 'submission advancement receipt is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_result_retained_v2"
        " BEFORE UPDATE ON tasks"
        " WHEN OLD.status IN ('submitted','completed')"
        " AND NEW.status IN ('submitted','completed')"
        " AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=OLD.id"
        " AND e.submission_preparation IS NOT NULL)"
        " AND (NEW.result IS NOT OLD.result OR NEW.result_ref IS NOT OLD.result_ref"
        " OR NEW.completed_by IS NOT OLD.completed_by OR NEW.completed_at IS NOT OLD.completed_at"
        " OR NEW.require_verification<>1)"
        " BEGIN SELECT RAISE(ABORT, 'submission preparation result is retained'); END"
    )
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_result_retained")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_completion_guard")


def _binding(
    conn: sqlite3.Connection,
    task: Task,
    *,
    worker_id: str,
    expected_generation: int,
    expected_owner_session_id: str,
    workspace: AssignmentWorkspace,
) -> dict:
    allowed, _ = _task_transition_spec("complete")
    require_execution_admission(conn, task.id)
    require_relationship(conn, task.id)
    if (
        task.status not in allowed
        or task.owner != worker_id
        or task.generation != expected_generation
        or task.owner_session_id != expected_owner_session_id
    ):
        raise TaskError("submission preparation ownership incarnation changed")
    if task.hold_reason is not None:
        raise TaskError("held task cannot prepare or advance submission")
    row = latest_relationship(conn, task.id)
    if row is None:
        raise TaskError(
            "submission preparation requires its original protected workspace assignment"
        )
    relationship = json.loads(row["task_relationship"])
    request = relationship["request"]
    continuation = relationship.get("continuation") or {}
    if (
        relationship["state"] != "bound"
        or request["workspace"] != asdict(workspace)
        or continuation.get("generation", request["generation"]) != task.generation
        or (
            "owner_session_id" in continuation
            and continuation["owner_session_id"] != task.owner_session_id
        )
    ):
        raise TaskError("submission preparation workspace assignment changed or is unconfirmed")
    return {
        "task_id": task.id,
        "queue_identity": conn.execute(
            "SELECT identity FROM workspace_queue_identity WHERE singleton=1",
        ).fetchone()["identity"],
        "worker_id": worker_id,
        "generation": task.generation,
        "owner_session_id": task.owner_session_id,
        "workspace": asdict(workspace),
        "assignment_event_id": row["id"],
        "assignment_request_id": request["request_id"],
        "assignment_continuation": relationship.get("continuation"),
    }


class QueueSubmissionPreparationMixin:
    def advance_prepared_submission(self, task_id: str, *, request_id: str) -> CompletionOutcome:
        """Internal guarded submission only; no ordinary caller capability."""
        from .queue_submission_advancement import advance

        return advance(self, task_id, request_id=request_id)

    def observe_submission_readiness(self, task_id: str, *, request_id: str) -> dict:
        """Internal owner observation only; ordinary submission stays disabled."""
        from .queue_submission_readiness import observe

        return observe(self, task_id, request_id=request_id)

    def submission_preparation(self, task_id: str) -> dict | None:
        """Read the retained proposal even if its owner or task state changed."""
        with self._connect() as conn:
            row = _latest(conn, task_id)
        return json.loads(row["submission_preparation"]) if row is not None else None

    def prepare_submission(
        self,
        task_id: str,
        worker_id: str,
        *,
        request_id: str,
        expected_generation: int,
        expected_owner_session_id: str,
        workspace: AssignmentWorkspace,
        result: StructuredResult | None = None,
        result_ref: str | None = None,
        now: float | None = None,
    ) -> dict:
        """Internal custody only; no readiness proof or caller support is advertised."""
        _text(request_id, "request_id", 128)
        _text(expected_owner_session_id, "native owner_session_id", 256)
        if type(expected_generation) is not int or expected_generation < 0:
            raise TaskError("submission preparation requires an exact generation")
        if not isinstance(workspace, AssignmentWorkspace) or workspace.worker_id != worker_id:
            raise TaskError("submission preparation requires the assigned workspace worker")
        if result_ref is not None:
            _text(result_ref, "result_ref", 4096)
        encoded_result = self._encode_result(result)
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            request = _binding(
                conn, task, worker_id=worker_id, expected_generation=expected_generation,
                expected_owner_session_id=expected_owner_session_id, workspace=workspace,
            )
            request.update(
                request_id=request_id,
                result=json.loads(encoded_result) if encoded_result is not None else None,
                result_ref=result_ref,
            )
            previous = _latest(conn, task_id)
            if previous is not None:
                value = json.loads(previous["submission_preparation"])
                if _encoded(value["request"]) != _encoded(request):
                    raise TaskError("submission preparation conflicts with its original request")
                return value
            value = {"version": 1, "request": request, "blocker": None}
            conn.execute(
                "INSERT INTO task_events"
                " (task_id, ts, from_status, to_status, worker, note, submission_preparation)"
                " VALUES (?, ?, ?, ?, ?, 'submission prepared; not submitted', ?)",
                (task_id, ts, task.status, task.status, worker_id, _encoded(value)),
            )
            return value

    def record_submission_blocker(
        self,
        task_id: str,
        *,
        request_id: str,
        code: str,
        message: str,
        now: float | None = None,
    ) -> dict:
        """Retain a bounded failure against the same current proposal, never a success."""
        _text(code, "blocker code", 128)
        _text(message, "blocker message", 4096)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _latest(conn, task_id)
            if row is None:
                raise TaskError("task has no retained submission preparation")
            value = json.loads(row["submission_preparation"])
            request = value["request"]
            if request["request_id"] != request_id:
                raise TaskError("submission preparation request changed")
            task = self._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            current = _binding(
                conn, task, worker_id=request["worker_id"],
                expected_generation=request["generation"],
                expected_owner_session_id=request["owner_session_id"],
                workspace=AssignmentWorkspace.from_dict(request["workspace"]),
            )
            if _encoded({key: request[key] for key in current}) != _encoded(current):
                raise TaskError("submission preparation binding changed")
            value["blocker"] = {"code": code, "message": message, "at": self._now(now)}
            conn.execute(
                "UPDATE task_events SET submission_preparation=? WHERE id=?",
                (_encoded(value), row["id"]),
            )
            return value
