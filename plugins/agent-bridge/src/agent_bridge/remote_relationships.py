"""Bridge-owned acquisition and retirement journal for externally hosted sessions."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from . import relationship_authority as authority
from .models import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager


def owned(session: Session) -> bool:
    return session.target.type == "remote-acp" and session.target.caller_owner_ref is not None


def client_quiescent(client) -> bool:
    transport = getattr(client, "_remote_transport", None)
    return (
        not client.is_running and not client.has_active_background_tasks
        and all(task.done() for task in getattr(transport, "_tasks", ()))
    )


def binding(session: Session) -> dict:
    return {
        "target": session.target.remote_acp,
        "project": session.target.project,
        "cwd": session.target.cwd,
    }


def _row(manager: SessionManager, session: Session) -> dict:
    row = manager._db.get_relationship(session.session_id)
    if row is None or row["owner"] != session.target.caller_owner_ref or row["recovery"]["binding"] != binding(session):
        raise authority.RelationshipRefused("Managed relationship journal is missing or changed")
    return row


def native_ids(manager: SessionManager, session: Session) -> set[str]:
    """Include persisted attachment identities even before a load succeeds."""
    values = {session.acp_session_id, session._remote_requested_session_id}
    if owned(session):
        row = _row(manager, session)
        values.update((row["native_id"], row["requested_native_id"]))
    return {value for value in values if value is not None}


def _reconcile_native_id(manager: SessionManager, session: Session) -> dict:
    row = _row(manager, session)
    if row["phase"] in {"attempting", "retiring", "retired"} and row["native_id"] is None:
        stored = manager._db.get_session(session.session_id)
        persisted_native_id = stored.get("acp_session_id") if stored else None
        if persisted_native_id:
            if row["requested_native_id"] not in {None, persisted_native_id}:
                raise authority.RelationshipRefused("Persisted remote native identity changed")
            manager._db.execute_write(
                "UPDATE remote_acp_relationships SET native_id=? WHERE session_id=? AND native_id IS NULL",
                (persisted_native_id, session.session_id),
            )
            row = _row(manager, session)
    return row


async def _transaction(
    manager: SessionManager, session: Session, operation: str, payload: dict,
    *, closed: bool = False, allow_closed: bool = False,
) -> dict:
    row = _row(manager, session)
    try:
        result = await authority.command(operation, row["owner"], payload)
    except authority.RelationshipUnknown:
        try:
            result = await authority.command("inspect", row["owner"], {"request_id": row["id"]})
        except authority.RelationshipRefused as exc:
            raise authority.RelationshipUnknown(
                "Original relationship outcome remains unknown; reconciliation was unavailable",
            ) from exc
    needs_identity = operation == "bind" or isinstance(result.get("producer"), dict) and "identity" in result["producer"]
    if needs_identity and not row["native_id"]:
        raise authority.RelationshipUnknown("Bound claim has no durable native identity")
    identity = (
        authority.native_identity(row["native_id"])
        if needs_identity else None
    )
    return authority.confirm(
        result, owner=row["owner"], request_id=row["id"], recovery=row["recovery"],
        token=row["claim"]["token"] if row["claim"] else None,
        identity=identity,
        closed=closed or allow_closed and result.get("phase") == "closed",
    )


async def acquire(manager: SessionManager, session: Session, requested_native_id: str | None) -> str | None:
    """Reserve before remote admission; recover the same request after response loss."""
    row = _reconcile_native_id(manager, session)
    if row["phase"] in {"retiring", "retired", "closed", "conflict"}:
        raise authority.RelationshipRefused("Managed relationship is locally retired")
    if requested_native_id is not None and requested_native_id not in {row["requested_native_id"], row["native_id"]}:
        raise authority.RelationshipRefused("Managed relationship native identity changed")
    if row["claim"] is not None and row["native_id"]:
        authority.require_binding_capacity(row["claim"])
        await bind(manager, session, row["native_id"])
        return row["native_id"]
    if row["claim"] is None:
        await authority.require_capability()
    result = await _transaction(manager, session, "reserve", {
        "kind": "bridge", "namespace": authority.NAMESPACE,
        "request_id": row["id"], "recovery": row["recovery"],
    })
    if row["phase"] == "pending":
        manager._db.save_relationship_claim(session.session_id, result, "reserved")
    elif row["claim"] is None:
        raise authority.RelationshipRefused("Managed relationship lost its confirmed reservation")
    authority.require_binding_capacity(result)
    if row["phase"] == "attempting" and not row["native_id"] and not row["requested_native_id"]:
        raise authority.RelationshipUnknown(
            "Remote creation outcome is uncertain; session/new will not be replayed",
        )
    if row["phase"] == "attempting":
        manager._db.save_relationship_claim(session.session_id, result, "reserved")
    return row["requested_native_id"]


def enter_attempt(manager: SessionManager, session: Session) -> None:
    row = _row(manager, session)
    if row["phase"] != "reserved":
        raise authority.RelationshipRefused("Remote admission has no confirmed reservation")
    manager._db.enter_relationship_attempt(session.session_id)


async def bind(manager: SessionManager, session: Session, native_id: str) -> None:
    if not isinstance(native_id, str) or not native_id:
        raise authority.RelationshipUnknown("Peer did not return a durable native conversation identity")
    row = _row(manager, session)
    if row["phase"] in {"retired", "closed"} or row["native_id"] not in {None, native_id}:
        raise authority.RelationshipRefused("Managed relationship cannot change its native identity")
    authority.require_binding_capacity(row["claim"])
    manager._db.save_relationship_native_id(session.session_id, native_id)
    row = _row(manager, session)
    result = await _transaction(manager, session, "bind", {
        "token": row["claim"]["token"],
        "identity": authority.native_identity(native_id),
    })
    manager._db.save_relationship_claim(session.session_id, result, "bound")


async def assert_admission(manager: SessionManager, session: Session) -> None:
    if not owned(session):
        return
    client, status = session.client, session.status
    if client is None or not client.is_running or status not in {SessionStatus.IDLE, SessionStatus.RUNNING}:
        raise authority.RelationshipRefused("Managed relationship has no live admitted client")
    row = _row(manager, session)
    if row["phase"] != "bound" or not row["native_id"] or row["native_id"] != session.acp_session_id:
        raise authority.RelationshipRefused("Managed relationship is not confirmed for admission")
    await _transaction(manager, session, "inspect", {"token": row["claim"]["token"]})
    current = _row(manager, session)
    if (
        manager._sessions.get(session.session_id) is not session
        or current["phase"] != "bound" or current["claim"] != row["claim"]
        or session.client is not client or not client.is_running or session.status != status
    ):
        raise authority.RelationshipRefused("Managed relationship changed during admission")


def inspection(manager: SessionManager, session: Session) -> dict:
    if not owned(session):
        return {"ownership": "unowned"}
    row = manager._db.get_relationship(session.session_id)
    if row is None:
        return {"ownership": "unconfirmed"}
    return inspect_row(row)


def inspect_row(row: dict) -> dict:
    return {
        "ownership": "owned" if row["phase"] == "bound" else row["phase"],
        "relationship_id": row["id"], "owner": row["owner"],
        "claim_ref": row["claim"]["ref"] if row["claim"] else None,
        "native_id": row["native_id"], "uncertain_remote_outcome": row["uncertain"],
        **({"reservation_absent": True} if row.get("absence") is not None else {}),
    }


def verification(manager: SessionManager, subject: str) -> dict:
    if not isinstance(subject, str) or not subject.startswith(authority.NAMESPACE + ":l1.") or len(subject) > 4096:
        raise authority.RelationshipRefused("Invalid relationship verification subject")
    row = manager._db.relationship_subject(subject)
    if row is None:
        raise authority.RelationshipRefused("Relationship subject is not in this Bridge authority")
    session = manager._sessions.get(row["session_id"])
    quiescent = session is None or (
        session.client is None
        and (session._prompt_task is None or session._prompt_task.done())
    )
    return {
        "exists": True,
        "relationship_verification": {
            "version": 1, "subject": subject,
            "retired": row["phase"] in {"retired", "closed"}, "quiescent": quiescent,
        },
    }


async def end(manager: SessionManager, session: Session, *, turn_locked: bool = False) -> None:
    """Retire local admission, never the peer service or its retained history."""
    if turn_locked:
        await _end_locked(manager, session)
        return
    async with session._turn_start_lock:
        await _end_locked(manager, session)


async def _end_locked(manager: SessionManager, session: Session) -> None:
    async with session._lifecycle_lock:
        row = _reconcile_native_id(manager, session)
        if row["phase"] != "closed":
            manager._db.execute_write(
                "UPDATE remote_acp_relationships SET phase='retiring' WHERE session_id=?",
                (session.session_id,),
            )
            manager._clear_pending_queue(session, reason="ended")
            # Do not claim remote cancellation: close only our local transport.
            task = session._prompt_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if session.client is not None:
                client = session.client
                await client.shutdown()
                if not client_quiescent(client):
                    raise authority.RelationshipUnknown("Local remote transport did not quiesce")
                session.client = None
            manager._db.retire_relationship(session.session_id)
            session.status = SessionStatus.STOPPED
            manager._db.update_session_stopped(session.session_id, time.time(), False)
            if row["claim"] is None:
                result = await authority.command("inspect", row["owner"], {
                    "request_id": row["id"], "allow_missing": True,
                })
                if authority.confirm_absence(result, owner=row["owner"], request_id=row["id"]):
                    current = _row(manager, session)
                    if current["native_id"] is not None or current["uncertain"]:
                        raise authority.RelationshipUnknown("Reservation absence conflicts with a retained remote outcome")
                    manager._db.save_relationship_absence(session.session_id, result)
                else:
                    result = authority.confirm(
                        result, owner=row["owner"], request_id=row["id"], recovery=row["recovery"],
                    )
                    manager._db.save_relationship_claim(session.session_id, result, "retired")
            row = _row(manager, session)
            if row["phase"] != "closed" and row["claim"]["phase"] == "pending" and row["native_id"] is not None:
                latest = await _transaction(
                    manager, session, "inspect", {"token": row["claim"]["token"]}, allow_closed=True,
                )
                manager._db.save_relationship_claim(session.session_id, latest, "retired")
            if row["phase"] != "closed":
                result = await _transaction(
                    manager, session, "close", {"token": row["claim"]["token"]}, closed=True,
                )
                manager._db.save_relationship_claim(session.session_id, result, "closed")
        manager._db.update_session_status(session.session_id, SessionStatus.ENDED.value, time.time())
        session.status = SessionStatus.ENDED
        if session.event_log:
            session.event_log.append("relationship_retired", inspection(manager, session))
        manager._sessions.pop(session.session_id, None)
