"""Explicit local enrollment and retained handoff; never a legacy downgrade."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import ipaddress
import time

from fastapi import APIRouter, HTTPException, Request

from ..db import Database
from ..inbox_custody import InboxConsumer
from ..live_inbox_models import (
    InboxBeginResult, InboxCurrentRequest, InboxEnrollRequest, InboxHandoffRequest,
    InboxNegotiateRequest, InboxNegotiationInfo, InboxOutcomeRequest, InboxOutcomeResult,
    InboxPollResult, RetainedInboxMessage,
)
from ..live_inbox_runtime import LiveInboxRuntime
from ..loop_governance import LoopGovernance
from ..models import SendMessageRequest
from ..runtime_custody import RuntimeCustodyError

router = APIRouter(
    prefix="/api/v1/live-sessions/{session_id}/retained-inbox", tags=["live-sessions"],
)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _local_database(request: Request) -> Database:
    # Shared-token middleware remains mandatory. Loopback alone is not a user
    # ACL; this additionally refuses unsupported network/proxy ingress.
    config = getattr(request.app.state, "config", None)
    bind = getattr(config, "bind", "")
    peer = request.client
    if (
        not (bind == "localhost" or _is_loopback(bind))
        or peer is None or not _is_loopback(peer.host)
        or any(key == "forwarded" or key.startswith("x-forwarded-")
               for key in request.headers)
    ):
        raise HTTPException(status_code=409, detail={
            "code": "retained_inbox_ingress_unsupported",
            "message": "Retained inbox requires the admitted local endpoint; proxy ingress is unqualified",
        })
    db = getattr(request.app.state, "db", None)
    if not isinstance(db, Database):
        raise HTTPException(status_code=503, detail="database not ready")
    if db.read_only:
        raise HTTPException(status_code=503, detail="retained inbox database is read-only")
    return db


def _current_owners(request: Request) -> tuple[Database, LoopGovernance, LiveInboxRuntime]:
    db = _local_database(request)
    manager = getattr(request.app.state, "session_manager", None)
    if (
        not getattr(request.app.state, "ready", False)
        or getattr(request.app.state, "shutting_down", False)
        or (manager is not None and manager.is_draining)
    ):
        raise HTTPException(status_code=503, detail="retained inbox frontend is not serving")
    governance = getattr(request.app.state, "loop_governance", None)
    runtime = getattr(request.app.state, "live_inbox_runtime", None)
    if not isinstance(governance, LoopGovernance) or not isinstance(runtime, LiveInboxRuntime):
        raise HTTPException(status_code=503, detail="retained inbox owners are unavailable")
    return db, governance, runtime


def _consumer(session_id: str, body: InboxNegotiateRequest) -> InboxConsumer:
    consumer = body.consumer.observation()
    if consumer.session_id != session_id:
        raise RuntimeCustodyError("Retained inbox requires the exact native session id")
    return consumer


@contextmanager
def _refusals() -> Iterator[None]:
    try:
        yield
    except RuntimeCustodyError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "retained_inbox_refused", "message": str(exc),
        }) from exc


def enqueue_message(
    request: Request, db: Database, session_id: str, body: SendMessageRequest, *,
    kind: str, expected_session_id: str | None, now: float,
) -> tuple[int | None, str | None]:
    """Associate ordinary admitted sends only with their currently enrolled inbox.

    Observing unavailable owners is not a legacy fallback. The DB serializes
    the actual enrollment decision and surfaces this refusal for retained work.
    Identical keyed recovery needs neither a new enrollment nor an open epoch.
    """
    if db.read_only:
        raise HTTPException(status_code=503, detail="retained inbox database is read-only")
    admission = None
    refusal = None
    try:
        current_db, governance, runtime = _current_owners(request)
        if current_db is not db:
            raise RuntimeCustodyError("Inbox frontend database owner changed")
        admission = runtime.producer_admission(db, governance)
    except HTTPException as exc:
        detail = exc.detail
        refusal = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    except RuntimeCustodyError as exc:
        refusal = str(exc)
    with _refusals():
        return db.enqueue_live_message_if_fresh(
            session_id, sender=body.sender, body=body.body, now=now,
            reply_to=body.reply_to, kind=kind, delivery=body.delivery,
            expected_session_id=expected_session_id, idempotency_key=body.idempotency_key,
            report_duplicate=True, runtime_admission=admission,
            enrolled_inbox=True, inbox_refusal=refusal,
        )


@router.post("/negotiate", response_model=InboxNegotiationInfo)
def negotiate(
    session_id: str, body: InboxNegotiateRequest, request: Request,
) -> InboxNegotiationInfo:
    db, governance, runtime = _current_owners(request)
    with _refusals():
        observation = runtime.negotiate(
            db, governance, _consumer(session_id, body), now=time.time(),
        )
    return InboxNegotiationInfo(**asdict(observation))


@router.post("/enroll", response_model=InboxNegotiationInfo)
def enroll(
    session_id: str, body: InboxEnrollRequest, request: Request,
) -> InboxNegotiationInfo:
    db, governance, runtime = _current_owners(request)
    with _refusals():
        runtime.enroll(
            db, governance, _consumer(session_id, body), body.binding.observation(),
            expected_incarnation=body.expected_incarnation, now=time.time(),
        )
    return InboxNegotiationInfo(
        binding=body.binding, expected_incarnation=body.expected_incarnation,
    )


@router.post("/poll", response_model=InboxPollResult)
def poll(session_id: str, body: InboxCurrentRequest, request: Request) -> InboxPollResult:
    db, governance, runtime = _current_owners(request)
    with _refusals():
        rows = runtime.poll(
            db, governance, _consumer(session_id, body), body.binding.observation(), now=time.time(),
        )
    return InboxPollResult(
        binding=body.binding, consumer=body.consumer,
        messages=[RetainedInboxMessage.model_validate(row) for row in rows],
    )


@router.post("/begin", response_model=InboxBeginResult)
def begin(session_id: str, body: InboxHandoffRequest, request: Request) -> InboxBeginResult:
    db, governance, runtime = _current_owners(request)
    with _refusals():
        granted = runtime.begin(
            db, governance, _consumer(session_id, body), body.binding.observation(),
            body.message_id, body.handoff_id, now=time.time(),
        )
    return InboxBeginResult(begin=granted)


@router.post("/outcome", response_model=InboxOutcomeResult)
def outcome(
    session_id: str, body: InboxOutcomeRequest, request: Request,
) -> InboxOutcomeResult:
    # Original observations can settle after closure, deregistration or retire.
    # No current binding or fresh admission is acquired by reporting an outcome.
    db = _local_database(request)
    with _refusals():
        stage = db.record_live_message_handoff(
            _consumer(session_id, body), body.message_id, body.handoff_id,
            outcome=body.outcome, now=time.time(),
        )
    return InboxOutcomeResult(stage=stage)
