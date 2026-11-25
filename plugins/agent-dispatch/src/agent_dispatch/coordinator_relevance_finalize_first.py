"""Control-authorized supervisor finalize-first settlement for obsolete tasks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .queue import TaskQueue
from .queue_records import TaskError
from .queue_common import CompletionOutcome
from .task_relevance_contract import FINALIZE_FIRST_PATH, FINALIZE_FIRST_PROTOCOL, RELEVANCE_PATH


class FenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    supervisor_id: str = Field(min_length=1, max_length=4096)
    generation: int = Field(strict=True, ge=0, le=2**63 - 1)
    reservation_key: str = Field(min_length=1, max_length=4096)


class SettleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    token: str = Field(min_length=1, max_length=256)
    evidence: dict[str, Any]


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    token: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=4096)


def envelope(result: object) -> dict[str, Any]:
    return {"protocol": FINALIZE_FIRST_PROTOCOL, "version": 1, "result": result}


def register_finalize_first_routes(
    app: FastAPI, queue: TaskQueue, *, dependencies: list[Any],
    transition: Callable[[CompletionOutcome], None],
) -> None:
    base = f"{RELEVANCE_PATH}/tasks/{{task_id}}/finalize-first"

    @app.get(FINALIZE_FIRST_PATH, dependencies=dependencies)
    def candidates(supervisor_id: str = Query(min_length=1, max_length=4096)) -> dict[str, Any]:
        return envelope(queue.relevance_finalize_candidates(supervisor_id))

    @app.post(f"{base}/fence", dependencies=dependencies)
    def fence(task_id: str, body: FenceBody, request: Request) -> dict[str, Any]:
        gate = request.app.state.drain_gate
        try:
            with gate.track_claim():
                if gate.draining:
                    raise TaskError("relevance finalize-first is inhibited while draining")
                return envelope(queue.fence_relevance_finalize(
                    task_id, supervisor_id=body.supervisor_id, generation=body.generation,
                    reservation_key=body.reservation_key,
                ))
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post(f"{base}/settle", dependencies=dependencies)
    def settle(task_id: str, body: SettleBody) -> dict[str, Any]:
        try:
            outcome = queue.settle_relevance_finalize(task_id, token=body.token, evidence=body.evidence)
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        transition(outcome)
        return envelope({"task_id": task_id, "status": outcome.task.status})

    @app.post(f"{base}/release", dependencies=dependencies)
    def release(task_id: str, body: ReleaseBody) -> dict[str, Any]:
        try:
            return envelope(queue.release_relevance_finalize(task_id, token=body.token, detail=body.detail))
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
