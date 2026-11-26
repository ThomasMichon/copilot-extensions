"""Identity-bound stop through the existing local managed lifecycle owner."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .session_status import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager

CAPABILITY = "managed_exact_stop"
router = APIRouter(tags=["sessions"])


class ExactStopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_session_id: StrictStr = Field(
        min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )


class ExactStopResult(BaseModel):
    session_id: str
    native_session_id: str
    status: str = "stopped"
    host_reclaimed: bool = False


def require_holder(
    manager: SessionManager, session_id: str, session: Session, native_session_id: str | None,
) -> None:
    row = manager.db.get_session(session_id)
    if (
        not native_session_id or manager._sessions.get(session_id) is not session
        or session.acp_session_id != native_session_id
        or session.client is not None and session.client.acp_session_id != native_session_id
        or row is None or row["acp_session_id"] != native_session_id
        or session.status == SessionStatus.ENDED or row["status"] == "ended"
    ):
        raise ValueError("expected_native_mismatch")
    if session.target.type != "local":
        raise ValueError("native_stop_owner_unsupported")


@router.post("/api/v1/sessions/{session_id}/stop/exact", response_model=ExactStopResult)
async def stop_exact_managed(
    session_id: str, request: Request, body: ExactStopBody,
) -> ExactStopResult:
    from .session_manager import SessionBusyError, SessionManager

    manager = cast(SessionManager, request.app.state.session_manager)
    try:
        await manager.stop_session(session_id, expected_native_session_id=body.native_session_id)
    except KeyError:
        raise HTTPException(404, detail={"code": "native_stop_unavailable"}) from None
    except SessionBusyError as exc:
        raise HTTPException(409, detail={"code": "native_stop_busy", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(502, detail={"code": "native_stop_unconfirmed"}) from exc
    session = manager._sessions.get(session_id)
    row = manager.db.get_session(session_id)
    if (
        session is None or session.acp_session_id != body.native_session_id
        or session.status != SessionStatus.STOPPED
        or row is None or row["acp_session_id"] != body.native_session_id or row["status"] != "stopped"
    ):
        raise HTTPException(502, detail={"code": "native_stop_unconfirmed"})
    return ExactStopResult(session_id=session_id, native_session_id=body.native_session_id)
