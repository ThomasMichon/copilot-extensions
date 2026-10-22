"""Strict session acquisition; legacy create remains unchanged."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request

from ..acquisition_cli import ACQUISITION_PROTOCOL_VERSION
from ..acquisition_contract import AcquisitionRefused, AcquisitionRequest, AcquisitionResult
from ..acquisition_runtime import AcquisitionRuntime
from ..protocol import PLUGIN_READINESS_PROTOCOL_VERSION

router = APIRouter(prefix="/api/v1/acquisitions", tags=["acquisition"])


class RequestEnvelope(AcquisitionRequest):
    protocol_version: int


@router.get("/{delivery_id}")
async def inspect_acquisition(delivery_id: str, request: Request) -> dict:
    state = request.app.state
    runtime = getattr(state, "acquisition_runtime", None) or AcquisitionRuntime(
        state.session_manager, state.resolver, state.db, state,
    )
    try:
        return await runtime.inspect_pending(delivery_id)
    except AcquisitionRefused as exc:
        raise HTTPException(404, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("", response_model=AcquisitionResult)
async def acquire_session(body: RequestEnvelope, request: Request) -> AcquisitionResult:
    if body.protocol_version not in {30, 31, 32, ACQUISITION_PROTOCOL_VERSION, PLUGIN_READINESS_PROTOCOL_VERSION} or (
        body.version == 2 and body.protocol_version < ACQUISITION_PROTOCOL_VERSION
    ) or (
        body.mode == "cli" and body.plugin_readiness is not None
        and body.protocol_version < PLUGIN_READINESS_PROTOCOL_VERSION
    ):
        raise HTTPException(426, detail={
            "code": "unsupported_acquisition_protocol",
            "message": "request does not support strict session acquisition protocol",
            "effect": "not_started",
        })
    state = request.app.state
    if not getattr(state, "ready", True) or state.session_manager.is_draining:
        raise HTTPException(503, detail={"code": "acquisition_not_ready"})
    runtime = getattr(state, "acquisition_runtime", None)
    if runtime is None:
        runtime = state.acquisition_runtime = AcquisitionRuntime(
            state.session_manager, state.resolver, state.db, state,
        )
    req = AcquisitionRequest.model_validate(body.model_dump(exclude={"protocol_version"}))
    try:
        return await runtime.acquire(req)
    except AcquisitionRefused as exc:
        raise HTTPException(409, detail={
            "code": exc.code, "message": str(exc), "delivery_id": req.delivery_id,
            **({"effect": exc.effect} if exc.effect is not None else {}),
        }) from exc
    except sqlite3.IntegrityError as exc:
        if "acquisition launch fence held" not in str(exc):
            raise
        raise HTTPException(409, detail={"code": "launch_gate_held"}) from exc
