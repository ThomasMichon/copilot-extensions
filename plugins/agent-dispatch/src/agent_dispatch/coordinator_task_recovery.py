"""Operator recovery routes, separate from worker-scoped lifecycle authority."""

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .coordinator_auth import _make_control_auth
from .events import EventBus
from .queue import TaskError, TaskQueue

RECOVERY_CONTRACT = {
    "protocol": "task_recovery/v1",
    "operations": ["detach_agent", "reset_proposed", "reset_queued", "reconcile_wake"],
}


class RecoveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(strict=True, min_length=1, max_length=128)
    request_id: str = Field(strict=True, min_length=1, max_length=256)
    actor: str = Field(strict=True, min_length=1, max_length=256)
    reason: str = Field(strict=True, min_length=1, max_length=4096)


class ReconcileWakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outbox: Literal["wake_outbox", "run_waiter_wakes"]
    wake_id: str = Field(strict=True, min_length=1, max_length=1024)
    expected_revision: str = Field(strict=True, min_length=1, max_length=128)
    actor: str = Field(strict=True, min_length=1, max_length=256)
    reason: str = Field(strict=True, min_length=1, max_length=4096)


def register_task_recovery_routes(
    app: FastAPI, queue: TaskQueue, bus: EventBus, *, control_token: str | None,
) -> None:
    control = _make_control_auth(
        control_token, lambda kind, detail: bus.publish({"type": kind, **detail}),
        operation="task_recovery", rejection_event="task.recovery_rejected",
        code_prefix="task_recovery",
        unavailable_message="task recovery requires configured control authority",
        forbidden_message="task recovery requires control authority",
    )

    @app.get("/tasks/{task_id}/diagnostics")
    def diagnostics(task_id: str) -> dict:
        try:
            return {
                **queue.task_diagnostics(task_id),
                "authority": {
                    "recovery_control_configured": bool(control_token),
                    "required": "coordinator control authority",
                },
            }
        except TaskError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/recovery/{operation}", dependencies=[Depends(control)])
    def recover(
        task_id: str, operation: Literal["detach_agent", "reset_proposed", "reset_queued"],
        body: RecoveryBody,
    ) -> dict:
        try:
            result = queue.recover_task(task_id, operation=operation, **body.model_dump())
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        if not result["replayed"]:
            bus.publish({"type": "task.recovered", **result})
        return result

    @app.post("/tasks/{task_id}/wakes/reconcile", dependencies=[Depends(control)])
    def reconcile(task_id: str, body: ReconcileWakeBody) -> dict:
        from .task_wake_recovery import reconcile_wake
        try:
            result = reconcile_wake(queue, task_id, **body.model_dump())
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        if not result["replayed"]:
            bus.publish({"type": "task.wake_reconciled", **result})
        return result
