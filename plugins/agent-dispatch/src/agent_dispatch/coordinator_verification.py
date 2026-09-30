"""Verification, event-note, and detached-run waiter routes."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, StrictInt

from . import remote_dispatch
from .coordinator_auth import _make_control_auth, scoped_control_token
from .events import EventBus
from .queue import RegistrationKind, Status, Task, TaskError, TaskQueue


class EventNoteBody(BaseModel):
    sender: str
    note: str


class VerifySubmittedBody(BaseModel):
    evaluator_ref: str | None = None


class RunWaiterRegisterBody(BaseModel):
    worker_id: str
    reason: str
    resume_worktree: str
    command: list[str] = Field(default_factory=list)


class RunWaiterArmBody(BaseModel):
    generation: StrictInt
    pid: StrictInt
    host: str
    start_token: str


class RunWaiterFinishBody(RunWaiterArmBody):
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
            task, event_id = queue.append_event_note(
                task_id,
                sender=body.sender,
                note=body.note,
                enqueue_verification=True,
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
        if task.status in (Status.CLAIMED, Status.STARTED, Status.SUSPENDED):
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
