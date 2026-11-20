"""Resolve one managed claim's creation stamp without replacing its execution binding."""

from __future__ import annotations

import sqlite3
from typing import Any

from .assignment_workspace import AssignmentWorkspace
from .queue_records import TaskError
from .queue_relationship_continuation import current_binding
from .queue_task_relationships import _matches_receipt


def managed_allocation(
    conn: sqlite3.Connection,
    task_id: str,
    reservation: sqlite3.Row,
    relationship: dict[str, Any],
) -> dict[str, Any]:
    """Return the Worktrees creation owner; the caller checks the executing native separately."""
    request = relationship["request"]
    binding = current_binding(relationship)
    workspace = AssignmentWorkspace.from_dict(request["workspace"])
    if (
        request["task_id"] != task_id
        or reservation["task_id"] != task_id
        or not reservation["task_relationship_required"]
        or reservation["task_relationship_event_id"] != request["event_id"]
        or reservation["task_relationship_generation"] != binding["generation"]
        or reservation["key"] != binding["reservation_key"]
        or relationship["state"] not in {"bound", "closed"}
        or not _matches_receipt(relationship["receipt"], request, phase=relationship["state"])
    ):
        raise TaskError("managed allocation lost its original relationship binding")
    root = reservation["allocation_reservation_key"] or reservation["key"]
    if reservation["driver"] == "ordinary-bridge" and reservation["key"] != root:
        from .ordinary_capture_proof import original_evidence

        allocation = original_evidence(conn, request)
    else:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (root,)).fetchone()
        if row is None or request["reservation_key"] != root:
            raise TaskError("managed allocation lost its original workspace receipt")
        allocation = dict(row)
    if (
        allocation["key"] != root
        or allocation["task_id"] != task_id
        or allocation["worktree_ownership"] != "created"
        or allocation["driver"] != reservation["driver"]
        or allocation["driver"] not in {"cli", "ordinary-bridge"}
        or not isinstance(allocation["reserved_by"], str)
        or not allocation["reserved_by"]
        or any(
            allocation[k] != expected or reservation[k] != expected
            for k, expected in (
                ("execution_machine", workspace.machine),
                ("allocation_project", workspace.project),
                ("worktree", workspace.worktree),
                ("worktree_path", workspace.path),
                ("creation_nonce", workspace.creation_nonce),
            )
        )
    ):
        raise TaskError("managed allocation lost its original workspace incarnation")
    return allocation


def creation_stamp(allocation: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": allocation["task_id"],
        "reservation_key": allocation["key"],
        "attempt": allocation["attempt"],
        "driver": allocation["driver"],
        "supervisor": allocation["reserved_by"],
        "creator_machine": allocation["execution_machine"],
        "ownership": "created",
    }
