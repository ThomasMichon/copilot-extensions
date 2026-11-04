"""Pure task detail, bulk and event projections for coordinator routes."""

from __future__ import annotations

from dataclasses import asdict

from .queue_common import Task


def _task_dict(task: Task) -> dict:
    return {
        **asdict(task), "steering_revision": task.steering_revision,
        "card_draft_revision": task.card_draft_revision,
    }


def _bulk_task_dict(task: Task, draft_scope: dict | None = None) -> dict:
    result = _task_dict(task)
    if draft_scope is not None:
        result["draft_scope"] = draft_scope
    result.pop("result")
    return result


def _event_task_dict(task: dict) -> dict:
    result = dict(task)
    result["has_result"] = result.pop("result", None) is not None or bool(
        result.get("has_result")
    )
    return result
