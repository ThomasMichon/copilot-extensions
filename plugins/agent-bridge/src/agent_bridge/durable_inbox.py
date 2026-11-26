"""Producer acceptance in the existing inbox, before a receiver owns delivery."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .db_live_session_aliases import CANONICAL_SESSION_SQL
from .message_contracts import TYPE_VERSIONS, producer_contract
from .runtime_custody import RuntimeCustodyError, encode

if TYPE_CHECKING:
    from .live_message_enqueue import LiveMessageEnqueue

_KNOWN_TYPES = ",".join(f"'{kind}'" for kind in TYPE_VERSIONS)
LEGACY_SUPPORTED_SQL = (
    "(CASE WHEN producer_target_json IS NULL THEN 1 "
    "WHEN json_valid(message_contract_json) THEN "
    "json_extract(message_contract_json,'$.base_version')=1 "
    "AND json_extract(message_contract_json,'$.type')=kind "
    "AND json_extract(message_contract_json,'$.type_version')=1 "
    "AND json_extract(message_contract_json,'$.requires')='[]' "
    f"AND (kind IN ({_KNOWN_TYPES}) OR kind GLOB 'startup:prompt:[1-9]*') "
    "ELSE 0 END)"
)


def original_target(request: LiveMessageEnqueue) -> str:
    return encode({
        "version": 1, "session_id": request.session_id,
        "expected_session_id": request.expected_session_id,
        "exact_session": request.exact_session,
    })


def enqueue(
    conn: sqlite3.Connection, request: LiveMessageEnqueue,
) -> tuple[int | None, str | None]:
    """Commit valid input without observing a frontend, epoch or consumer."""
    if request.idempotency_key is not None and not request.idempotency_key.strip():
        raise RuntimeCustodyError("A supplied operation key must be nonempty")
    target_id = request.session_id
    if request.exact_session:
        retired = conn.execute(
            "SELECT target_session_id FROM live_session_aliases WHERE alias_session_id=?",
            (target_id,),
        ).fetchone()
        if retired is not None:
            return None, f"superseded:{retired[0]}"
    if not request.exact_session:
        target_id = conn.execute(
            f"SELECT {CANONICAL_SESSION_SQL}", (target_id, target_id),
        ).fetchone()[0]
    target = conn.execute(
        "SELECT session_id FROM live_sessions WHERE session_id=?", (target_id,),
    ).fetchone()
    if target is None:
        return None, "not_found"
    expected = request.expected_session_id
    if expected is not None and not request.exact_session:
        expected = conn.execute(
            f"SELECT {CANONICAL_SESSION_SQL}", (expected, expected),
        ).fetchone()[0]
    if expected is not None and expected != target_id:
        return None, f"expected_mismatch:{target_id}"
    cur = conn.execute(
        "INSERT INTO live_messages "
        "(session_id,sender,body,reply_to,kind,delivery,idempotency_key,created_at,"
        "consumption_tracked,exact_session,message_contract_json,producer_target_json) "
        "VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
        (
            target_id, request.sender, request.body, request.reply_to, request.kind,
            request.delivery, request.idempotency_key, request.now, request.exact_session,
            producer_contract(
                request.kind, type_version=request.message_version,
                requires=request.message_requires,
            ),
            original_target(request),
        ),
    )
    return int(cur.lastrowid or 0), None


def schema_triggers() -> dict[str, tuple[str, str]]:
    immutable = (
        "producer_target_json", "session_id", "exact_session", "sender", "body",
        "reply_to", "kind", "delivery", "idempotency_key", "created_at",
        "message_contract_json",
    )
    changed = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in immutable)
    return {
        "live_messages_producer_retained": (
            "live_messages",
            "CREATE TRIGGER IF NOT EXISTS live_messages_producer_retained "
            "BEFORE DELETE ON live_messages WHEN OLD.producer_target_json IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT,'bridge_producer_input_retained'); END",
        ),
        "live_messages_producer_immutable": (
            "live_messages",
            "CREATE TRIGGER IF NOT EXISTS live_messages_producer_immutable BEFORE UPDATE OF "
            + ",".join(immutable) + " ON live_messages "
            "WHEN OLD.producer_target_json IS NOT NULL AND (" + changed + ") "
            "BEGIN SELECT RAISE(ABORT,'bridge_producer_input_immutable'); END",
        ),
    }
