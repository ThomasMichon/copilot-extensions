"""Retained remote relationship inspection and session-start HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from .. import remote_acp_sessions as remote_api
from ..models import StartSessionRequest, StartSessionResponse
from ..relationship_authority import RelationshipRefused
from ..session_manager import SessionManager

router = APIRouter()


@router.get("/relationship-verification")
async def relationship_verification(request: Request, subject: str = Query(max_length=4096)) -> dict:
    try:
        return remote_api.relationships.verification(request.app.state.session_manager, subject)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/{session_id}/relationship")
async def inspect_relationship(session_id: str, request: Request) -> dict:
    row = request.app.state.session_manager.db.get_relationship(session_id)
    if row is None:
        raise HTTPException(404, "No retained relationship for this Bridge handle")
    return remote_api.relationships.inspect_row(row)


async def start_remote_session(
    mgr: SessionManager, req: StartSessionRequest, agent_name: str,
) -> StartSessionResponse:
    if remote_api.has_local_launch_options(req):
        raise HTTPException(422, "Remote ACP target does not launch local resources")
    try:
        remote = await remote_api.connect_remote_session(
            mgr, agent_name.removeprefix("remote-acp:"),
            cwd=req.target_dir or "/", caller_id=req.caller_id,
            caller_session_id=req.caller_session_id, remote_session_id=req.remote_session_id,
            caller_owner_ref=req.caller_owner_ref,
            **({"reuse": True} if (req.caller_id or req.caller_owner_ref is not None) and not req.force_new else {}),
            **({"project": req.sender_repo} if req.sender_repo is not None else {}),
        )
    except remote_api.RemoteSessionBusy as exc:
        raise HTTPException(409, exc.detail) from None
    except RelationshipRefused as exc:
        raise HTTPException(409, getattr(exc, "detail", str(exc))) from None
    except (ValueError, PermissionError, ConnectionError):
        raise HTTPException(422, "Remote ACP target/connection refused") from None
    return StartSessionResponse(
        session_id=remote.session_id, name=remote.name, status=remote.status,
        caller_session_id=remote.caller_session_id,
        remote_relationship=remote_api.relationships.inspection(mgr, remote),
    )
