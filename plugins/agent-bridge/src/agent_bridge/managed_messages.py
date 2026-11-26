"""Durable ordinary input acceptance; only the existing prompt consumer delivers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .db import Database
    from .session_manager import SessionManager


CAPABILITY = "durable_managed_messages"


class ManagedMessage(BaseModel):
    prompt: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=512)
    caller_id: str | None = None
    expected_native_session_id: str | None = Field(default=None, min_length=1)


def _receipt(row: sqlite3.Row, *, duplicate: bool) -> dict[str, Any]:
    return {
        "session_id": row["session_id"], "queue_id": row["queue_id"],
        "idempotency_key": row["idempotency_key"], "native_session_id": row["native_session_id"],
        "queued": True, "duplicate": duplicate, "delivery_state": row["state"],
        "turn_index": row["turn_index"],
    }


def accept(db: Database, session_id: str, message: ManagedMessage) -> dict[str, Any]:
    """Commit the input and its original-key receipt before inspecting a carrier."""
    digest = hashlib.sha256(json.dumps(
        message.model_dump(exclude={"idempotency_key"}),
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with db._runtime_transaction(write=True, full_sync=True) as conn:
        saved = conn.execute(
            "SELECT * FROM managed_message_receipts WHERE session_id=? AND idempotency_key=?",
            (session_id, message.idempotency_key),
        ).fetchone()
        if saved is not None:
            if saved["request_digest"] != digest:
                raise ValueError("managed_message_key_conflict")
            return _receipt(saved, duplicate=True)
        target = conn.execute(
            "SELECT acp_session_id FROM sessions WHERE id=?", (session_id,),
        ).fetchone()
        if target is None:
            raise KeyError(session_id)
        queue_id = conn.execute(
            "INSERT INTO pending_prompts (session_id,caller_id,prompt,created_at) VALUES (?,?,?,?)",
            (session_id, message.caller_id, message.prompt, time.time()),
        ).lastrowid
        conn.execute(
            "INSERT INTO managed_message_receipts "
            "(queue_id,session_id,idempotency_key,request_digest,native_session_id,state) "
            "VALUES (?,?,?,?,?,'queued')",
            (queue_id, session_id, message.idempotency_key, digest,
             message.expected_native_session_id or target["acp_session_id"]),
        )
        row = conn.execute(
            "SELECT * FROM managed_message_receipts WHERE queue_id=?", (queue_id,),
        ).fetchone()
        assert row is not None
        return _receipt(row, duplicate=False)


def consume(
    conn: sqlite3.Connection, session_id: str, queue_id: int, turn_index: int,
    native_session_id: str | None,
) -> bool:
    """Fence generation and journal handoff in the queue-to-turn transaction."""
    row = conn.execute(
        "SELECT native_session_id FROM managed_message_receipts "
        "WHERE session_id=? AND queue_id=? AND state='queued'", (session_id, queue_id),
    ).fetchone()
    if row is None:
        return True
    target = conn.execute("SELECT acp_session_id FROM sessions WHERE id=?", (session_id,)).fetchone()
    mismatch = row["native_session_id"] is not None and (
        row["native_session_id"] != native_session_id
        or target is None or row["native_session_id"] != target["acp_session_id"]
    )
    conn.execute(
        "UPDATE managed_message_receipts SET state=?,turn_index=? WHERE queue_id=?",
        ("target_changed" if mismatch else "handoff_committed",
         None if mismatch else turn_index, queue_id),
    )
    return not mismatch


def removed(conn: sqlite3.Connection, session_id: str, queue_id: int | None = None) -> None:
    """Keep terminal original-key evidence when an operator removes queued input."""
    conn.execute(
        "UPDATE managed_message_receipts SET state='removed' WHERE session_id=? AND state='queued'"
        + (" AND queue_id=?" if queue_id is not None else ""),
        (session_id,) if queue_id is None else (session_id, queue_id),
    )


async def drain_ready(manager: SessionManager) -> None:
    """Heartbeat backstop for a response lost after commit but before its wake."""
    from .session_status import SessionStatus

    if manager.is_draining:
        return
    for session in list(manager._sessions.values()):
        if (session.status != SessionStatus.IDLE
            or not session.client or not session.client.is_running):
            continue
        if manager.db.execute_read(
            "SELECT 1 FROM managed_message_receipts WHERE session_id=? AND state='queued' LIMIT 1",
            (session.session_id,),
        ):
            await manager._kick_pending_drain(session, allow_resume=False)
