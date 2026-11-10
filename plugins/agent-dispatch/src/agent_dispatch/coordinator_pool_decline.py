"""Worker-authenticated decline preparation, separate from workspace finalization."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .events import EventBus
from .queue import TaskError, TaskQueue
from .queue_pool_decline import PoolDeclineCloseout

POOL_DECLINE_PROTOCOL = "pool_worker_decline/v1"


def enrich_task(queue: TaskQueue, task_id: str, result: dict) -> dict:
    assignment = queue.task_pool_assignment(task_id)
    if assignment is not None:
        result["pool_assignment"] = asdict(assignment)
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


def register(app: FastAPI, queue: TaskQueue, bus: EventBus, control_auth: Callable) -> None:
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

    @app.get("/tasks/{task_id}/pool-decline/recovery")
    def recovery(
        task_id: str, worker_id: str = Query(min_length=1), native_id: str = Query(min_length=1),
        generation: int = Query(ge=1, le=2**63 - 2),
    ) -> dict:
        # Read-only: returns a binding only behind its committed closeout receipt.
        found = queue.recover_pool_decline(
            task_id, worker_id=worker_id, native_id=native_id, generation=generation,
        )
        if found is None:
            raise HTTPException(404, detail="no settled worker decline for this exact incarnation")
        decline, worktree = found
        return {
            "protocol": POOL_DECLINE_PROTOCOL, "version": 1,
            "decline": asdict(decline), "worktree": worktree,
        }

    @app.post("/tasks/{task_id}/pool-decline/closeout", dependencies=[Depends(control_auth)])
    def closeout(task_id: str, body: DeclineBindingBody, request: Request) -> dict:
        arguments = body.model_dump(exclude={"version"})
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="worker decline closeout is inhibited while draining")
            try:
                result = queue.close_pool_decline(task_id, **arguments)
            except TaskError as exc:
                try:
                    queue.record_pool_decline_blocker(task_id, **arguments, detail=str(exc)[:4096])
                except TaskError:
                    pass
                raise HTTPException(409, detail=str(exc)) from exc
        if result.applied:
            bus.publish({"type": "task.queued", "task_id": task_id})
            bus.publish({"type": "spawn.release_requested", "reservation_key": body.reservation_key})
        return _closeout_response(result)

    @app.post("/tasks/{task_id}/pool-decline/settlement")
    def settlement(task_id: str, body: DeclineBindingBody, request: Request) -> dict:
        # Bound to an already committed disposition; the queue proves retirement itself.
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="worker decline settlement is inhibited while draining")
            try:
                result = queue.settle_pool_decline(task_id, **body.model_dump(exclude={"version"}))
            except TaskError as exc:
                raise HTTPException(409, detail=str(exc)) from exc
        return _closeout_response(result)


def _closeout_response(result: PoolDeclineCloseout) -> dict:
    response = {"protocol": POOL_DECLINE_PROTOCOL, "version": 1, "closeout": result.receipt}
    if result.relationship is not None:
        response["task_relationship"] = result.relationship
    return response
