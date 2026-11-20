"""Narrow creator-claim reconciliation through the actual task owner."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from .coordinator_auth import _make_control_auth
from .events import EventBus
from .handoff_claim_release import reconcile_claim
from .queue import TaskQueue
from .queue_records import TaskError


class HandoffClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: dict[str, Any] | None = None
    owner_ref: str | None = None


def register_routes(app: FastAPI, queue: TaskQueue, bus: EventBus, *, control_token: str | None) -> None:
    """Keep control authorization independent of the recorded selector."""
    control = _make_control_auth(
        control_token, lambda kind, detail: bus.publish({"type": kind, **detail}),
        operation="handoff_creator_claim", rejection_event="task.handoff_claim_rejected",
        code_prefix="handoff_creator_claim",
        unavailable_message="creator reconciliation requires configured control authority",
        forbidden_message="creator reconciliation requires control authority",
    )
    @app.post(
        "/tasks/{task_id}/handoff-claim",
        dependencies=[Depends(control)],
    )
    def handoff_claim(task_id: str, body: HandoffClaimBody) -> dict[str, Any]:
        if body.binding is not None and body.owner_ref is not None:
            raise HTTPException(400, detail="binding and explicit legacy owner are separate modes")
        try:
            return reconcile_claim(queue, task_id, binding=body.binding, owner_ref=body.owner_ref)
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
