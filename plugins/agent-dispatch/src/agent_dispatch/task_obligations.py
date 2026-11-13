"""Provider-owned task responsibility facts; never a worktree release operation."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .queue_records import SpawnState, Status

if TYPE_CHECKING:
    from .queue import TaskQueue

SCHEMA = "dispatch-task-obligation"
VERSION = 1
ASSIGNMENT_PREDICATE = (
    "((to_status='claimed' AND note='claim' AND from_status IS NOT to_status)"
    " OR (note='worktree assignment' AND task_relationship IS NOT NULL))"
)
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


def read_snapshot(
    queue: TaskQueue, task_id: str, *, assignment_event_id: int | None = None,
) -> dict | None:
    """Read task, logical assignment and disposition history in one snapshot."""
    if assignment_event_id is not None and (
        type(assignment_event_id) is not int or not 1 <= assignment_event_id <= 2**63 - 1
    ):
        raise ValueError("invalid task assignment event identity")
    conn = queue._connect()
    try:
        conn.execute("BEGIN")
        task = conn.execute(
            "SELECT id, status, generation, owner, owner_session_id, target_worktree, requires,"
            " hold_reason, hold_actor, hold_at,"
            " (SELECT readiness FROM task_relevance_closeouts WHERE task_id=tasks.id)"
            " AS closeout_readiness,"
            " (SELECT identity FROM workspace_queue_identity WHERE singleton=1) AS queue_identity"
            " FROM tasks WHERE id=?", (task_id,),
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
        assigned = conn.execute(
            "SELECT id, worker, task_relationship FROM task_events WHERE task_id=? AND "
            + ASSIGNMENT_PREDICATE
            + (" AND id=?" if assignment_event_id is not None else "")
            + " ORDER BY id DESC LIMIT 1",
            (task_id, assignment_event_id) if assignment_event_id is not None else (task_id,),
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
            "queue_identity": task["queue_identity"],
            "assignment": (
                {"event_id": assigned["id"], "worker_id": assigned["worker"]}
                if assigned is not None and assigned["worker"] else None
            ),
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
        if assigned is not None and assigned["task_relationship"] is not None:
            result["relationship"] = json.loads(assigned["task_relationship"])["request"]
        if task["closeout_readiness"] is not None:
            from .queue_relevance_readiness import release_evidence
            result["closeout"] = release_evidence(queue, conn, task["closeout_readiness"])
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
    if _closeout_ready(value):
        return "relevance_closeout_ready"
    return None


def _closeout_ready(value: dict) -> bool:
    proof = value.get("closeout")
    assignment, request = value.get("assignment"), value.get("relationship")
    return bool(
        isinstance(proof, dict) and set(proof) == {
            "request_id", "generation", "assignment_event_id", "relationship_request_id",
            "worker_id", "native_id",
        }
        and not value["held"]
        and value["state"] in {Status.PROPOSED, Status.QUEUED, *Status.OWNED, Status.SUBMITTED}
        and type(proof["generation"]) is int and proof["generation"] == value["generation"]
        and type(proof["assignment_event_id"]) is int
        and isinstance(assignment, dict)
        and assignment == {"event_id": proof["assignment_event_id"], "worker_id": proof["worker_id"]}
        and isinstance(request, dict)
        and request.get("request_id") == proof["relationship_request_id"]
        and request.get("event_id") == proof["assignment_event_id"]
        and all(isinstance(proof[key], str) and bool(proof[key]) for key in (
            "request_id", "relationship_request_id", "worker_id", "native_id",
        ))
    )


def enrich_task_detail(
    queue: TaskQueue, detail: dict, *, assignment_event_id: int | None = None,
) -> dict:
    return {**detail, "task_obligation": read_snapshot(
        queue, detail["id"], assignment_event_id=assignment_event_id,
    )}


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
    if "queue_identity" in value and (
        not isinstance(value["queue_identity"], str)
        or re.fullmatch(r"[0-9a-f]{32}", value["queue_identity"]) is None
    ):
        return False
    assignment = value.get("assignment")
    if assignment is not None and (
        not isinstance(assignment, dict) or set(assignment) != {"event_id", "worker_id"}
        or type(assignment["event_id"]) is not int or not 1 <= assignment["event_id"] <= 2**63 - 1
        or not isinstance(assignment["worker_id"], str)
        or not 0 < len(assignment["worker_id"]) <= 4096
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
