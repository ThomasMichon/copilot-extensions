"""Worker-authenticated decline preparation, separate from workspace finalization."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .events import EventBus
from .queue import TaskError, TaskQueue

POOL_DECLINE_PROTOCOL = "pool_worker_decline/v1"


def enrich_task(queue: TaskQueue, task_id: str, result: dict) -> dict:
    decline = queue.pool_worker_decline(task_id)
    if decline is not None:
        result["worker_decline"] = asdict(decline)
    return result


class DeclineBindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=1, strict=True)
    worker_id: str = Field(min_length=1)
    native_id: str = Field(min_length=1)
    generation: int = Field(ge=1, le=2**63 - 2, strict=True)
    startup_revision: int = Field(ge=1, le=2**63 - 1, strict=True)
    reservation_key: str = Field(min_length=1)


class PrepareDeclineBody(DeclineBindingBody):
    exclusions: list[str] = Field(min_length=1, max_length=3)
    note: str = Field(min_length=1, max_length=4096)


class DeclineBlockerBody(DeclineBindingBody):
    detail: str = Field(min_length=1, max_length=4096)


def register(app: FastAPI, queue: TaskQueue, bus: EventBus) -> None:
    # These routes inherit coordinator data authentication like explicit start.
    # Task-only credentials are not admitted to this pre-acceptance operation.
    @app.post("/tasks/{task_id}/pool-decline")
    def prepare(task_id: str, body: PrepareDeclineBody) -> dict:
        try:
            result = queue.prepare_pool_decline(
                task_id, **body.model_dump(exclude={"version"}),
            )
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "task.worker_decline_pending", "task_id": task_id})
        return {"protocol": POOL_DECLINE_PROTOCOL, "version": 1, "decline": asdict(result)}

    @app.post("/tasks/{task_id}/pool-decline/blocker")
    def blocker(task_id: str, body: DeclineBlockerBody) -> dict:
        try:
            result = queue.record_pool_decline_blocker(
                task_id, **body.model_dump(exclude={"version"}),
            )
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "task.worker_decline_blocked", "task_id": task_id})
        return {"protocol": POOL_DECLINE_PROTOCOL, "version": 1, "decline": asdict(result)}
