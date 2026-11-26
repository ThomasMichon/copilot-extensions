"""Identity-pinned resume over the existing workspace lifecycle owners."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .models import SessionInfo
from .worktree_native_resume import Mode, _refuse, _resume_native_worktree

router = APIRouter(tags=["worktrees"])


class ExactResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_session_id: StrictStr = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@router.post("/api/v1/worktrees/{worktree_id}/resume/{mode}/exact", response_model=SessionInfo)
async def resume_exact_worktree(
    worktree_id: str, request: Request, mode: Mode, body: ExactResumeBody,
) -> SessionInfo | JSONResponse:
    result = await _resume_native_worktree(
        worktree_id, request, mode, strict=True, expected_native=body.native_session_id,
    )
    value = result.model_dump() if isinstance(result, SessionInfo) else json.loads(bytes(result.body))
    if (
        value.get("acp_session_id") != body.native_session_id
        or value.get("durable_session_id") != body.native_session_id
        or value.get("worktree_id") != worktree_id or value.get("mode") != mode
    ):
        raise _refuse(
            "native_resume_unconfirmed",
            "The resume result does not confirm the selected conversation; inspect before retrying",
            status=502,
        )
    return result
