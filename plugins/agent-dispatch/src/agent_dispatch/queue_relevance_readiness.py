"""Prepare exact claim settlement without releasing the original native body."""

from __future__ import annotations

from collections.abc import Callable
import json
import sqlite3
from typing import TYPE_CHECKING

from .assignment_workspace import AssignmentWorkspace, same_assignment_worker
from .queue_records import SpawnState, Status, TaskError
from .queue_relevance_delivery import closeout_owner_matches
from .queue_task_relationships import _encoded, _matches_receipt

if TYPE_CHECKING:
    from .queue import TaskQueue


def _state(queue: TaskQueue, conn: sqlite3.Connection, task_id: str, arguments: dict) -> dict:
    from .ordinary_native_bindings import reservation_native_identity
    from .queue_relationship_continuation import current_binding

    task = queue._fetch(conn, task_id)
    row = conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
    ).fetchone()
    if (
        row is None or task is None or type(arguments["expected_generation"]) is not int
        or arguments != {
            "request_id": row["request_id"], "worker_id": row["owner"],
            "native_id": row["owner_session_id"], "expected_generation": row["generation"],
        }
        or not row["resource_binding"] or not closeout_owner_matches(task, row)
        or any(getattr(task, key) is not None for key in ("hold_reason", "hold_actor", "hold_at"))
    ):
        raise TaskError("relevance closeout owner changed or is held; custody retained")
    binding = json.loads(row["resource_binding"])
    if binding.get("state") != "captured" or not binding.get("reservation_key"):
        raise TaskError("relevance closeout lacks its captured managed allocation")
    event = conn.execute(
        "SELECT * FROM task_events WHERE task_id=? AND id=?",
        (task_id, binding["assignment_event_id"]),
    ).fetchone()
    relationship = (
        json.loads(event["task_relationship"]) if event and event["task_relationship"] else None
    )
    reservation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (binding["reservation_key"],),
    ).fetchone()
    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id=?", (task_id,),
    ).fetchone()
    identity = conn.execute(
        "SELECT identity FROM workspace_queue_identity WHERE singleton=1",
    ).fetchone()["identity"]
    if relationship is None or reservation is None or assignment is None:
        raise TaskError("relevance closeout original resources are unavailable")
    request = relationship["request"]
    workspace = AssignmentWorkspace.from_dict(request["workspace"])
    current = current_binding(relationship)
    before_start = (
        binding.get("owner_phase") == "before_start" and task.status == Status.CLAIMED
        and task.owner is None and task.owner_session_id is None
    )
    if (
        binding["queue_identity"] != identity
        or request["request_id"] != binding["relationship_request_id"]
        or request["event_id"] != binding["assignment_event_id"]
        or request["workspace"] != binding["workspace"]
        or not same_assignment_worker(workspace.worker_id, row["owner"])
        or not same_assignment_worker(event["worker"], row["owner"])
        or request["task_id"] != task_id or request["recovery"]["queue_identity"] != identity
        or relationship["state"] not in {"bound", "closed"}
        or not _matches_receipt(relationship["receipt"], request, phase=relationship["state"])
        or current["generation"] != row["generation"]
        or (
            current.get("owner_session_id", row["owner_session_id"]) != row["owner_session_id"]
            and not (before_start and current.get("owner_session_id") is None)
        )
        or current["reservation_key"] != reservation["key"]
        or reservation["task_id"] != task_id
        or reservation["startup_revision"] != binding["startup_revision"]
        or not reservation["startup_complete"] or reservation["startup_fenced"]
        or reservation["state"] != SpawnState.SPAWNED or reservation["release_requested"]
        or reservation["conversation_retired"] or reservation["cooling_token"] is not None
        or reservation["native_release_pending"]
        or reservation["conclusion_state"] not in (None, "")
        or reservation["task_relationship_event_id"] != event["id"]
        or reservation["task_relationship_generation"] != row["generation"]
        or not reservation["task_relationship_required"]
        or reservation_native_identity(conn, reservation) != row["owner_session_id"]
        or assignment["assignment_released"] or assignment["exhausted_at"] is not None
        or assignment["task_generation"] != row["generation"]
        or assignment["reservation_key"] != reservation["key"]
    ):
        raise TaskError("relevance closeout resource incarnation changed; custody retained")
    from .ordinary_native_bindings import native_bindings

    _, retired = native_bindings(
        conn, reservation, task, require_owned_workspace=False,
        reject_pending_native=row["owner_session_id"],
    )
    if row["owner_session_id"] in retired:
        raise TaskError("relevance closeout native is retired; custody retained")
    original_key = reservation["allocation_reservation_key"] or reservation["key"]
    allocation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (original_key,),
    ).fetchone()
    if (
        allocation is None or allocation["task_id"] != task_id
        or request["reservation_key"] != original_key
        or allocation["worktree_ownership"] != "created"
        or allocation["driver"] not in {"cli", "ordinary-bridge"}
        or not allocation["reserved_by"]
        or any(allocation[column] != request["workspace"][key] for column, key in (
            ("execution_machine", "machine"), ("allocation_project", "project"),
            ("worktree", "worktree"), ("worktree_path", "path"),
            ("creation_nonce", "creation_nonce"),
        ))
        or any(reservation[column] != request["workspace"][key] for column, key in (
            ("execution_machine", "machine"), ("allocation_project", "project"),
            ("worktree", "worktree"), ("worktree_path", "path"),
            ("creation_nonce", "creation_nonce"),
        ))
    ):
        raise TaskError("relevance closeout original allocation changed; custody retained")
    return {
        "arguments": arguments, "binding": binding, "relationship": relationship,
        "assignment_worker": event["worker"],
        "reservation": dict(reservation), "assignment": dict(assignment),
        "allocation": dict(allocation), "status": task.status,
    }


def prepare(
    queue: TaskQueue, task_id: str, *,
    authority_guard: Callable[[], None] | None = None, **arguments: str | int,
) -> dict:
    """Internal provider only: obtain readiness, commit it, then settle the exact claim."""
    if authority_guard is not None:
        authority_guard()
    with queue._connect() as conn:
        conn.execute("BEGIN")
        original = _state(queue, conn, task_id, arguments)
        row = conn.execute(
            "SELECT readiness FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
        ).fetchone()
        receipt = json.loads(row["readiness"]) if row["readiness"] is not None else None
    if receipt is None:
        if original["relationship"]["state"] != "bound":
            raise TaskError("relevance closeout lacks its original bound claim")
        from .submission_readiness_observation import observe

        allocation, request = original["allocation"], original["relationship"]["request"]
        observation = observe(
            {"workspace": request["workspace"], "task_id": task_id,
             "owner_session_id": arguments["native_id"]},
            {"task_id": task_id, "reservation_key": allocation["key"],
             "attempt": allocation["attempt"], "driver": allocation["driver"],
             "supervisor": allocation["reserved_by"],
             "creator_machine": allocation["execution_machine"], "ownership": "created"},
            original["relationship"]["receipt"]["ref"],
        )
        receipt = {
            "task_id": task_id, "arguments": arguments, "binding": original["binding"],
            "observation": observation, "finalized": False, "process_retirement": False,
        }
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if authority_guard is not None:
                authority_guard()
            if _encoded(_state(queue, conn, task_id, arguments)) != _encoded(original):
                raise TaskError("relevance closeout changed during readiness observation")
            prior = conn.execute(
                "SELECT readiness FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
            ).fetchone()["readiness"]
            if prior is None:
                conn.execute(
                    "UPDATE task_relevance_closeouts SET readiness=? WHERE task_id=?",
                    (_encoded(receipt), task_id),
                )
            else:
                receipt = json.loads(prior)
    if receipt["arguments"] != arguments or receipt["binding"] != original["binding"]:
        raise TaskError("relevance closeout readiness belongs to another incarnation")
    from .queue_pool_decline_closeout import _settle

    return {"receipt": receipt, "relationship": _settle(queue, task_id, {
        "task_relationship_event_id": original["binding"]["assignment_event_id"],
    }, authority_guard=authority_guard)}


def release_evidence(queue: TaskQueue, conn: sqlite3.Connection, raw: str) -> dict | None:
    """Revalidate persisted readiness in the same snapshot as the obligation read."""
    receipt = json.loads(raw)
    try:
        state = _state(queue, conn, receipt["task_id"], receipt["arguments"])
    except TaskError:
        return None
    if receipt["binding"] != state["binding"]:
        return None
    request = state["relationship"]["request"]
    return {
        "request_id": receipt["arguments"]["request_id"],
        "generation": receipt["arguments"]["expected_generation"],
        "assignment_event_id": request["event_id"],
        "relationship_request_id": request["request_id"],
        "worker_id": state["assignment_worker"],
        "native_id": receipt["arguments"]["native_id"],
    }
