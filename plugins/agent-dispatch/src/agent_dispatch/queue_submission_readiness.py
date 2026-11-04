"""Observe outside SQLite, then recheck the original proposal without advancing it."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .assignment_workspace import AssignmentWorkspace
from .queue_pool_placement import require_pool_start
from .queue_records import TaskError
from .queue_relationship_continuation import current_binding
from .queue_submission_preparation import _binding, _encoded, _latest
from .queue_task_relationships import _matches_receipt, latest_relationship

if TYPE_CHECKING:
    from .queue import TaskQueue


def _state(queue: TaskQueue, conn: sqlite3.Connection, task_id: str, request_id: str) -> dict:
    prepared = _latest(conn, task_id)
    task = queue._fetch(conn, task_id)
    if prepared is None or task is None:
        raise TaskError("submission readiness requires its retained preparation")
    value = json.loads(prepared["submission_preparation"])
    request = value["request"]
    if request["request_id"] != request_id:
        raise TaskError("submission readiness preparation request changed")
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
    relationship = json.loads(latest_relationship(conn, task_id)["task_relationship"])
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
    original_key = reservation["allocation_reservation_key"] or reservation["key"]
    allocation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?",
        (original_key,),
    ).fetchone()
    if (
        allocation is None
        or relationship["request"]["reservation_key"] != original_key
        or allocation["task_id"] != task_id
        or allocation["worktree_ownership"] != "created"
        or allocation["creation_nonce"] != workspace.creation_nonce
        or allocation["worktree"] != workspace.worktree
        or allocation["allocation_project"] != workspace.project
        or allocation["worktree_path"] != workspace.path
        or allocation["execution_machine"] != workspace.machine
        or allocation["driver"] not in {"cli", "ordinary-bridge"}
        or not isinstance(allocation["reserved_by"], str)
        or not allocation["reserved_by"]
    ):
        raise TaskError("submission readiness lost its original managed allocation")
    return {
        "preparation": value,
        "relationship": relationship,
        "task": dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()),
        "reservation": dict(reservation),
        "allocation": dict(allocation),
        "assignment": dict(
            conn.execute(
                "SELECT * FROM task_pool_assignments WHERE task_id=?",
                (task_id,),
            ).fetchone()
        ),
    }


def observe(queue: TaskQueue, task_id: str, *, request_id: str) -> dict:
    """Internal point-in-time read, not a persisted permission to submit later."""
    with queue._connect() as conn:
        conn.execute("BEGIN")
        original = _state(queue, conn, task_id, request_id)
    from .submission_readiness_observation import observe as observe_owner

    allocation = original["allocation"]
    observation = observe_owner(
        original["preparation"]["request"],
        {
            "task_id": task_id,
            "reservation_key": allocation["key"],
            "attempt": allocation["attempt"],
            "driver": allocation["driver"],
            "supervisor": allocation["reserved_by"],
            "creator_machine": allocation["execution_machine"],
            "ownership": "created",
        },
        original["relationship"]["receipt"]["ref"],
    )
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _encoded(_state(queue, conn, task_id, request_id)) != _encoded(original):
            raise TaskError("submission changed during owner observation; custody retained")
    return {"preparation": original["preparation"], "observation": observation}
