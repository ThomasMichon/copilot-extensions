"""Verification, event-note, and detached-run waiter routes."""

from __future__ import annotations

import hashlib

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictInt

from .events import EventBus
from .queue import Status, Task, TaskError, TaskQueue
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
    pid: StrictInt
    host: str | None = None
    start_token: str | None = None


def register_verification_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
    *,
    task_dict,
    event_task_dict,
) -> None:
    def _wake_for_event_note(task: Task, note: str) -> None:
        from . import bridge, hibernation_claims

        message = (
            f"Task {task.id} received an event note: {note}. "
            "Re-read the task history and handle the new external state before finalizing."
        )
        waiter = queue.supersede_run_waiter(task.id, reason="event note wake")
        if waiter is not None:
            bridge.send_nudge(
                waiter["resume_worktree"],
                message,
                sender="agent-dispatch-event-note",
            )
            hibernation_claims.release_hibernation_claim_for_worktree(
                task.id, waiter["resume_worktree"]
            )
            return
        if task.owner and task.owner_session_id is not None:
            bridge.resume_steered_owner(
                task.owner,
                task.id,
                message,
                owner_session_id=task.owner_session_id,
                idempotency_key=(
                    "event-note:"
                    f"{task.id}:"
                    f"{hashlib.sha1(note.encode('utf-8')).hexdigest()[:16]}"
                ),
            )

    @app.post("/tasks/{task_id}/verify-submitted")
    def verify_submitted(task_id: str) -> dict:
        try:
            return evaluate_submitted_task(queue, task_id, bus=bus, trigger="backfill")
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/event-note")
    def append_event_note(task_id: str, body: EventNoteBody) -> dict:
        try:
            task = queue.append_event_note(task_id, sender=body.sender, note=body.note)
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
            _wake_for_event_note(task, body.note)
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
        waiter = queue.retire_run_waiter(
            task_id,
            pid=body.pid,
            host=body.host,
            start_token=body.start_token,
            reason="waiter completed",
        )
        return {"accepted": waiter is not None, "waiter": waiter}
