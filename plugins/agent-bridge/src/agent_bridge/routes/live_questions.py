"""Authenticated exact-session question discovery, control delivery and audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..live_controls import CONTROL_MAX_AGE_SECONDS
from ..live_questions import (
    QUESTION_CONTROL, LiveQuestionAnswer, LiveQuestions, QuestionAnswerAck,
    QuestionObservation, matches_process, validate_answer,
)

router = APIRouter(prefix="/api/v1/live-sessions", tags=["live-questions"])
POLL_SECONDS = 0.1


def _exact(request: Request, session_id: str) -> dict[str, Any]:
    row = request.app.state.db.get_live_session(session_id)
    if row is None:
        raise HTTPException(404, "live session not found")
    if row["session_id"] != session_id:
        raise HTTPException(409, "question answers require the exact native session id, not an alias")
    return row


def _questions(request: Request) -> LiveQuestions:
    return request.app.state.live_questions


@router.get("/{session_id}/questions")
async def questions(session_id: str, request: Request) -> dict[str, Any]:
    row = _exact(request, session_id)
    observed = _questions(request).current(session_id, row)
    return {
        "session_id": session_id,
        "generation": observed.generation if observed else None,
        "availability": "available" if observed else "unavailable",
        "reason": None if observed else "no fresh native question observation; use the terminal",
        "requests": [q.model_dump() for q in observed.requests] if observed else [],
    }


@router.post("/{session_id}/questions/poll")
async def poll_questions(
    session_id: str, body: QuestionObservation, request: Request,
) -> dict[str, Any]:
    """The owning extension publishes current state before claiming only answers."""
    row = _exact(request, session_id)
    if not matches_process(row, body):
        raise HTTPException(409, "native extension incarnation changed")
    try:
        _questions(request).publish(session_id, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db = request.app.state.db
    if _questions(request).current(session_id, row) is None:
        raise HTTPException(409, "native registration is not current")
    from ..status_producers import question_snapshot
    question_snapshot(db, session_id, body)
    controls = db.claim_live_controls(
        session_id, time.time(), CONTROL_MAX_AGE_SECONDS, questions_only=True,
    )
    return {"controls": [
        {"id": c["id"], "body": json.loads(c["body"]), "sender": c["sender"]}
        for c in controls
    ]}


def _control(request: Request, session_id: str, control_id: int) -> dict[str, Any]:
    rows = request.app.state.db.execute_read(
        "SELECT claimed_at,outcome,kind,body FROM live_messages "
        "WHERE session_id=? AND id=? AND kind=? AND exact_session=1",
        (session_id, control_id, QUESTION_CONTROL),
    )
    if not rows:
        raise HTTPException(404, "question answer not found")
    return dict(rows[0])


def _outcome(control: dict[str, Any], session_id: str, control_id: int) -> dict[str, Any]:
    raw = control["outcome"]
    result: dict[str, Any] = {
        "session_id": session_id, "answer_id": control_id, "state": "in_flight",
        "accepted": None,
    }
    if raw in {"withdrawn", "expired", "rejected"}:
        result.update(state="withdrawn" if raw != "rejected" else "rejected", accepted=False)
    elif raw is not None:
        ack = QuestionAnswerAck.model_validate_json(raw)
        result.update(
            state=ack.state, accepted=True if ack.state == "accepted" else (
                False if ack.state == "rejected" else None
            ), detail=ack.detail,
        )
    return result


@router.get("/{session_id}/questions/answers/{control_id}")
async def answer_outcome(session_id: str, control_id: int, request: Request) -> dict[str, Any]:
    return _outcome(_control(request, session_id, control_id), session_id, control_id)


@router.post("/{session_id}/questions/answers")
async def answer_question(
    session_id: str, body: LiveQuestionAnswer, request: Request,
) -> dict[str, Any]:
    row = _exact(request, session_id)
    key = hashlib.sha256(json.dumps([
        session_id, body.generation, body.family, body.request_id,
    ]).encode()).hexdigest()
    db = request.app.state.db
    operation_key = "question-answer:" + key

    def previous_answer_id() -> int | None:
        rows = db.execute_read(
            "SELECT id FROM live_messages WHERE session_id=? AND idempotency_key=? "
            "AND kind=? AND exact_session=1",
            (session_id, operation_key, QUESTION_CONTROL),
        )
        return int(rows[0]["id"]) if rows else None

    if (previous := previous_answer_id()) is not None:
        raise HTTPException(409, {
            "reason": "already_admitted", "answer_id": previous,
            "message": "inspect the original answer outcome; do not replay",
        })
    observed = _questions(request).current(session_id, row)
    if observed is None or observed.generation != body.generation:
        raise HTTPException(409, "question observation is unavailable or its generation changed")
    question = next((
        q for q in observed.requests
        if q.request_id == body.request_id and q.family == body.family
    ), None)
    if question is None:
        raise HTTPException(409, "the exact native question is not pending")
    try:
        validate_answer(question, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = {
        **body.model_dump(exclude={"wait_timeout"}),
        "session_id": session_id, "expires_at": time.time() + (body.wait_timeout or 30.0),
    }
    control_id, reason = db.enqueue_live_message_if_fresh(
        session_id, sender=body.sender, body=json.dumps(payload), now=time.time(),
        kind=QUESTION_CONTROL, expected_session_id=session_id, exact_session=True,
        idempotency_key=operation_key, report_duplicate=True,
    )
    if control_id is None or reason is not None:
        raise HTTPException(409, {
            "reason": reason, "answer_id": control_id or previous_answer_id(),
            "message": "answer was already admitted or native target changed; do not replay",
        })
    deadline = time.monotonic() + body.wait_timeout
    while True:
        result = _outcome(_control(request, session_id, control_id), session_id, control_id)
        if result["state"] != "in_flight":
            return result
        if time.monotonic() >= deadline:
            if body.wait_timeout > 0 and db.withdraw_live_control(
                session_id, control_id, time.time(),
            ):
                return _outcome(_control(request, session_id, control_id), session_id, control_id)
            return result
        await asyncio.sleep(POLL_SECONDS)


@router.post("/{session_id}/questions/answers/{control_id}/ack")
async def ack_answer(
    session_id: str, control_id: int, body: QuestionAnswerAck, request: Request,
) -> dict[str, int]:
    control = _control(request, session_id, control_id)
    original = json.loads(control["body"])
    if body.generation != original["generation"] or body.request_id != original["request_id"]:
        raise HTTPException(409, "answer acknowledgment does not match the admitted request")
    acked = request.app.state.db.ack_live_messages(
        session_id, [control_id], time.time(), controls=True, outcome=body.model_dump_json(),
        questions_only=True,
    )
    return {"acked": acked}
