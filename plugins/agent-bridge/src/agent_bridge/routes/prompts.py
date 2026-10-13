"""Ordinary prompt admission and pending-queue routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response

from ..prompt_models import (
    PendingPrompt,
    PendingQueueResponse,
    SubmitPromptRequest,
    SubmitPromptResponse,
)
from ..session_manager import DaemonDrainingError, ProviderTargetRefreshError
from ..session_status import SessionStatus

if TYPE_CHECKING:
    from ..session_manager import SessionManager

router = APIRouter()


@router.post("/{session_id}/turns", response_model=SubmitPromptResponse)
async def submit_prompt(
    session_id: str,
    req: SubmitPromptRequest,
    request: Request,
    response: Response,
) -> SubmitPromptResponse:
    """Submit a prompt to a session.

    Default (``queue=false``) preserves the legacy contract: run now, or
    409 if the session is busy. With ``queue=true`` the prompt is durably
    queued when busy (persisted to ``pending_prompts``, delivered FIFO on
    settle, surviving remount/crash/restart); response is 202 with
    ``queued=true`` and the queue position.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        if req.expected_native_session_id is not None and req.queue:
            raise ValueError("native-bound prompts cannot be queued")
        if req.queue:
            result = await mgr.submit_or_queue_prompt(
                session_id, req.prompt, caller_id=req.caller_id, no_resume=req.no_resume
            )
        else:
            if req.expected_native_session_id is not None or req.no_resume:
                turn_index = await mgr.submit_prompt(
                    session_id, req.prompt, no_resume=req.no_resume,
                    expected_native_session_id=req.expected_native_session_id,
                )
            else:
                turn_index = await mgr.submit_prompt(session_id, req.prompt)
            result = {"queued": False, "turn_index": turn_index}
    except DaemonDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ProviderTargetRefreshError as exc:
        raise HTTPException(
            status_code=502,
            detail=ProviderTargetRefreshError.public_message,
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    effective = result.get("session_id") or session_id  # a successor, after a prompt-triggered handoff
    session = mgr.get_session(effective)
    status = session.status if session else SessionStatus.IDLE
    if result.get("queued"):
        response.status_code = 202
        return SubmitPromptResponse(
            status=status, session_id=effective, queued=True,
            queue_id=result.get("queue_id"), position=result.get("position"),
        )
    return SubmitPromptResponse(
        turn_index=result.get("turn_index"), status=status, session_id=effective,
        native_session_id=req.expected_native_session_id,
    )


@router.post("/{session_id}/turns/native-bound", response_model=SubmitPromptResponse)
async def submit_native_bound_prompt(
    session_id: str, req: SubmitPromptRequest, request: Request, response: Response,
) -> SubmitPromptResponse:
    """Conditional ordinary turn admission; older daemons cannot ignore its fence."""
    if not req.expected_native_session_id:
        raise HTTPException(status_code=422, detail="expected_native_session_id is required")
    return await submit_prompt(session_id, req, request, response)


@router.get("/{session_id}/queue", response_model=PendingQueueResponse)
async def get_pending_queue(session_id: str, request: Request) -> PendingQueueResponse:
    """Snapshot a session's durable pending-prompt queue (FIFO order)."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        rows = mgr.list_pending_queue(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return PendingQueueResponse(
        session_id=session_id,
        pending=[PendingPrompt(**r) for r in rows],
    )


@router.delete("/{session_id}/queue/{queue_id}", status_code=204)
async def remove_pending_prompt(session_id: str, queue_id: int, request: Request) -> None:
    """Drop one queued follow-up by id (operator removes a chip)."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        hit = mgr.remove_pending_prompt(session_id, queue_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not hit:
        raise HTTPException(
            status_code=404,
            detail=f"Queued prompt {queue_id} not found for session {session_id}",
        )


@router.delete("/{session_id}/queue", status_code=204)
async def clear_pending_queue(session_id: str, request: Request) -> None:
    """Clear a session's whole durable pending-prompt queue."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        mgr.clear_pending_queue(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
