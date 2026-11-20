"""Verify the executing decline and its distinct workspace creation stamp."""

from __future__ import annotations

from typing import Any

from .assignment_workspace import same_assignment_worker
from .client_errors import DispatchError
from .queue_records import TaskError
from .queue_task_relationships import _matches_receipt
from .task_relationships import AssignmentSubject


def require_bound_receipt(value: dict[str, Any], task: str, event_id: object) -> None:
    request = value["request"]
    try:
        if (
            request.get("task_id") == task
            and type(event_id) is int
            and type(request.get("event_id")) is int
            and request["event_id"] == event_id
            and _matches_receipt(value.get("receipt"), request, phase="bound")
        ):
            return
    except (TaskError, TypeError, KeyError, ValueError):
        pass
    raise DispatchError(
        409, "worker decline lacks its original bound task relationship receipt; custody retained"
    )


def require_continuation(
    task: str,
    key: str,
    generation: int | None,
    execution: dict[str, Any] | None,
    relationship: dict[str, Any],
    prepared: object,
) -> None:
    request = relationship["request"]
    if execution is not None and (
        execution.get("driver") == "ordinary-bridge"
        and execution.get("task_id") == task
        and execution.get("key") == key
        and execution.get("task_relationship_required") is True
        and execution.get("worktree_ownership") == "reused"
        and execution.get("inherited_worktree") == execution.get("worktree")
        and request.get("task_id") == task
        and isinstance(prepared, dict)
        and prepared.get("key") == request.get("reservation_key")
        and prepared["key"] != key
        and prepared.get("worktree_ownership") == "created"
        and prepared.get("release_requested") is True
        and prepared.get("state") in {"settled", "failed", "rearmed"}
        and prepared.get("native_release_pending") is False
        and isinstance(execution.get("allocation_reservation_key"), str)
        and execution.get("creation_key")
        and execution.get("worktree_writer_requirement")
        and all(
            prepared.get(k) == execution.get(k)
            for k in (
                "task_id",
                "driver",
                "allocation_reservation_key",
                "creation_key",
                "allocation_revision",
                "reserved_by",
                "task_relationship_event_id",
                "task_relationship_required",
                "creation_nonce",
                "worktree_writer_requirement",
                "worktree",
                "worktree_path",
                "execution_machine",
                "allocation_project",
            )
        )
        and type(request.get("event_id")) is int
        and request["event_id"] == execution.get("task_relationship_event_id")
        and type(generation) is int
        and type(request.get("generation")) is int
        and request["generation"] < generation
        and relationship.get("continuation") == {"generation": generation, "reservation_key": key}
        and type(relationship["continuation"]["generation"]) is int
        and request.get("workspace")
        == {
            "machine": execution.get("execution_machine"),
            "project": execution.get("allocation_project"),
            "worktree": execution.get("worktree"),
            "path": execution.get("worktree_path"),
            "creation_nonce": execution.get("creation_nonce"),
        }
    ):
        return
    raise DispatchError(
        409, "worker decline lacks its original task relationship continuation; custody retained"
    )


def finalize_binding(
    task: str,
    decline: dict[str, Any],
    worktree: str,
    receipt: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    root = execution.get("allocation_reservation_key")
    nonce = execution.get("creation_nonce")
    observation = receipt.get("observation")
    allocation = observation.get("allocation") if isinstance(observation, dict) else None
    if not (
        execution.get("key") == decline["reservation_key"]
        and execution.get("task_id") == task
        and execution.get("worktree") == worktree
        and execution.get("task_relationship_required") is True
        and type(execution.get("task_relationship_event_id")) is int
        and type(receipt.get("task_relationship_event_id")) is int
        and receipt.get("task_relationship_event_id") == execution["task_relationship_event_id"]
        and isinstance(root, str)
        and root
        and isinstance(nonce, str)
        and nonce
        and isinstance(observation, dict)
        and observation.get("ready") is True
        and observation.get("checkout_retained") is True
        and observation.get("finalized") is False
        and observation.get("task_release") is False
        and observation.get("process_retirement") is False
        and observation.get("operation") == "observe-ready"
        and observation.get("task_id") == task
        and observation.get("reservation_key") == root
        and observation.get("native_id") == decline["native_id"]
        and observation.get("worktree_id") == worktree
        and observation.get("creation_nonce") == nonce
        and observation.get("path") == execution.get("worktree_path")
        and observation.get("project") == execution.get("allocation_project")
        and isinstance(allocation, dict)
        and allocation.get("reservation_key") == root
        and allocation.get("task_id") == task
        and allocation.get("ownership") == "created"
        and allocation.get("driver") == execution.get("driver")
        and allocation.get("supervisor") == execution.get("reserved_by")
        and same_assignment_worker(
            f"{allocation.get('creator_machine')}/{worktree}", decline["worker_id"]
        )
    ):
        raise DispatchError(
            502, "worker decline lacks confirmed allocation readiness; custody retained"
        )
    ref = observation.get("task_claim_ref")
    namespace, _, bare = ref.partition(":") if isinstance(ref, str) else ("", "", "")
    authority = {"dispatch-local-task": "local", "dispatch-central-task": "central"}.get(namespace)
    if authority is None:
        raise DispatchError(502, "worker decline readiness lacks its task claim; custody retained")
    subject = AssignmentSubject.decode(bare, authority=authority)
    if (
        subject.task_id != task
        or subject.assignment_event_id != execution["task_relationship_event_id"]
        or not same_assignment_worker(subject.worker_id, decline["worker_id"])
    ):
        raise DispatchError(
            502, "worker decline readiness names another assignment; custody retained"
        )
    return {
        "task_id": task,
        "native_id": decline["native_id"],
        "reservation_key": root,
        "creation_nonce": nonce,
    }
