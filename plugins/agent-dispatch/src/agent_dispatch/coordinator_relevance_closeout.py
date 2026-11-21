"""Provider-first worker closeout, retaining normal task transition hooks."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal
from .queue_relevance_acknowledgement import finish as finish_closeout
from .review_closeout_submission import ABANDON, SUBMIT

from .queue import TaskQueue
from .queue_records import TaskError
from .task_closeout_authority import (
    CLOSEOUT_PATH,
    require_closeout_fencing,
    resolve_closeout_authority,
)
from .task_relevance_contract import CLOSEOUT_PROTOCOL


class CloseoutBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    request_id: str = Field(min_length=1, max_length=4096)
    worker_id: str = Field(min_length=1, max_length=4096)
    native_id: str = Field(min_length=1, max_length=4096)
    expected_generation: int = Field(strict=True, ge=0, le=2**63 - 1)


class CloseoutBody(CloseoutBinding):
    finish_action: Literal["submit-finalized-review/v1", "abandon-finalized-source/v1"] | None = None


def register_closeout_routes(
    app: FastAPI,
    queue: TaskQueue,
    *,
    guard: Callable[..., dict[str, Any]],
) -> None:
    """Expand the existing authenticated coordinator; no automatic caller migration."""

    def envelope(result: dict[str, Any]) -> dict[str, Any]:
        return {"protocol": CLOSEOUT_PROTOCOL, "version": 1, "result": result}

    def authorize(request: Request, task_id: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return resolve_closeout_authority(
            request,
            queue,
            app.state.task_fencing_guard,
            task_id,
            arguments,
        )

    @app.get(CLOSEOUT_PATH)
    def snapshot(task_id: str, request: Request) -> dict[str, Any]:
        current = authorize(request, task_id)
        if current.get("finish_action") and request.headers.get("X-Dispatch-Closeout-Action") != current["finish_action"]:
            raise HTTPException(409, detail="original review closeout requires a submission-aware caller")
        if request.headers.get("X-Dispatch-Closeout-Action") == SUBMIT:
            current.setdefault("finish_action", ABANDON)
        return envelope(current)

    @app.get(f"{CLOSEOUT_PATH}/observe-provisional")
    def preview_contract(task_id: str, request: Request) -> dict[str, Any]:
        authorize(request, task_id)
        return envelope({"provisional_observation_versions": [1]})

    @app.post(f"{CLOSEOUT_PATH}/observe-provisional")
    def preview(task_id: str, body: CloseoutBinding, request: Request) -> dict[str, Any]:
        from .queue_relevance_readiness import observe_provisional

        arguments = body.model_dump(exclude={"version"})
        try:
            with app.state.drain_gate.track_claim():
                if app.state.drain_gate.draining:
                    raise TaskError("provisional observation is inhibited while draining")
                authorize(request, task_id, arguments)
                return envelope(observe_provisional(
                    queue, task_id, arguments,
                    lambda: require_closeout_fencing(request, app.state.task_fencing_guard),
                ))
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    def mutate(task_id: str, body: CloseoutBody, request: Request, *, finish: bool) -> dict[str, Any]:
        arguments = body.model_dump(exclude={"version", "finish_action"})
        gate = app.state.drain_gate

        def authority_guard() -> None:
            require_closeout_fencing(request, app.state.task_fencing_guard)

        try:
            with gate.track_claim():
                if gate.draining:
                    raise TaskError("relevance closeout is inhibited while draining")
                current = authorize(request, task_id, arguments)
                if (body.finish_action or ABANDON) != current.get("finish_action", ABANDON):
                    raise TaskError("negotiated closeout action changed; custody retained")
                if finish:
                    if body.finish_action != SUBMIT:
                        result = guard(lambda: queue.finish_relevance_closeout(
                            task_id, authority_guard=authority_guard, **arguments,
                        ))
                    else:
                        result = guard(lambda: finish_closeout(
                            queue, task_id, submit_review=True, authority_guard=authority_guard, **arguments,
                        ))
                    if getattr(request.state, "task_scope", None) is not None:
                        result = {key: result[key] for key in ("id", "status", "generation")}
                else:
                    result = queue.prepare_relevance_closeout(
                        task_id, authority_guard=authority_guard, **arguments,
                    )
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return envelope(result)

    @app.post(f"{CLOSEOUT_PATH}/prepare")
    def prepare(task_id: str, body: CloseoutBody, request: Request) -> dict[str, Any]:
        return mutate(task_id, body, request, finish=False)

    @app.post(f"{CLOSEOUT_PATH}/finish")
    def finish(task_id: str, body: CloseoutBody, request: Request) -> dict[str, Any]:
        return mutate(task_id, body, request, finish=True)
