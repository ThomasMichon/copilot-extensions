"""Ordinary completion uses the existing retained proposal and readiness owners."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import TYPE_CHECKING

from .assignment_workspace import AssignmentWorkspace
from .queue_common import CompletionOutcome, StructuredResult, Task
from .queue_records import Status, TaskError
from .queue_submission_preparation import _encoded, _latest, submission_required_condition
from .queue_task_relationships import latest_relationship

if TYPE_CHECKING:
    from .queue import TaskQueue


def original_request(
    conn: sqlite3.Connection, task: Task, worker_id: str, *,
    allowed: set[str], expected_status: str | None,
) -> dict | None:
    """Resolve requirement and retry identity under the completion owner's lock."""
    required = conn.execute(
        "SELECT " + submission_required_condition("t") + " FROM tasks t WHERE t.id=?",  # noqa: S608 -- fixed predicate
        (task.id,),
    ).fetchone()[0]
    if not required:
        return None
    prepared = _latest(conn, task.id)
    previous = json.loads(prepared["submission_preparation"]) if prepared is not None else None
    if task.status in {Status.SUBMITTED, Status.COMPLETED}:
        if previous is None:
            raise TaskError("protected historical submission has no retained preparation")
        request = previous["request"]
        return {
            "request_id": request["request_id"],
            "expected_generation": task.generation,
            "expected_owner_session_id": task.owner_session_id,
            "workspace": AssignmentWorkspace.from_dict(request["workspace"]),
            "expected_status": expected_status,
        }
    if task.status not in allowed:
        raise TaskError(f"cannot complete a {task.status!r} task (allowed: {sorted(allowed)})")
    if task.owner != worker_id:
        raise TaskError("protected submission requires its original assigned worker")
    relationship = latest_relationship(conn, task.id)
    if relationship is None:
        raise TaskError("protected submission requires its original workspace assignment")
    workspace = AssignmentWorkspace.from_dict(
        json.loads(relationship["task_relationship"])["request"]["workspace"],
    )
    request_id = (
        previous["request"]["request_id"]
        if previous is not None and previous.get("advancement") is None
        else f"complete:{relationship['id']}:{task.generation}:{prepared['id'] if prepared else 0}"
    )
    return {
        "request_id": request_id,
        "expected_generation": task.generation,
        "expected_owner_session_id": task.owner_session_id,
        "workspace": workspace,
        "expected_status": expected_status,
    }


def _exact_replay(
    queue: TaskQueue, task_id: str, worker_id: str, arguments: dict,
) -> CompletionOutcome | None:
    from .queue_submission_advancement import _replay

    with queue._connect() as conn:
        conn.execute("BEGIN")
        task = queue._fetch(conn, task_id)
        row = _latest(conn, task_id)
        if task is None or task.status not in {Status.SUBMITTED, Status.COMPLETED} or row is None:
            return None
        request = json.loads(row["submission_preparation"])["request"]
        expected = {
            "request_id": arguments["request_id"], "worker_id": worker_id,
            "generation": arguments["expected_generation"],
            "owner_session_id": arguments["expected_owner_session_id"],
            "workspace": asdict(arguments["workspace"]),
            "result": arguments["result"], "result_ref": arguments["result_ref"],
        }
        if arguments["expected_status"] is not None:
            expected["expected_status"] = arguments["expected_status"]
        if _encoded({key: request.get(key) for key in expected}) != _encoded(expected):
            return None
        return _replay(queue, conn, task_id, arguments["request_id"])


def complete(
    queue: TaskQueue, task_id: str, worker_id: str, arguments: dict, *,
    result: StructuredResult | None, result_ref: str | None, now: float,
) -> CompletionOutcome:
    """Retain before observing; a lost reply only permits an exact committed replay."""
    arguments = {**arguments, "result": result, "result_ref": result_ref}
    prepared = False
    try:
        replay = _exact_replay(queue, task_id, worker_id, arguments)
        if replay is not None:
            return replay
        queue.prepare_submission(task_id, worker_id, **arguments, now=now)
        prepared = True
        return queue.advance_prepared_submission(task_id, request_id=arguments["request_id"])
    except TaskError as exc:
        replay = _exact_replay(queue, task_id, worker_id, arguments)
        if replay is not None:
            return replay
        if prepared:
            try:
                queue.record_submission_blocker(
                    task_id, request_id=arguments["request_id"],
                    code="submission-readiness", message=str(exc)[:4096],
                )
            except TaskError:
                # A changed binding cannot authorize even a blocker write.
                pass
        raise TaskError(f"submission requires authoritative readiness: {exc}") from exc
