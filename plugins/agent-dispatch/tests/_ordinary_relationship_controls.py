"""Exact assignment proof helpers for queue integration tests."""

from typing import Any

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_relevance import RelevanceScope
from agent_dispatch.task_obligations import read_snapshot


def report(queue: TaskQueue, task_id: str, *, scope: RelevanceScope) -> dict[str, Any]:
    current = queue.relevance_snapshot(task_id)
    return queue.reconcile_task_relevance(
        task_id,
        request_id="closed-source",
        scope=scope,
        expected_generation=current["generation"],
        expected_revision=current["revision"],
        verdict="obsolete",
        reason="Backing object is closed",
        observation_ref="provider-object:closed:revision-2",
        reporter="source-evaluator",
    )


def relationship(queue: TaskQueue, task_id: str) -> dict[str, Any]:
    value = queue.task_relationship(task_id)
    assert value is not None
    return value


def snapshot_for(queue: TaskQueue, task_id: str) -> dict[str, Any]:
    value = read_snapshot(queue, task_id)
    assert value is not None
    return value


def claim(queue: TaskQueue, workspace: AssignmentWorkspace, task: Task) -> Task:
    claimed = queue.claim_one(workspace.worker_id, task_id=task.id, assignment_workspace=workspace)
    assert isinstance(claimed, Task)
    return claimed
