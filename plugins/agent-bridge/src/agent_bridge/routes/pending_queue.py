"""Outside-session inbox controls over the existing local retained-inbox owner."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import compatible_inbox, pending_queue
from ..runtime_custody import RuntimeCustodyError
from .live_inbox import _local_database, _serving_database

router = APIRouter(tags=["pending-queue"])


class ReconcileQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: int = Field(strict=True, ge=1, le=1)
    worktree_id: str = Field(min_length=1, max_length=256)
    consumer_incarnation: str = Field(min_length=1, max_length=128)
    after: int = Field(default=0, ge=0, le=pending_queue.MAX_ID)
    limit: int = Field(default=50, ge=1, le=100)


@router.get("/pending-queue-contract")
def contract() -> dict[str, Any]:
    """Declare the executing provider's optional contract independently of builds."""
    return pending_queue.CONTRACT


@router.get("/{session_id}/pending-queue")
def inspect(
    session_id: str,
    request: Request,
    worktree_id: str = Query(min_length=1, max_length=256),
    contract_version: int = Query(ge=1, le=1),
    after: int = Query(default=0, ge=0, le=pending_queue.MAX_ID),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Read metadata only; never use the extension's claiming/polling routes."""
    db = _local_database(request, write=False)
    try:
        return pending_queue.inspect(
            db,
            session_id,
            worktree_id,
            limit=limit,
            after=after,
            attention=getattr(request.app.state, "live_delivery_state", None),
        )
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except RuntimeCustodyError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/{session_id}/pending-queue/reconcile")
def reconcile(
    session_id: str,
    body: ReconcileQueue,
    request: Request,
) -> dict[str, Any]:
    """Prepare safe original offers; native submission stays with the receiver."""
    db = _serving_database(request)
    try:
        prepared = compatible_inbox.reconcile_offers(
            db,
            session_id,
            body.worktree_id,
            body.consumer_incarnation,
            after=body.after,
            limit=body.limit,
        )
    except RuntimeCustodyError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "pending_queue_reconcile_refused",
                "message": str(exc),
            },
        ) from exc
    result = pending_queue.inspect(
        db,
        session_id,
        body.worktree_id,
        limit=body.limit,
        after=body.after,
        attention=getattr(request.app.state, "live_delivery_state", None),
    )
    return {
        **result,
        "reconciled": {
            "prepared_message_ids": prepared,
            "scope": "original-offer-preparation",
            "sdk_submission": "not-requested",
            "receiver_notice": "unchanged",
        },
    }
