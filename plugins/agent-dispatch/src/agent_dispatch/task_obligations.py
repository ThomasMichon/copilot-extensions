"""Provider-owned task responsibility facts; never a worktree release operation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .queue_records import SpawnState, Status

if TYPE_CHECKING:
    from .queue import TaskQueue

SCHEMA = "dispatch-task-obligation"
VERSION = 1
_BINDINGS = {
    "owner", "native", "target_worktree", "worktree_requirement", "pool_assignment", "spawn_reservation",
}
_UNASSIGNABLE = {Status.CLAIMED, Status.STARTED, Status.SUSPENDED}


def _worktree_requirement(raw: str) -> bool:
    try:
        requires = json.loads(raw)
    except (ValueError, TypeError):
        return True
    if not isinstance(requires, list) or any(not isinstance(item, str) for item in requires):
        return True
    return any(item.startswith("worktree:") for item in requires)


def read_snapshot(queue: TaskQueue, task_id: str) -> dict | None:
    """Read task, logical assignment and disposition history in one snapshot."""
    conn = queue._connect()
    try:
        conn.execute("BEGIN")
        task = conn.execute(
            "SELECT id, status, generation, owner, owner_session_id, target_worktree, requires,"
            " hold_reason, hold_actor, hold_at FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if task is None:
            return None
        assignment = conn.execute(
            "SELECT 1 FROM task_pool_assignments WHERE task_id=? AND assignment_released IS NOT 1",
            (task_id,),
        ).fetchone()
        reservation = conn.execute(
            "SELECT 1 FROM spawn_reservations WHERE task_id=?"
            " AND (state IS NULL OR state NOT IN (?, ?, ?, ?))"
            " AND (state IS NOT ? OR release_requested IS NOT 1) LIMIT 1",
            (task_id, SpawnState.SETTLED, SpawnState.FAILED, SpawnState.REARMED, SpawnState.DEFERRED,
             SpawnState.RELEASING),
        ).fetchone()
        transition = conn.execute(
            "SELECT id, from_status, to_status, unassigned_worker FROM task_events"
            " WHERE task_id=? AND from_status IS NOT to_status ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        unassignment = None
        if (
            transition is not None and transition["from_status"] in _UNASSIGNABLE
            and transition["to_status"] == Status.QUEUED and transition["unassigned_worker"]
        ):
            unassignment = {
                "event_id": transition["id"], "from_state": transition["from_status"],
                "worker_id": transition["unassigned_worker"],
            }
        result = {
            "schema": SCHEMA, "version": VERSION, "task_id": task["id"],
            "state": task["status"], "generation": task["generation"],
            "bindings": {
                "owner": task["owner"] is not None,
                "native": task["owner_session_id"] is not None,
                "target_worktree": task["target_worktree"] is not None,
                "worktree_requirement": _worktree_requirement(task["requires"]),
                "pool_assignment": assignment is not None,
                "spawn_reservation": reservation is not None,
            },
            "held": any(task[key] is not None for key in ("hold_reason", "hold_actor", "hold_at")),
            "unassignment": unassignment,
        }
        return {**result, "release_outcome": _release_outcome(result)}
    finally:
        conn.rollback()
        conn.close()


def _release_outcome(value: dict) -> str | None:
    if value["state"] == Status.COMPLETED:
        return "completed"
    if (
        value["state"] == Status.QUEUED and not value["held"]
        and not any(value["bindings"][key] for key in _BINDINGS)
        and value["unassignment"] is not None
    ):
        return "unassigned_queued"
    return None


def valid_snapshot(value: object, *, task_id: str) -> bool:
    """Reject unsupported or inconsistent evidence, including bool-as-version."""
    if not isinstance(value, dict) or not {
        "schema", "version", "task_id", "state", "generation", "bindings", "held",
        "unassignment", "release_outcome",
    } <= value.keys():
        return False
    if (
        value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != VERSION
        or value["task_id"] != task_id or not isinstance(value["state"], str)
        or type(value["generation"]) is not int or not 0 <= value["generation"] <= 2**63 - 1
        or type(value["held"]) is not bool
        or not isinstance(value["bindings"], dict) or not _BINDINGS <= value["bindings"].keys()
        or any(type(value["bindings"][key]) is not bool for key in _BINDINGS)
    ):
        return False
    unassignment = value["unassignment"]
    if unassignment is not None and (
        not isinstance(unassignment, dict)
        or not {"event_id", "from_state", "worker_id"} <= unassignment.keys()
        or type(unassignment["event_id"]) is not int or unassignment["event_id"] < 1
        or not isinstance(unassignment["from_state"], str)
        or unassignment["from_state"] not in _UNASSIGNABLE
        or not isinstance(unassignment["worker_id"], str)
        or not 0 < len(unassignment["worker_id"]) <= 4096
    ):
        return False
    return value["release_outcome"] == _release_outcome(value)
