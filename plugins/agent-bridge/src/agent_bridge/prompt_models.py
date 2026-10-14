"""Ordinary prompt admission and durable pending-prompt API models."""

from __future__ import annotations

from pydantic import BaseModel

from .session_status import SessionStatus


class SubmitPromptRequest(BaseModel):
    """Request to submit a prompt to a session.

    ``queue`` opts into durable send-or-queue (#4114): a busy session's prompt
    is persisted (``pending_prompts``) and delivered FIFO on the next settle,
    surviving remount, crash and restart, instead of a 409 (the default).
    ``caller_id`` tags the queued row. With ``queue``, ``no_resume`` refuses a
    stopped session (409 ``session_stopped``) instead of resuming it (gen 25).
    """

    prompt: str
    queue: bool = False
    caller_id: str | None = None
    no_resume: bool = False
    expected_native_session_id: str | None = None


class SubmitPromptResponse(BaseModel):
    """Result of a prompt submission to ``session_id`` (a successor when the submit
    handed an over-critical idle session off). Immediate run: ``turn_index`` is
    the started turn. Durable send-or-queue (``queued=True``, #4114): the prompt
    was persisted rather than run, ``turn_index`` is None and ``queue_id`` /
    ``position`` place it in the FIFO queue.
    """

    status: SessionStatus
    native_session_id: str | None = None
    session_id: str | None = None
    turn_index: int | None = None
    queued: bool = False
    queue_id: int | None = None
    position: int | None = None


class PendingPrompt(BaseModel):
    """One durable queued follow-up awaiting delivery (#4114)."""

    id: int
    session_id: str
    caller_id: str | None = None
    prompt: str
    created_at: float


class PendingQueueResponse(BaseModel):
    """Snapshot of a session's durable pending-prompt queue."""

    session_id: str
    pending: list[PendingPrompt]
