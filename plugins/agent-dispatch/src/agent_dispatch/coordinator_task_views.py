"""Pure task detail, bulk and event projections for coordinator routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .queue_common import Task


def _task_dict(task: Task) -> dict[str, Any]:
    from .handoff_claim_binding import CONTRACT

    return {
        **asdict(task), "steering_revision": task.steering_revision,
        "card_draft_revision": task.card_draft_revision,
        "handoff_claim_contract": CONTRACT,
    }


def _bulk_task_dict(task: Task, draft_scope: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _task_dict(task)
    if draft_scope is not None:
        result["draft_scope"] = draft_scope
    result.pop("result")
    return result


def _event_task_dict(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    result["has_result"] = result.pop("result", None) is not None or bool(
        result.get("has_result")
    )
    return result
