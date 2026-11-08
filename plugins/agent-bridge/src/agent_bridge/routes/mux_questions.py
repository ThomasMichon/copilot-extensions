"""Authenticated opt-in degraded question inspection, input and audit."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..mux_question_forms import MuxQuestionError
from ..represented_pending import represented_pending_events
from .live_questions import _exact

router = APIRouter(prefix="/api/v1/live-sessions", tags=["mux-questions"])


class MuxAnswer(BaseModel):
    """Exact inspected single-form selection, never a permission operation."""

    model_config = ConfigDict(extra="forbid")
    inspection_id: str = Field(min_length=36, max_length=36)
    sender: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")
    option: Annotated[int, Field(strict=True, ge=1, le=12)] | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def one_answer(self):
        if not self.sender.strip() or (self.option is None) == (self.text is None):
            raise ValueError("exactly one option or text answer is required")
        return self


def _available(request: Request, session_id: str) -> dict:
    row = _exact(request, session_id)
    if row.get("status") != "live" or not row.get("worktree_id"):
        raise HTTPException(409, "an exact registered live worktree session is required")
    native = request.app.state.live_questions.current(session_id, row)
    if native is not None and any(q.answerable for q in native.requests):
        raise HTTPException(409, "native answering is available; use the native question/generation path")
    if native is not None and native.requests:
        raise HTTPException(409, "native pending question is not answerable; mux cannot override its refusal")
    log = request.app.state.live_event_store.get(session_id)
    pending = represented_pending_events(log.snapshot_history()[1]) if log else []
    if len(pending) != 1 or pending[0].event != "ask_user_request":
        raise HTTPException(409, "one corroborated root ask_user is required; permissions stay terminal-owned")
    data = pending[0].data
    schema = data.get("requested_schema")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if (
        not isinstance(properties, dict) or len(properties) != 1
        or not isinstance(data.get("message"), str) or not data["message"].strip()
        or not (data.get("tool_call_id") or data.get("request_id"))
    ):
        raise HTTPException(409, "only a corroborated single-field native form is supported")
    field = next(iter(properties.values()))
    if not isinstance(field, dict) or field.get("type") not in {"string", "boolean", "integer", "number"}:
        raise HTTPException(409, "only single scalar forms are mux-answerable")
    return {**row, "_mux_request": {"event_id": pending[0].id, **data}}


@router.get("/{session_id}/questions/mux")
async def inspect_mux_question(session_id: str, request: Request) -> dict:
    row = _available(request, session_id)
    try:
        return await request.app.state.mux_questions.inspect(session_id, row, request.app.state.db)
    except MuxQuestionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{session_id}/questions/mux")
async def answer_mux_question(session_id: str, body: MuxAnswer, request: Request) -> dict:
    row = _available(request, session_id)
    try:
        return await request.app.state.mux_questions.answer(
            session_id, row, body, request.app.state.db, request.app.state.live_event_store,
            refresh=lambda: _available(request, session_id),
        )
    except MuxQuestionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{session_id}/questions/mux/answers/{inspection_id}")
async def mux_answer_audit(session_id: str, inspection_id: str, request: Request) -> dict:
    rows = request.app.state.db.execute_read(
        "SELECT inspection_id,session_id,sender,answer,state,detail,created_at "
        "FROM mux_question_answers WHERE session_id=? AND inspection_id=?",
        (session_id, inspection_id),
    )
    if not rows:
        raise HTTPException(404, "mux answer audit not found")
    return dict(rows[0])
