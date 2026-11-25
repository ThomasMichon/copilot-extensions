"""Original owned ordinary allocation facts, independent of task attachments."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from .bridge_remote import normalize_host
from .queue_common import Task
from .queue_pool_placement import PoolAssignment, pool_assignment
from .queue_records import SpawnReservation, Status, TaskError


def require_support(contract: dict[str, Any]) -> None:
    versions = contract.get("ordinary_allocation_nonce_versions")
    if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
        raise TaskError("coordinator lacks original ordinary allocation nonce storage")


def original_owned(row: dict[str, Any], driver: str | None, ownership: str) -> bool:
    return (
        driver == "ordinary-bridge"
        and ownership == "created"
        and row.get("allocation_reservation_key") == row["key"]
        and not row.get("task_relationship_required")
    )


def _placement(
    conn: sqlite3.Connection,
    task: Task,
    row: sqlite3.Row,
) -> PoolAssignment | None:
    assignment = pool_assignment(conn, task.id)
    cohort = json.loads(row["allocation_cohort"]) if row["allocation_cohort"] else {}
    if assignment is None and cohort.get("pool") is None:
        return None  # An accounting-only pool binding is not an allocator's claim.
    binding = conn.execute(
        "SELECT registration_id,machine,env FROM pool_reservation_bindings"
        " WHERE reservation_key=?",
        (row["key"],),
    ).fetchone()
    if (
        assignment is None
        or assignment.reservation_key != row["key"]
        or assignment.task_generation != task.generation
        or task.status != Status.CLAIMED
        or task.owner is not None
        or task.owner_session_id is not None
        or cohort.get("pool") != asdict(assignment)
        or binding is None
        or tuple(binding) != (assignment.registration_id, assignment.machine, assignment.env)
    ):
        raise TaskError("ordinary allocation requires the original current pool placement")
    return assignment


def validate_preparation(
    conn: sqlite3.Connection,
    task: Task,
    row: sqlite3.Row,
    *,
    ownership: str,
    creating_host: str | None,
    execution_machine: str | None,
    nonce: str | None,
    project: str | None,
    path: str | None,
) -> None:
    if row["driver"] != "ordinary-bridge" or row["task_relationship_required"]:
        if nonce is not None:
            raise TaskError("ordinary allocation nonce requires its original owned pool placement")
        return
    if (
        not original_owned(dict(row), row["driver"], "created")
        or ("created" not in (ownership, row["worktree_ownership"]))
        or _placement(conn, task, row) is None
    ):
        if nonce is not None or row["creation_nonce"] is not None:
            raise TaskError("ordinary allocation nonce requires its original owned pool placement")
        return
    if row["worktree"] and ownership != row["worktree_ownership"]:
        raise TaskError("ordinary allocation ownership cannot change")
    if (
        row["startup_fenced"]
        or row["release_requested"]
        or row["session_handle"]
        or row["agent_info"]
        or not creating_host
        or not execution_machine
        or normalize_host(creating_host) != normalize_host(execution_machine)
        or row["creating_host"] not in (None, creating_host)
        or not project
        or project != row["allocation_project"]
        or not path
        or not path.strip()
    ):
        raise TaskError(
            "ordinary allocation requires the original current pool placement and workspace"
        )
    if nonce is not None and (not nonce.strip() or row["creation_nonce"] not in (None, nonce)):
        raise TaskError("ordinary allocation requires its original creation nonce")


def published_nonce(
    conn: sqlite3.Connection,
    task: Task,
    row: sqlite3.Row,
    info: dict[str, Any],
    record: dict[str, Any] | None,
    *,
    resumed: bool,
) -> str | None:
    """Recheck the original owner return in the transaction publishing the native."""
    from .ordinary_pool_startup import created_workspace_record

    if not row["worktree"] and pool_assignment(conn, task.id) is not None:
        raise TaskError("ordinary pool creation requires its prepared workspace")
    if not original_owned(dict(row), row["driver"], row["worktree_ownership"]) or (
        _placement(conn, task, row) is None
    ):
        return None
    if resumed or not record:
        raise TaskError("ordinary agent differs from the original pool allocation")
    reservation = asdict(SpawnReservation._from_row(row))
    if created_workspace_record({"id": task.id}, reservation, info) != record:
        raise TaskError("ordinary agent differs from the original workspace owner return")
    nonce = record.get("creation_nonce")
    if (
        not isinstance(nonce, str)
        or not nonce.strip()
        or row["creation_nonce"] not in (None, nonce)
    ):
        raise TaskError("ordinary allocation requires its original creation nonce")
    return nonce
