"""Provider-first worker closeout, retaining normal task transition hooks."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .queue import TaskError, TaskQueue
from .task_closeout_authority import CLOSEOUT_PATH, resolve_closeout_authority
from .task_relevance_contract import CLOSEOUT_PROTOCOL


class CloseoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    request_id: str = Field(min_length=1, max_length=4096)
    worker_id: str = Field(min_length=1, max_length=4096)
    native_id: str = Field(min_length=1, max_length=4096)
    expected_generation: int = Field(strict=True, ge=0, le=2**63 - 1)


def register_closeout_routes(
    app: FastAPI,
    queue: TaskQueue,
    *,
    guard: Callable[..., dict],
) -> None:
    """Expand the existing authenticated coordinator; no automatic caller migration."""

    def envelope(result: dict) -> dict:
        return {"protocol": CLOSEOUT_PROTOCOL, "version": 1, "result": result}

    def authorize(request: Request, task_id: str, arguments: dict | None = None) -> dict:
        return resolve_closeout_authority(
            request,
            queue,
            app.state.task_fencing_guard,
            task_id,
            arguments,
        )

    @app.get(CLOSEOUT_PATH)
    def snapshot(task_id: str, request: Request) -> dict:
        return envelope(authorize(request, task_id))

    def mutate(task_id: str, body: CloseoutBody, request: Request, *, finish: bool) -> dict:
        arguments = body.model_dump(exclude={"version"})
        gate = app.state.drain_gate
        try:
            with gate.track_claim():
                if gate.draining:
                    raise TaskError("relevance closeout is inhibited while draining")
                authorize(request, task_id, arguments)
                if finish:
                    result = guard(lambda: queue.finish_relevance_closeout(task_id, **arguments))
                else:
                    result = queue.prepare_relevance_closeout(task_id, **arguments)
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return envelope(result)

    @app.post(f"{CLOSEOUT_PATH}/prepare")
    def prepare(task_id: str, body: CloseoutBody, request: Request) -> dict:
        return mutate(task_id, body, request, finish=False)

    @app.post(f"{CLOSEOUT_PATH}/finish")
    def finish(task_id: str, body: CloseoutBody, request: Request) -> dict:
        return mutate(task_id, body, request, finish=True)
