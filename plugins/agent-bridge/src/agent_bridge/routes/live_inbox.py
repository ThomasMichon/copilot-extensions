"""Explicit local enrollment and retained handoff; never a legacy downgrade."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import ipaddress
import time

from fastapi import APIRouter, HTTPException, Query, Request

from ..db import Database
from .. import compatible_inbox
from ..db_live_message_receipts import MAX_RECOVERY_MESSAGE_ID
from ..inbox_custody import InboxConsumer
from ..live_inbox_models import (
    InboxBeginResult, InboxEnrollRequest, InboxHandoffRequest, InboxPollRequest,
    InboxNegotiateRequest, InboxNegotiationInfo, InboxOutcomeRequest, InboxOutcomeResult,
    InboxPollResult, InboxHandoffReceipt, InboxRecoveryResult, RetainedInboxMessage,
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


def _local_database(request: Request, *, write: bool = True) -> Database:
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
    if write and db.read_only:
        raise HTTPException(status_code=503, detail="retained inbox database is read-only")
    return db


@router.get("/recovery", response_model=InboxRecoveryResult)
def recover_handoffs(
    session_id: str, request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    after: int = Query(default=0, ge=0, le=MAX_RECOVERY_MESSAGE_ID),
) -> InboxRecoveryResult:
    """Read pending original observations without enrollment or admission."""
    db = _local_database(request, write=False)
    with _refusals():
        rows = db.inspect_live_handoff_recovery(session_id, limit=limit, after=after)
    return InboxRecoveryResult.model_validate(rows)


@router.get("/recovery/{message_id}", response_model=InboxHandoffReceipt)
def recover_handoff(
    session_id: str, message_id: int, request: Request,
) -> InboxHandoffReceipt:
    """Observe an original attempt, including a consumed outcome after reply loss."""
    db = _local_database(request, write=False)
    with _refusals():
        row = db.live_message_handoff_receipt(session_id, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="original inbox handoff is unavailable")
    return InboxHandoffReceipt.model_validate(row)


def _serving_database(request: Request) -> Database:
    db = _local_database(request)
    manager = getattr(request.app.state, "session_manager", None)
    if (
        not getattr(request.app.state, "ready", False)
        or getattr(request.app.state, "shutting_down", False)
        or (manager is not None and manager.is_draining)
    ):
        raise HTTPException(status_code=503, detail="retained inbox frontend is not serving")
    return db


def _current_owners(request: Request) -> tuple[Database, LoopGovernance, LiveInboxRuntime]:
    db = _serving_database(request)
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


def _compatible_binding(body: InboxEnrollRequest | InboxPollRequest | InboxHandoffRequest) -> None:
    if body.binding.installation != compatible_inbox.BINDING or body.binding.epoch != 1:
        raise RuntimeCustodyError("Unsupported native SDK inbox binding")


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
    compatible = bool(db.execute_read(
        "SELECT 1 FROM bridge_inbox_consumers "
        "WHERE session_id=? AND installation=? AND current=1",
        (session_id, compatible_inbox.BINDING),
    ))
    if compatible and _serving_database(request) is not db:
        raise HTTPException(status_code=503, detail="inbox frontend database owner changed")
    admission = None
    refusal = None
    try:
        current_db = _serving_database(request)
        if current_db is not db:
            raise RuntimeCustodyError("Inbox frontend database owner changed")
        if not compatible:
            _, governance, runtime = _current_owners(request)
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
            exact_session=body.exact_session,
        )


@router.post("/negotiate", response_model=InboxNegotiationInfo)
def negotiate(
    session_id: str, body: InboxNegotiateRequest, request: Request,
) -> InboxNegotiationInfo:
    if body.receiving_api == "native-sdk-v1":
        db = _serving_database(request)
        with _refusals():
            predecessor = compatible_inbox.negotiate(
                db, _consumer(session_id, body), now=time.time(),
            )
        return InboxNegotiationInfo(
            binding={"installation": compatible_inbox.BINDING, "epoch": 1},
            expected_incarnation=predecessor,
        )
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
    if body.receiving_api == "native-sdk-v1":
        db = _serving_database(request)
        with _refusals():
            _compatible_binding(body)
            compatible_inbox.enroll(
                db, _consumer(session_id, body),
                expected_incarnation=body.expected_incarnation, now=time.time(),
            )
        return InboxNegotiationInfo(
            binding=body.binding, expected_incarnation=body.expected_incarnation,
        )
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
def poll(session_id: str, body: InboxPollRequest, request: Request) -> InboxPollResult:
    from .live_startup_controls import startup_readiness

    if body.receiving_api == "native-sdk-v1":
        db = _serving_database(request)
        with _refusals():
            _compatible_binding(body)
            rows = compatible_inbox.poll(
                db, _consumer(session_id, body), now=time.time(), limit=body.limit,
            )
    else:
        db, governance, runtime = _current_owners(request)
        with _refusals():
            rows = runtime.poll(
                db, governance, _consumer(session_id, body), body.binding.observation(),
                now=time.time(), limit=body.limit,
            )
    return InboxPollResult(
        binding=body.binding, consumer=body.consumer,
        messages=[RetainedInboxMessage.model_validate({
            **row, "plugin_readiness": startup_readiness(db, session_id, str(row["kind"])),
        }) for row in rows],
    )


@router.post("/begin", response_model=InboxBeginResult)
def begin(session_id: str, body: InboxHandoffRequest, request: Request) -> InboxBeginResult:
    if body.receiving_api == "native-sdk-v1":
        db = _serving_database(request)
        with _refusals():
            _compatible_binding(body)
            granted = compatible_inbox.begin(
                db, _consumer(session_id, body), body.message_id, body.handoff_id, now=time.time(),
            )
        return InboxBeginResult(begin=granted)
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
            compatible_consumer=body.receiving_api == "native-sdk-v1",
        )
    return InboxOutcomeResult(stage=stage)
