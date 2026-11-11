"""Fence continued execution without replacing the original worktree obligation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from .assignment_workspace import AssignmentWorkspace, same_assignment_worker
from .queue_common import Task
from .queue_records import Status, TaskError
from .queue_standing_schema import STANDING_KIND
from .queue_task_relationships import latest_relationship, require_relationship


def current_binding(value: dict) -> dict:
    """Keep immutable acquisition facts separate from the current execution fence."""
    return {**value["request"], **value.get("continuation", {})}


def _continue(conn: sqlite3.Connection, event_id: int, value: dict, binding: dict) -> None:
    conn.execute(
        "UPDATE task_events SET task_relationship=? WHERE id=?",
        (
            json.dumps(
                {**value, "continuation": binding},
                sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ),
            event_id,
        ),
    )


def continue_owner_session(
    conn: sqlite3.Connection, task: Task, owner_session_id: object, *,
    standing_succession: bool = False,
) -> None:
    """Rebind a protected resume or admitted standing succession in its transaction."""
    row = latest_relationship(conn, task.id)
    if row is None:
        return
    require_relationship(conn, task.id)
    value = json.loads(row["task_relationship"])
    allowed = (
        task.kind == STANDING_KIND and task.status == Status.STARTED
        and task.owner_session_id is not None and owner_session_id != task.owner_session_id
        and isinstance(owner_session_id, str) and bool(owner_session_id.strip())
        if standing_succession else task.status == Status.SUSPENDED
    )
    if (
        not allowed
        or not same_assignment_worker(task.owner, row["worker"])
        or current_binding(value)["reservation_key"] is not None
    ):
        raise TaskError("task relationship cannot continue a different assignment")
    _continue(conn, row["id"], value, {
        "generation": task.generation + 1,
        "owner_session_id": owner_session_id,
    })


def continue_pool_startup(
    conn: sqlite3.Connection, task: Task, reservation_key: str, *, authority: str | None,
) -> None:
    """Carry one exhausted episode only after retirement into its exact workspace."""
    row = latest_relationship(conn, task.id)
    if row is None:
        return
    value = json.loads(row["task_relationship"])
    if value["state"] in {"closed", "absent"}:
        return
    binding = current_binding(value)
    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id=?", (task.id,),
    ).fetchone()
    previous = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (binding["reservation_key"],),
    ).fetchone()
    candidate = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (reservation_key,),
    ).fetchone()
    workspace = AssignmentWorkspace(**value["request"]["workspace"])
    candidate_workspace = {
        "machine": candidate["execution_machine"],
        "project": candidate["allocation_project"],
        "worktree": candidate["worktree"],
        "path": candidate["worktree_path"],
        "creation_nonce": previous["creation_nonce"] if previous is not None else None,
    }
    if (
        assignment is None or previous is None
        or authority != value["request"]["authority"]
        or task.status != Status.QUEUED or task.owner is not None
        or task.owner_session_id is not None
        or assignment["assignment_released"] or assignment["exhausted_at"] is None
        or assignment["reservation_key"] != previous["key"]
        or assignment["task_generation"] != binding["generation"]
        or task.generation != binding["generation"] + 1
        or previous["task_id"] != task.id
        or previous["task_relationship_event_id"] != row["id"]
        or previous["task_relationship_generation"] != binding["generation"]
        or candidate["creation_key"] != previous["creation_key"]
        or candidate["allocation_revision"] != previous["allocation_revision"]
        or candidate["session_handle"] is not None
        or candidate_workspace != asdict(workspace)
    ):
        raise TaskError(
            "previous task relationship requires original-owner settlement or continuation"
        )
    from .queue_pool_placement import require_pool_recovery_retirement

    require_pool_recovery_retirement(conn, task)
    _continue(conn, row["id"], value, {
        "generation": task.generation + 1,
        "reservation_key": reservation_key,
    })
    conn.execute(
        "UPDATE spawn_reservations SET creation_nonce=?, task_relationship_event_id=? WHERE key=?",
        (workspace.creation_nonce, row["id"], reservation_key),
    )
