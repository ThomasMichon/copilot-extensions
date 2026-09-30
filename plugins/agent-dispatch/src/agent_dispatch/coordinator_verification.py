"""Verification, event-note, and detached-run waiter routes."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, StrictInt

from . import remote_dispatch
from .coordinator_auth import scoped_control_token
from .events import EventBus
from .queue import RegistrationKind, Status, Task, TaskError, TaskQueue
from .verification import evaluate_submitted_task


class EventNoteBody(BaseModel):
    sender: str
    note: str


class RunWaiterRegisterBody(BaseModel):
    pid: StrictInt
    host: str | None = None
    start_token: str | None = None
    resume_worktree: str
    command: list[str] = Field(default_factory=list)


class RunWaiterFinishBody(BaseModel):
    generation: StrictInt
    pid: StrictInt
    host: str | None = None
    start_token: str | None = None
    message: str


def register_verification_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
    *,
    control_token: str | None,
    task_dict,
    event_task_dict,
) -> None:
    def _wake_for_event_note(task: Task, note: str) -> bool:
        message = (
            f"Task {task.id} received an event note: {note}. "
            "Re-read the task history and handle the new external state before finalizing."
        )
        waiter = queue.supersede_run_waiter_with_wake(
            task.id,
            reason="event note wake",
            message=message,
            sender="agent-dispatch-event-note",
        )
        return waiter is not None

    def _emit_producer_event(event_type: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event_type, "producer_fence": detail})

    current_machine = remote_dispatch.local_machine()
    current_env = os.environ.get("AGENT_DISPATCH_ENV") or "default"
    emitter_bearer = HTTPBearer(auto_error=False)

    def _require_trusted_emitter(task: Task, sender: str) -> None:
        record = queue.get_registration(sender)
        if record is None or record.kind != RegistrationKind.EMITTER:
            raise HTTPException(status_code=403, detail="event note sender is not a registered emitter")
        if record.status != "active":
            raise HTTPException(status_code=403, detail="event note sender is not active")
        if record.machine not in (None, current_machine):
            raise HTTPException(status_code=403, detail="event note sender belongs to a different machine")
        if str(record.env or "default") != current_env:
            raise HTTPException(status_code=403, detail="event note sender belongs to a different environment")
        spec = record.spec or {}
        if not (spec.get("all_repos") or spec.get("repo") == task.repo):
            raise HTTPException(status_code=403, detail="event note sender is not registered for this task's repo")

    @app.post("/tasks/{task_id}/verify-submitted")
    def verify_submitted(task_id: str) -> dict:
        try:
            return evaluate_submitted_task(queue, task_id, bus=bus, trigger="backfill")
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/event-note")
    def append_event_note(
        task_id: str,
        body: EventNoteBody,
        creds: HTTPAuthorizationCredentials | None = Depends(emitter_bearer),  # noqa: B008
    ) -> dict:
        if control_token is None:
            _emit_producer_event(
                "producer_scope.transition_rejected",
                {
                    "code": "producer_control_unavailable",
                    "operation": "event_note",
                    "reason": "control_authority_not_configured",
                    "retryable": False,
                },
            )
            raise HTTPException(
                status_code=503,
                detail="managed producer transitions require a configured control token",
            )
        expected = scoped_control_token(control_token, f"event-note:{body.sender}")
        if creds is None or not secrets.compare_digest(creds.credentials, expected):
            _emit_producer_event(
                "producer_scope.transition_rejected",
                {
                    "code": "producer_control_forbidden",
                    "operation": "event_note",
                    "reason": "invalid_control_authority",
                    "retryable": False,
                },
            )
            raise HTTPException(status_code=403, detail="invalid or missing producer control bearer")
        task = queue.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"no such task {task_id!r}")
        _require_trusted_emitter(task, body.sender)
        try:
            task, event_id = queue.append_event_note(task_id, sender=body.sender, note=body.note)
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        result = task_dict(task)
        bus.publish(
            {
                "type": "task.event_note",
                "task": event_task_dict(result),
                "sender": body.sender,
                "note": body.note,
            }
        )
        if task.status == Status.SUBMITTED and task.require_verification and task.evaluator_ref:
            evaluate_submitted_task(queue, task.id, bus=bus, trigger="event-note")
        elif task.status in (Status.CLAIMED, Status.STARTED, Status.SUSPENDED):
            woke_waiter = False
            if queue.get_active_run_waiter(task.id) is not None:
                woke_waiter = _wake_for_event_note(task, body.note)
            if (not woke_waiter) and task.owner and task.owner_session_id is not None:
                from . import bridge

                bridge.resume_steered_owner(
                    task.owner,
                    task.id,
                    (
                        f"Task {task.id} received an event note: {body.note}. "
                        "Re-read the task history and handle the new external state before finalizing."
                    ),
                    owner_session_id=task.owner_session_id,
                    idempotency_key=f"event-note:{task.id}:{event_id}",
                )
        return result

    @app.post("/tasks/{task_id}/run-waiter/register")
    def register_run_waiter(task_id: str, body: RunWaiterRegisterBody) -> dict:
        try:
            return queue.register_run_waiter(
                task_id,
                pid=body.pid,
                host=body.host,
                start_token=body.start_token,
                resume_worktree=body.resume_worktree,
                command=body.command,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc

    @app.post("/tasks/{task_id}/run-waiter/finish")
    def finish_run_waiter(task_id: str, body: RunWaiterFinishBody) -> dict:
        waiter = queue.retire_run_waiter_with_wake(
            task_id,
            generation=body.generation,
            pid=body.pid,
            host=body.host,
            start_token=body.start_token,
            reason="waiter completed",
            message=body.message,
            sender="agent-dispatch-hibernate",
        )
        return {"accepted": waiter is not None, "waiter": waiter}
