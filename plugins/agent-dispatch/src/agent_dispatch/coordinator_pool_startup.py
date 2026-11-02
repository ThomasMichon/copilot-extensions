"""Strict control-authorized routes over the existing startup transaction owner."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from .events import EventBus
from .pool_startup_contract import POOL_STARTUP_PATH, POOL_STARTUP_PROTOCOL, pool_startup_contract
from .queue import TaskError, TaskQueue
from .queue_records import WorkerAdmissionInhibited
from .queue_pool_placement import POOL_PLACEMENT_PROTOCOL, POOL_PLACEMENT_VERSION


class StartupPlacementBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=1, strict=True)
    task_id: str = Field(min_length=1)
    reserved_by: str = Field(min_length=1)
    catalog_revision: int = Field(ge=0, le=2**63 - 1, strict=True)
    driver: Literal["cli"] = "cli"
    startup_timeout: FiniteFloat = Field(gt=0, strict=True)


class StartupBindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=1, strict=True)
    reservation_key: str = Field(min_length=1)
    generation: int = Field(ge=1, le=2**63 - 2, strict=True)
    startup_revision: int = Field(ge=1, le=2**63 - 1, strict=True)


class ReachableBody(StartupBindingBody):
    native_id: str = Field(min_length=1)

class StartupOutcomeBody(StartupBindingBody):
    outcome_kind: Literal["startup_failed", "startup_rejected", "startup_unknown"]
    detail: str = Field(min_length=1)
    native_id: str | None = Field(default=None, min_length=1)


class ExhaustionBody(StartupBindingBody):
    reason: Literal["deadline", "attempts"] = "deadline"


class StartupRetryBody(ReachableBody):
    worktree: str = Field(min_length=1)
    machine: str = Field(min_length=1)
    reserved_by: str = Field(min_length=1)


class RetryCompletionBody(StartupRetryBody):
    outcome_kind: Literal["startup_succeeded", "startup_failed", "startup_rejected", "startup_unknown"]
    detail: str = Field(min_length=1)


def _envelope(result: object) -> dict[str, object]:
    return {"protocol": POOL_STARTUP_PROTOCOL, "version": 1, "result": result}


def register(
    app: FastAPI, queue: TaskQueue, bus: EventBus, control_auth: Callable,
) -> None:
    """Reuse the reservation registrar's strict control and drain owners."""
    dependencies = [Depends(control_auth)]

    @app.get(f"{POOL_STARTUP_PATH}/contract", dependencies=dependencies)
    def contract() -> dict:
        return pool_startup_contract()

    @app.post(f"{POOL_STARTUP_PATH}/place", dependencies=dependencies)
    def place(body: StartupPlacementBody, request: Request) -> dict:
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="pool placement is inhibited while draining")
            try:
                placement = queue.place_pool_task(
                    body.task_id, reserved_by=body.reserved_by,
                    catalog_revision=body.catalog_revision, driver=body.driver,
                    startup_timeout=body.startup_timeout,
                )
            except WorkerAdmissionInhibited as exc:
                raise HTTPException(409, detail=exc.detail()) from exc
            except TaskError as exc:
                raise HTTPException(409, detail={"code": "pool_claim_rejected", "message": str(exc)}) from exc
        if placement.reserved:
            bus.publish({"type": "task.claimed", "task_id": body.task_id})
            bus.publish({"type": "spawn.reserved", "reservation": asdict(placement.reservation)})
        return _envelope({
            "protocol": POOL_PLACEMENT_PROTOCOL, "version": POOL_PLACEMENT_VERSION,
            "reserved": placement.reserved, "reservation": asdict(placement.reservation),
            "assignment": asdict(placement.assignment),
            "configured_startup_timeout": body.startup_timeout if placement.reserved else None,
        })

    @app.get(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}", dependencies=dependencies)
    def snapshot(task_id: str) -> dict:
        try:
            return _envelope(queue.pool_startup_snapshot(task_id))
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    def mutate(task_id: str, body: StartupBindingBody, operation: str) -> dict:
        arguments = {
            "task_id": task_id, "generation": body.generation,
            "startup_revision": body.startup_revision,
        }
        try:
            if isinstance(body, ReachableBody):
                arguments["native_id"] = body.native_id
            elif isinstance(body, StartupOutcomeBody):
                arguments.update(
                    native_id=body.native_id, outcome_kind=body.outcome_kind, detail=body.detail,
                )
            elif isinstance(body, ExhaustionBody):
                arguments["reason"] = body.reason
            result = getattr(queue, operation)(body.reservation_key, **arguments)
        except WorkerAdmissionInhibited as exc:
            raise HTTPException(409, detail=exc.detail()) from exc
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return _envelope({
            "binding": {"task_id": task_id, **body.model_dump()},
            "observation": asdict(result) if result is not None else None,
        })

    @app.post(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}/reachable", dependencies=dependencies)
    def reachable(task_id: str, body: ReachableBody) -> dict:
        return mutate(task_id, body, "observe_pool_reachable")

    @app.post(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}/nudge", dependencies=dependencies)
    def nudge(task_id: str, body: ReachableBody) -> dict:
        return mutate(task_id, body, "take_pool_nudge")

    @app.post(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}/exhaust", dependencies=dependencies)
    def exhaust(task_id: str, body: ExhaustionBody) -> dict:
        result = mutate(task_id, body, "exhaust_pool_startup")
        bus.publish({"type": "task.queued", "task_id": task_id})
        bus.publish({"type": "spawn.release_requested", "reservation_key": body.reservation_key})
        return result

    @app.post(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}/outcome", dependencies=dependencies)
    def outcome(task_id: str, body: StartupOutcomeBody) -> dict:
        return mutate(task_id, body, "record_pool_startup_outcome")

    def retry_mutate(task_id: str, body: StartupRetryBody, action: str) -> dict:
        from .queue_pool_startup_retry import record_retry
        from .pool_startup_retry_contract import retry_invocation

        binding = {"task_id": task_id, **body.model_dump(), "action": action}
        try:
            result = record_retry(queue, body.reservation_key, binding)
        except WorkerAdmissionInhibited as exc:
            raise HTTPException(409, detail=exc.detail()) from exc
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        invocation = retry_invocation(
            {name: value for name, value in binding.items() if name not in {"action", "outcome_kind", "detail"}},
            body.startup_revision + int(action == "retry"),
        )
        bus.publish({"type": f"spawn.startup_{action}", "reservation_key": body.reservation_key})
        return _envelope({"binding": binding, "reservation": asdict(result), "invocation": invocation})

    @app.post(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}/retry", dependencies=dependencies)
    def retry(task_id: str, body: StartupRetryBody, request: Request) -> dict:
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="pool retry is inhibited while draining")
            return retry_mutate(task_id, body, "retry")

    @app.post(f"{POOL_STARTUP_PATH}/tasks/{{task_id}}/complete", dependencies=dependencies)
    def complete(task_id: str, body: RetryCompletionBody) -> dict:
        return retry_mutate(task_id, body, "complete")
