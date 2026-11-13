"""Freeze the queue-owned closeout relationship before mutable assignment release."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3

from .assignment_workspace import AssignmentWorkspace, same_assignment_worker
from .queue_common import Task
from .queue_records import TaskError
from .queue_relevance_delivery import closeout_owner


def closeout_detail(row: sqlite3.Row | None) -> dict | None:
    """Expose the captured binding without inventing one for a legacy closeout."""
    if row is None:
        return None
    result = dict(row)
    for column in ("resource_binding", "readiness"):
        raw = result.get(column)
        result[column] = json.loads(raw) if raw is not None else None
    return result


def capture_binding(conn: sqlite3.Connection, task: Task) -> dict:
    """Inspect only this acceptance transaction; never contact a ground owner."""
    if not closeout_owner(task) or not task.owner_session_id:
        return {"state": "unconfirmed", "reason": "owner_unavailable"}
    try:
        return _capture(conn, task)
    except TaskError as exc:
        # Notification remains useful even when resource disposition needs recovery.
        return {"state": "unconfirmed", "reason": str(exc)}


def _capture(conn: sqlite3.Connection, task: Task) -> dict:
    from .queue_relationship_continuation import current_binding
    from .queue_task_relationships import _matches_receipt, latest_relationship, require_relationship

    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id=?", (task.id,),
    ).fetchone()
    reservation = None
    if assignment is not None:
        reservation = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (assignment["reservation_key"],),
        ).fetchone()
        if (
            assignment["assignment_released"] or assignment["task_generation"] != task.generation
            or reservation is None or reservation["task_id"] != task.id
        ):
            raise TaskError("current_pool_assignment_unconfirmed")
        event = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? AND id=? AND task_relationship IS NOT NULL",
            (task.id, reservation["task_relationship_event_id"]),
        ).fetchone()
    else:
        event = latest_relationship(conn, task.id)
    if event is None:
        raise TaskError("original_relationship_unavailable")
    value = json.loads(event["task_relationship"])
    request, binding = value["request"], current_binding(value)
    workspace = AssignmentWorkspace.from_dict(request["workspace"])
    queue_identity = conn.execute(
        "SELECT identity FROM workspace_queue_identity WHERE singleton=1",
    ).fetchone()["identity"]
    if (
        value["state"] != "bound" or binding["generation"] != task.generation
        or request["task_id"] != task.id or request["event_id"] != event["id"]
        or request["recovery"]["queue_identity"] != queue_identity
        or not same_assignment_worker(event["worker"], workspace.worker_id)
        or not same_assignment_worker(closeout_owner(task), workspace.worker_id)
        or binding.get("owner_session_id", task.owner_session_id) != task.owner_session_id
        or not _matches_receipt(value["receipt"], request, phase="bound")
    ):
        raise TaskError("original_relationship_binding_unconfirmed")
    require_relationship(conn, task.id)
    if reservation is None:
        if binding["reservation_key"] is not None:
            raise TaskError("original_reservation_unavailable")
    else:
        from .ordinary_native_bindings import reservation_native_identity

        if (
            binding["reservation_key"] != reservation["key"]
            or not reservation["task_relationship_required"]
            or reservation["task_relationship_event_id"] != event["id"]
            or reservation["task_relationship_generation"] != task.generation
            or asdict(workspace) != {
                "machine": reservation["execution_machine"],
                "project": reservation["allocation_project"], "worktree": reservation["worktree"],
                "path": reservation["worktree_path"], "creation_nonce": reservation["creation_nonce"],
            }
            or reservation_native_identity(conn, reservation) != task.owner_session_id
        ):
            raise TaskError("original_reservation_binding_unconfirmed")
    return {
        "state": "captured", "queue_identity": queue_identity,
        "assignment_event_id": event["id"], "relationship_request_id": request["request_id"],
        "workspace": asdict(workspace), "reservation_key": binding["reservation_key"],
        "startup_revision": reservation["startup_revision"] if reservation is not None else None,
    }
