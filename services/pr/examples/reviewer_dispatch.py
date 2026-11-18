"""Inert retained-task adapter for the existing Dispatch client contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from reviewer_feed import Proposal, Scope, Task


class Client(Protocol):
    def lookup_dedup(self, dedup_key: str, *, repo: str | None) -> list[dict]: ...

    def get(self, task_id: str) -> dict: ...

    def prepare_steer(
        self, task_id: str, request_id: str, *, expected_scope: dict,
        fields: dict, sender: str | None, wake: bool, message: str,
        expected_status: str, expected_revision: str,
    ) -> dict: ...


def _scope(scope: Scope) -> dict[str, str]:
    if len(scope.task_scope) != 3 or not all(scope.task_scope):
        raise ValueError("complete configured task scope required")
    return dict(zip(("queue", "installation", "machine"), scope.task_scope))


def retained_task(
    action: dict[str, Any], scope: Scope, *, client: Client, lane: str,
    task_key: Callable[[int], str],
) -> Task:
    """Use exact owner lookup/detail, never infer scoped absence or create work."""
    expected_scope = _scope(scope)
    if not isinstance(lane, str) or not lane.strip():
        raise ValueError("configured harness lane required")
    number = action.get("pr")
    if type(number) is not int or number < 1:
        raise ValueError("positive PR number required")
    key = task_key(number)
    if not isinstance(key, str) or not key.strip():
        raise ValueError("configured recipe dedup key required")
    unknown = Task(scope.task_scope, "unknown")
    rows = client.lookup_dedup(key, repo=lane)
    # An empty exact lookup lacks the full scope attestation returned by a task.
    # It cannot establish the configured installation/machine for task creation.
    if not isinstance(rows, list) or len(rows) != 1:
        return unknown
    row = rows[0]
    if (
        not isinstance(row, dict) or row.get("repo") != lane
        or row.get("dedup_key") != key or row.get("draft_scope") != expected_scope
        or not isinstance(row.get("id"), str) or not row["id"]
    ):
        return unknown
    task = client.get(row["id"])
    if (
        not isinstance(task, dict) or task.get("id") != row["id"]
        or task.get("repo") != lane or task.get("dedup_key") != key
        or task.get("draft_scope") != expected_scope
        or not isinstance(task.get("steering_revision"), str)
        or not task["steering_revision"]
        or not isinstance(task.get("status"), str) or not task["status"]
        or type(task.get("awaiting_steer")) is not bool
        or "hold_reason" not in task
        or task.get("kind") != "finite"
    ):
        return unknown
    return Task(
        scope.task_scope, "present", task["id"], task["steering_revision"],
        task["status"], bool(task["hold_reason"]) or task["awaiting_steer"],
    )


def prepare_notification(
    proposal: Proposal, scope: Scope, *, client: Client, request_id: str,
    fields: dict, sender: str | None, message: str, lane: str,
    task_key: Callable[[int], str],
) -> dict:
    """Prepare through the native owner; the caller must retain before effects."""
    if (
        proposal.kind != "steer" or not proposal.task_id or not proposal.revision
        or not proposal.action_key or not proposal.notification_key
        or not request_id
    ):
        raise ValueError("retained-task steer proposal and original request ID required")
    task = retained_task(
        {"pr": proposal.pr}, scope, client=client, lane=lane, task_key=task_key,
    )
    if (
        task.outcome != "present" or task.task_id != proposal.task_id
        or task.revision != proposal.revision
        or task.status not in {"started", "suspended"} or task.held
    ):
        raise ValueError("task changed or is held; reconcile before preparing")
    return client.prepare_steer(
        proposal.task_id, request_id, expected_scope=_scope(scope), fields=fields,
        sender=sender, wake=True, message=message, expected_status=task.status,
        expected_revision=proposal.revision,
    )
