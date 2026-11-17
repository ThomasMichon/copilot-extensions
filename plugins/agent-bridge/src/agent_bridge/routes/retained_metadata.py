"""Semantic metadata preparation, separate from native resume and task attachment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from ..acquisition_contract import AcquisitionRefused
from ..retained_metadata_contract import PrepareRetainedMetadataRequest
from ..retained_metadata_recovery import adoption, prepare
from ..creation_worktree_writer import _read

router = APIRouter()


@router.post("/{predecessor_id}/retained-metadata")
async def prepare_metadata(
    predecessor_id: str, request: Request, body: PrepareRetainedMetadataRequest,
) -> dict[str, Any]:
    try:
        _session, boundary = await prepare(
            request.app.state.session_manager, predecessor_id, body.recovery,
            body.worktree_writer, lambda: bool(getattr(request.app.state, "ready", False)),
        )
        return boundary
    except (AcquisitionRefused, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.get("/{predecessor_id}/retained-metadata/{journal_key}")
def inspect_metadata(predecessor_id: str, journal_key: str, request: Request) -> dict[str, Any]:
    """Absence is unknown metadata, never proof that an earlier request did not commit."""
    try:
        row = _read(request.app.state.session_manager.db, journal_key)
        if row is None or row["session_id"] != predecessor_id:
            raise ValueError("Original keyed journal is unavailable")
        boundary = adoption(row)
        if boundary is None:
            raise ValueError("No committed metadata adoption is observable; do not infer non-admission")
        return boundary
    except (AcquisitionRefused, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc
