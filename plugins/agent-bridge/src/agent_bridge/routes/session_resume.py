"""Stopped-session resume and retained-recovery HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ..acquisition_contract import AcquisitionRefused
from ..models import ResumeSessionRequest, SessionInfo
from ..session_manager import (
    DaemonDrainingError,
    ProviderTargetRefreshError,
    SessionManager,
)

router = APIRouter()


@router.post("/{session_id}/resume", response_model=SessionInfo)
async def resume_session(
    session_id: str, request: Request, body: ResumeSessionRequest | None = Body(default=None),
):
    """Resume a stopped session by spawning a new agent process."""
    from .sessions import _session_info

    mgr: SessionManager = request.app.state.session_manager
    try:
        if body and body.retained_recovery is not None:
            from ..retained_session_recovery import resume

            session, recovery = await resume(
                mgr, session_id, body.retained_recovery, body.worktree_writer,
                lambda: bool(getattr(request.app.state, "ready", False)),
            )
            return JSONResponse({
                **_session_info(session).model_dump(mode="json"), "retained_recovery": recovery,
            })
        session = await mgr.resume_session(
            session_id,
            **({"worktree_writer": body.worktree_writer} if body and body.worktree_writer else {}),
        )
    except AcquisitionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    return _session_info(session)


@router.get("/{session_id}/retained-recovery/{operation_id:path}")
def inspect_retained_recovery(session_id: str, operation_id: str, request: Request):
    from ..retained_session_recovery import inspect

    try:
        return inspect(request.app.state.session_manager, session_id, operation_id)
    except (AcquisitionRefused, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
