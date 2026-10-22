"""Control-only enrollment of this coordinator, never a caller-selected process."""

from __future__ import annotations

import os
import uuid

from fastapi import Depends, FastAPI, HTTPException

from .companion import process_start_token
from .coordinator_auth import _make_control_auth
from .coordinator_execution_scopes import ExecutionScopeBody
from .events import EventBus
from .execution_scope_binding import resolve_execution_installation
from .loop_governance import LoopGovernance
from .queue import TaskQueue
from .queue_execution_runtimes import CoordinatorExecutionRuntime
from .queue_records import TaskError


def _own_process(incarnation: str) -> CoordinatorExecutionRuntime:
    pid = os.getpid()
    token = process_start_token(pid)
    if token is None:
        raise TaskError("coordinator process start identity is unavailable")
    return CoordinatorExecutionRuntime(incarnation, pid, token)


def coordinator_incarnation(app: FastAPI) -> str:
    """Share one server-created incarnation across this application's senders."""
    if not hasattr(app.state, "execution_incarnation"):
        app.state.execution_incarnation = uuid.uuid4().hex
    return app.state.execution_incarnation


def register_execution_runtime_routes(
    app: FastAPI, queue: TaskQueue, bus: EventBus, *, control_token: str | None
) -> None:
    governance = LoopGovernance()
    incarnation = coordinator_incarnation(app)

    def emit(event: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event, **detail})

    @app.post(
        "/execution-scopes/enroll-coordinator",
        dependencies=[
            Depends(
                _make_control_auth(
                    control_token,
                    emit,
                    operation="enroll-coordinator",
                    rejection_event="execution_runtime.rejected",
                    code_prefix="execution_runtime_control",
                    unavailable_message="runtime enrollment requires configured control authority",
                    forbidden_message="invalid or missing runtime control authority",
                )
            )
        ],
    )
    def enroll(body: ExecutionScopeBody) -> dict[str, object]:
        try:
            installation = resolve_execution_installation(governance)
            runtime = _own_process(incarnation)
            binding = queue.bind_execution_scope(
                installation,
                repo=body.repo,
                source=body.source,
                expected_producer_generation=body.expected_producer_generation,
            )
            result = queue.enroll_coordinator_execution_runtime(binding, installation, runtime)
            if (
                resolve_execution_installation(governance) != installation
                or _own_process(incarnation) != runtime
            ):
                raise TaskError("coordinator runtime provenance changed during enrollment")
        except (TaskError, ValueError, OSError) as exc:
            detail = {
                "code": "execution_runtime_unavailable",
                "operation": "enroll-coordinator",
                "reason": str(exc),
                "retryable": False,
            }
            emit("execution_runtime.rejected", detail)
            raise HTTPException(status_code=409, detail=detail) from exc
        return result
