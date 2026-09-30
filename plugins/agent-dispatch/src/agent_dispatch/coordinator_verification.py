"""Verification, event-note, and detached-run waiter routes."""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import remote_dispatch
from .coordinator_auth import _make_control_auth, scoped_control_token
from .events import EventBus
from .queue import RegistrationKind, Task, TaskError, TaskQueue

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EventNoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender: NonEmptyText
    note: NonEmptyText


class VerifySubmittedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluator_ref: str | None = None


class RunWaiterRegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: NonEmptyText
    reason: NonEmptyText
    resume_worktree: NonEmptyText
    command: list[str] = Field(default_factory=list)


class RunWaiterArmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: PositiveInt
    pid: PositiveInt
    host: NonEmptyText
    start_token: NonEmptyText


class RunWaiterFinishBody(RunWaiterArmBody):
    message: NonEmptyText


class RunWaiterAbortBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: PositiveInt
    message: NonEmptyText
    wake: bool = True


def register_verification_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
    *,
    control_token: str | None,
    task_dict,
    event_task_dict,
) -> None:
    def _emit_producer_event(event_type: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event_type, "producer_fence": detail})

    current_machine = remote_dispatch.local_machine()
    current_env = os.environ.get("AGENT_DISPATCH_ENV") or "default"

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
        emitter_id = spec.get("id")
        if not isinstance(emitter_id, str) or not emitter_id:
            raise HTTPException(status_code=403, detail="event note sender has no stable emitter id")
        if task.origin_ref != emitter_id:
            raise HTTPException(
                status_code=403,
                detail="event note sender is not subscribed to this task",
            )

    @app.post("/tasks/{task_id}/verify-submitted")
    def verify_submitted(task_id: str, body: VerifySubmittedBody | None = None) -> dict:
        try:
            if body is not None and body.evaluator_ref is not None:
                _task, request = queue.opt_in_submitted_verification(
                    task_id,
                    evaluator_ref=body.evaluator_ref,
                    actor="backfill",
                )
            else:
                request = queue.request_submitted_verification(task_id, trigger="backfill")
            return {"task_id": task_id, "queued": True, "request_id": request.id}
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/event-note")
    def append_event_note(
        task_id: str,
        body: EventNoteBody,
        _auth: None = Depends(_make_control_auth(control_token, _emit_producer_event)),  # noqa: B008
        sender_proof: str | None = Header(default=None, alias="X-Agent-Dispatch-Sender-Proof"),
    ) -> dict:
        assert control_token is not None  # enforced by _make_control_auth
        expected = scoped_control_token(control_token, f"event-note:{body.sender}")
        if sender_proof is None or not secrets.compare_digest(sender_proof, expected):
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
            task, _event_id, _wake_kind = queue.append_event_note(
                task_id,
                sender=body.sender,
                note=body.note,
                enqueue_verification=True,
                wake_agent=True,
            )
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
        return result

    @app.post("/tasks/{task_id}/run-waiter/register")
    def register_run_waiter(task_id: str, body: RunWaiterRegisterBody) -> dict:
        try:
            return queue.prepare_run_waiter(
                task_id,
                worker_id=body.worker_id,
                reason=body.reason,
                resume_worktree=body.resume_worktree,
                command=body.command,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc

    @app.post("/tasks/{task_id}/run-waiter/arm")
    def arm_run_waiter(task_id: str, body: RunWaiterArmBody) -> dict:
        try:
            waiter = queue.arm_run_waiter(
                task_id,
                generation=body.generation,
                pid=body.pid,
                host=body.host,
                start_token=body.start_token,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        return {"accepted": waiter is not None, "waiter": waiter}

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

    @app.post("/tasks/{task_id}/run-waiter/abort")
    def abort_run_waiter(task_id: str, body: RunWaiterAbortBody) -> dict:
        if body.wake:
            waiter = queue.abort_preparing_run_waiter(
                task_id,
                generation=body.generation,
                reason="waiter failed before arming",
                message=body.message,
                sender="agent-dispatch-hibernate",
            )
        else:
            waiter = queue.cancel_preparing_run_waiter(
                task_id,
                generation=body.generation,
                reason="waiter cancelled before arming",
            )
        return {"accepted": waiter is not None, "waiter": waiter}
