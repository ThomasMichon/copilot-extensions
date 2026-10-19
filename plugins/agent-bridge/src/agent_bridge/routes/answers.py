"""Live permission and structured-form answer routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import AnswerAskUserRequest, AnswerPermissionRequest

router = APIRouter()


@router.post("/{session_id}/ask-user")
async def answer_ask_user(
    session_id: str, req: AnswerAskUserRequest, request: Request,
):
    """Answer a parked structured elicitation without changing its input."""
    mgr = request.app.state.session_manager
    try:
        resolved = await mgr.answer_ask_user(
            session_id, req.tool_call_id, req.content, action=req.action,
            **({"request_id": req.request_id} if req.request_id is not None else {}),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not resolved:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No pending ask_user for tool call {req.tool_call_id} "
                f"on session {session_id}"
            ),
        )
    return {"status": "answered"}


@router.post("/{session_id}/permission")
async def answer_permission(
    session_id: str, req: AnswerPermissionRequest, request: Request,
):
    """Resolve exactly the currently parked correlated permission request."""
    mgr = request.app.state.session_manager
    try:
        resolved = await mgr.answer_permission(
            session_id, req.request_id, req.option_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not resolved:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No pending permission request {req.request_id} "
                f"on session {session_id}"
            ),
        )
    return {"status": "answered"}
