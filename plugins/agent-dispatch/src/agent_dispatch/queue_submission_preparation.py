"""Retain an exact proposed result without submitting or releasing its owner."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from .queue import TaskQueue

from .assignment_workspace import AssignmentWorkspace, same_assignment_worker
from .queue_common import CompletionOutcome, StructuredResult, Task, _task_transition_spec
from .queue_execution_handoff import require_execution_admission
from .queue_records import TaskError
from .relationship_decline_history import TaskAlias, relationship_history, require_if_not_discharged
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
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, submission_preparation FROM task_events"
        " WHERE task_id=? AND submission_preparation IS NOT NULL ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return row


def previous_for_assignment(conn: sqlite3.Connection, task_id: str, acquisition: int) -> sqlite3.Row | None:
    previous = _latest(conn, task_id)
    if previous is None:
        return None
    from .completion_origins import PREPARATION_ORIGINS
    origin = conn.execute(
        "SELECT assignment_event_id FROM (" + PREPARATION_ORIGINS + ") WHERE task_id=? AND preparation_event_id=?",
        (task_id, previous["id"]),
    ).fetchone()
    if origin is not None and origin[0] == acquisition:
        return previous
    from .relationship_decline_history import prior_proposals_discharged
    if (origin is not None and origin[0] is not None and previous["id"] < acquisition
            and prior_proposals_discharged(conn, task_id, acquisition)):
        return None
    raise TaskError("retained submission preparation belongs to an unconfirmed assignment")


def require_request_slot(
    conn: sqlite3.Connection, task_id: str, request_id: str, previous: sqlite3.Row | None,
) -> None:
    if conn.execute(
        "SELECT 1 FROM task_events WHERE task_id=? AND submission_preparation IS NOT NULL"
        " AND json_extract(submission_preparation, '$.request.request_id')=? AND id IS NOT ? LIMIT 1",
        (task_id, request_id, previous["id"] if previous else None),
    ).fetchone() is not None:
        raise TaskError("submission preparation request was superseded")


def require_unprepared_submission(conn: sqlite3.Connection, task_id: str) -> None:
    required = conn.execute(
        "SELECT " + retained_preparation_condition("t") + " FROM tasks t WHERE t.id=?", (task_id,),
    ).fetchone()
    if required is not None and required[0]:
        raise TaskError(
            "task has a retained submission preparation;"
            " authoritative readiness is not yet available"
        )


def _preparation_history(task: TaskAlias) -> str:
    return f"EXISTS (SELECT 1 FROM task_events e WHERE e.task_id={task}.id AND e.submission_preparation IS NOT NULL)"


def retained_preparation_condition(task: TaskAlias) -> str:
    return require_if_not_discharged(task, _preparation_history(task))


def submission_required_condition(task: Literal["t", "NEW"]) -> str:
    return require_if_not_discharged(task, relationship_history(task) + " OR " + _preparation_history(task))


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
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_completion_guard_v6"
        " BEFORE UPDATE ON tasks"
        " WHEN NEW.status IN ('submitted','completed')"
        " AND OLD.status NOT IN ('submitted','completed')"
        " AND (" + retained_preparation_condition("OLD")
        + " OR (NEW.generation!=OLD.generation AND " + _preparation_history("OLD") + "))"
        + " AND NOT EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
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
        "CREATE TRIGGER IF NOT EXISTS submission_required_preparation_guard_v8"  # noqa: S608 -- fixed predicate
        " BEFORE UPDATE OF status ON tasks"
        " WHEN NEW.status IN ('submitted','completed')"
        " AND OLD.status NOT IN ('submitted','completed')"
        " AND (" + submission_required_condition("NEW") + ")"
        " AND NOT EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND e.submission_preparation IS NOT NULL)"
        " BEGIN SELECT RAISE(ABORT, 'submission requires retained preparation and readiness'); END"
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
        "CREATE TRIGGER IF NOT EXISTS submission_preparation_result_retained_v6"
        " BEFORE UPDATE ON tasks"
        " WHEN OLD.status IN ('submitted','completed')"
        " AND NEW.status IN ('submitted','completed')"
        " AND " + retained_preparation_condition("OLD")
        + " AND (NEW.result IS NOT OLD.result OR NEW.result_ref IS NOT OLD.result_ref"
        " OR NEW.completed_by IS NOT OLD.completed_by OR NEW.completed_at IS NOT OLD.completed_at"
        " OR (OLD.require_verification=1 AND NEW.require_verification IS NOT 1))"
        " BEGIN SELECT RAISE(ABORT, 'submission preparation result is retained'); END"
    )
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_result_retained_v5")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_result_retained_v4")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_completion_guard_v5")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_completion_guard_v4")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard_v7")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard_v6")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_result_retained_v3")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_completion_guard_v3")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard_v5")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_completion_guard_v2")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_result_retained_v2")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard_v4")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard_v3")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard_v2")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_result_retained")
    conn.execute("DROP TRIGGER IF EXISTS submission_required_preparation_guard")
    conn.execute("DROP TRIGGER IF EXISTS submission_preparation_completion_guard")


def _binding(
    conn: sqlite3.Connection,
    task: Task,
    *,
    worker_id: str,
    expected_generation: int,
    expected_owner_session_id: str,
    workspace: AssignmentWorkspace,
) -> dict[str, Any]:
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
    @property
    def _submission_queue(self) -> TaskQueue:
        return cast("TaskQueue", self)

    def advance_prepared_submission(self, task_id: str, *, request_id: str) -> CompletionOutcome:
        """Internal guarded submission only; no ordinary caller capability."""
        from .queue_submission_advancement import advance

        return advance(self._submission_queue, task_id, request_id=request_id)

    def observe_submission_readiness(self, task_id: str, *, request_id: str) -> dict[str, Any]:
        """Internal owner observation only; ordinary submission stays disabled."""
        from .queue_submission_readiness import observe

        return observe(self._submission_queue, task_id, request_id=request_id)

    def submission_preparation(self, task_id: str) -> dict[str, Any] | None:
        """Read the retained proposal even if its owner or task state changed."""
        with self._submission_queue._connect() as conn:
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
        expected_status: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Internal custody only; no readiness proof or caller support is advertised."""
        _text(request_id, "request_id", 128)
        _text(expected_owner_session_id, "native owner_session_id", 256)
        if type(expected_generation) is not int or expected_generation < 0:
            raise TaskError("submission preparation requires an exact generation")
        if not isinstance(workspace, AssignmentWorkspace) or not same_assignment_worker(
            workspace.worker_id, worker_id,
        ):
            raise TaskError("submission preparation requires the assigned workspace worker")
        if result_ref is not None:
            _text(result_ref, "result_ref", 4096)
        if expected_status is not None and expected_status not in _task_transition_spec("complete")[0]:
            raise TaskError("submission preparation has an invalid expected status")
        encoded_result = self._submission_queue._encode_result(result)
        ts = self._submission_queue._now(now)
        with self._submission_queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._submission_queue._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            if expected_status is not None and task.status != expected_status:
                raise TaskError("submission preparation expected status changed")
            request = _binding(
                conn, task, worker_id=worker_id, expected_generation=expected_generation,
                expected_owner_session_id=expected_owner_session_id, workspace=workspace,
            )
            request.update(
                request_id=request_id,
                result=json.loads(encoded_result) if encoded_result is not None else None,
                result_ref=result_ref,
            )
            if expected_status is not None:
                request["expected_status"] = expected_status
            previous = previous_for_assignment(conn, task_id, request["assignment_event_id"])
            require_request_slot(conn, task_id, request_id, previous)
            if previous is not None:
                from .queue_submission_resubmission import rejected_preparation, retained_lineage

                value = json.loads(previous["submission_preparation"])
                if value["request"]["request_id"] == request_id:
                    if value.get("advancement") is not None:
                        raise TaskError("submission preparation request was already consumed")
                    lineage = retained_lineage(conn, value["request"])
                    if lineage is not None:
                        request["resubmission"] = lineage
                    if _encoded(value["request"]) != _encoded(request):
                        raise TaskError("submission preparation conflicts with its original request")
                    return cast(dict[str, Any], value)
                if value.get("advancement") is None:
                    raise TaskError("submission preparation conflicts with its original request")
                request["resubmission"] = rejected_preparation(conn, previous, request)
            value = {"version": 1, "request": request, "blocker": None}
            conn.execute(
                "INSERT INTO task_events"
                " (task_id, ts, from_status, to_status, worker, note, submission_preparation)"
                " VALUES (?, ?, ?, ?, ?, 'submission prepared; not submitted', ?)",
                (task_id, ts, task.status, task.status, worker_id, _encoded(value)),
            )
            return cast(dict[str, Any], value)

    def record_submission_blocker(
        self,
        task_id: str,
        *,
        request_id: str,
        code: str,
        message: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Retain a bounded failure against the same current proposal, never a success."""
        _text(code, "blocker code", 128)
        _text(message, "blocker message", 4096)
        with self._submission_queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _latest(conn, task_id)
            if row is None:
                raise TaskError("task has no retained submission preparation")
            value = json.loads(row["submission_preparation"])
            request = value["request"]
            if request["request_id"] != request_id:
                raise TaskError("submission preparation request changed")
            from .queue_submission_resubmission import retained_lineage
            retained_lineage(conn, request)
            task = self._submission_queue._fetch(conn, task_id)
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
            value["blocker"] = {"code": code, "message": message, "at": self._submission_queue._now(now)}
            conn.execute(
                "UPDATE task_events SET submission_preparation=? WHERE id=?",
                (_encoded(value), row["id"]),
            )
            return cast(dict[str, Any], value)
