"""Observe outside SQLite, then recheck the original proposal without advancing it."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from .assignment_workspace import AssignmentWorkspace
from .managed_assignment_proof import creation_stamp, managed_allocation
from .queue_pool_placement import require_pool_start
from .queue_records import TaskError
from .queue_relationship_continuation import current_binding
from .queue_submission_preparation import _binding, _encoded, _latest
from .queue_task_relationships import _matches_receipt, latest_relationship

if TYPE_CHECKING:
    from .queue import TaskQueue

_TASK_BOOKKEEPING = {
    "updated_at",
    "lease_expires_at",
    "latest_progress",
    "last_seen_at",
    "last_liveness",
    "activity",
    "activity_updated_at",
}


def _state(queue: TaskQueue, conn: sqlite3.Connection, task_id: str, request_id: str) -> dict[str, Any]:
    prepared = _latest(conn, task_id)
    task = queue._fetch(conn, task_id)
    if prepared is None or task is None:
        raise TaskError("submission readiness requires its retained preparation")
    value = json.loads(prepared["submission_preparation"])
    request = value["request"]
    if request["request_id"] != request_id:
        raise TaskError("submission readiness preparation request changed")
    if request.get("expected_status") is not None and task.status != request["expected_status"]:
        raise TaskError("submission readiness expected status changed")
    from .queue_submission_resubmission import retained_lineage
    retained_lineage(conn, request)
    workspace = AssignmentWorkspace.from_dict(request["workspace"])
    current = _binding(
        conn,
        task,
        worker_id=request["worker_id"],
        expected_generation=request["generation"],
        expected_owner_session_id=request["owner_session_id"],
        workspace=workspace,
    )
    if (
        _encoded({key: request[key] for key in current}) != _encoded(current)
        or task.awaiting_steer
    ):
        raise TaskError("submission readiness preparation binding changed or is held")
    event = latest_relationship(conn, task_id)
    if event is None:
        raise TaskError("submission readiness lacks its original relationship event")
    relationship = json.loads(event["task_relationship"])
    if not _matches_receipt(relationship.get("receipt"), relationship["request"], phase="bound"):
        raise TaskError("submission readiness requires its original bound task claim")
    if not require_pool_start(
        conn,
        task,
        task.owner,
        task.owner_session_id,
        task.generation,
    ):
        raise TaskError("submission readiness lacks a qualified managed allocation")
    reservation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?",
        (current_binding(relationship)["reservation_key"],),
    ).fetchone()
    if reservation is None:
        raise TaskError("submission readiness lost its original managed allocation")
    allocation = managed_allocation(conn, task_id, reservation, relationship)
    return {
        "preparation": value,
        "relationship": relationship,
        "task": {
            key: value
            for key, value in dict(
                conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            ).items()
            if key not in _TASK_BOOKKEEPING
        },
        "reservation": dict(reservation),
        "allocation": dict(allocation),
        "assignment": dict(
            conn.execute(
                "SELECT * FROM task_pool_assignments WHERE task_id=?",
                (task_id,),
            ).fetchone()
        ),
    }


def read_preparation(queue: TaskQueue, task_id: str, request_id: str) -> dict[str, Any]:
    with queue._connect() as conn:
        conn.execute("BEGIN")
        return _state(queue, conn, task_id, request_id)


def observe_owner_state(task_id: str, original: dict[str, Any]) -> dict[str, Any]:
    from .submission_readiness_observation import observe as observe_owner

    allocation = original["allocation"]
    return observe_owner(
        original["preparation"]["request"],
        creation_stamp(allocation),
        original["relationship"]["receipt"]["ref"],
    )


def observe(queue: TaskQueue, task_id: str, *, request_id: str) -> dict[str, Any]:
    """Internal point-in-time read, not a persisted permission to submit later."""
    original = read_preparation(queue, task_id, request_id)
    observation = observe_owner_state(task_id, original)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _encoded(_state(queue, conn, task_id, request_id)) != _encoded(original):
            raise TaskError("submission changed during owner observation; custody retained")
    return {"preparation": original["preparation"], "observation": observation}
