"""Control-authenticated scope resolution; not a transition or send endpoint."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .coordinator_auth import _make_control_auth
from .events import EventBus
from .execution_scope_binding import resolve_execution_installation
from .loop_governance import LoopGovernance
from .queue import TaskQueue
from .queue_records import TaskError


class ExecutionScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str
    expected_producer_generation: int = Field(strict=True, gt=0)


def register_execution_scope_routes(
    app: FastAPI, queue: TaskQueue, bus: EventBus, *, control_token: str | None
) -> None:
    governance = LoopGovernance()

    def emit(event: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event, **detail})

    @app.post(
        "/execution-scopes/resolve",
        dependencies=[
            Depends(
                _make_control_auth(
                    control_token,
                    emit,
                    operation="resolve",
                    rejection_event="execution_scope.rejected",
                    code_prefix="execution_scope_control",
                    unavailable_message="scope resolution requires configured control authority",
                    forbidden_message="invalid or missing scope control authority",
                )
            )
        ],
    )
    def resolve(body: ExecutionScopeBody) -> dict[str, object]:
        try:
            installation = resolve_execution_installation(governance)
            binding = queue.bind_execution_scope(
                installation,
                repo=body.repo,
                source=body.source,
                expected_producer_generation=body.expected_producer_generation,
            )
            if resolve_execution_installation(governance) != installation:
                raise TaskError("execution installation changed during scope resolution")
        except (TaskError, ValueError) as exc:
            detail = {
                "code": "execution_scope_unavailable",
                "operation": "resolve",
                "reason": str(exc),
                "retryable": False,
            }
            emit("execution_scope.rejected", detail)
            raise HTTPException(status_code=409, detail=detail) from exc
        return {
            **asdict(binding),
            "runtime_enrolled": False,
            "acknowledged": False,
        }
