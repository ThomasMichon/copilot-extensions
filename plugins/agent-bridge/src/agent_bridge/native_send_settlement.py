"""Original native-send settlement; no resume, prompt or lifecycle transition."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .native_send_admission import NativeSendPrompt

if TYPE_CHECKING:
    from .db import Database


def _digest(session_id: str, request: NativeSendPrompt) -> str:
    document = json.dumps(
        {"session_id": session_id, "request": request.model_dump(mode="json")},
        sort_keys=True, allow_nan=False, separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def require_unsettled(db: Database, request: NativeSendPrompt) -> None:
    """Reject an already closed key before ordinary send attempts a resume."""
    if db.execute_read(
        "SELECT 1 FROM native_send_settlements WHERE delivery_id=?",
        (request.delivery_id(),),
    ):
        raise ValueError("native_send_settled")


def settle_record(db: Database, session_id: str, request: NativeSendPrompt) -> dict[str, Any]:
    """Linearize original admission or proved non-admission in the same journal."""
    from .native_keyed_admission import stored_receipt
    from .native_send_history import require_original_history

    digest = _digest(session_id, request)
    receipt = {
        "version": 1, "session_id": session_id,
        "native_session_id": request.expected_native_session_id,
        "invocation_key": request.invocation_key, "queued": False,
        "admission": "not_admitted", "fenced": True,
        "request_digest": digest,
    }
    db.flush()
    with db._runtime_transaction(write=True, full_sync=True) as conn:
        saved = stored_receipt(db, session_id, request, connection=conn)
        row = conn.execute(
            "SELECT * FROM native_send_settlements WHERE delivery_id=?",
            (request.delivery_id(),),
        ).fetchone()
        if saved is not None:
            if row is not None:
                raise ValueError("native_send_history_unproven")
            return saved
        if row is not None:
            if (
                row["session_id"] != session_id
                or row["native_session_id"] != request.expected_native_session_id
                or row["request_digest"] != digest
            ):
                raise ValueError("native_invocation_conflict")
            if json.loads(row["receipt_json"]) != receipt:
                raise ValueError("native_send_history_unproven")
            return receipt
        require_original_history(db, conn, session_id, request)
        conn.execute(
            "INSERT INTO native_send_settlements "
            "(delivery_id,session_id,native_session_id,request_digest,receipt_json) "
            "VALUES (?,?,?,?,?)",
            (request.delivery_id(), session_id, request.expected_native_session_id, digest,
             json.dumps(receipt, sort_keys=True, allow_nan=False)),
        )
        return receipt


async def settle(manager: Any, session_id: str, request: NativeSendPrompt) -> dict[str, Any]:
    """Settle the exact current native holder without starting or interrupting it."""
    from .native_keyed_admission import validate_holder

    session = manager.get_session(session_id)
    if session is None:
        raise KeyError("native_session_unavailable")
    async with session._turn_start_lock:
        validate_holder(manager, session_id, session, request)
        return settle_record(manager.db, session_id, request)
