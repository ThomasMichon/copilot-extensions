"""Stable rejection envelopes over original queue rows, not a second inbox."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any

from .inbox_custody import InboxConsumer, observed_time
from .runtime_custody import RuntimeCustodyError, _normalized_schema, encode

BASE_VERSION = 1
TYPE_VERSIONS = {
    "prompt": 1, "notify": 1, "status-check": 1, "reply": 1,
    "control:replay-history": 1, "control:plugin-readiness": 1,
    "control:set-mode": 1, "control:set-mode-ready": 1, "control:answer-question": 1,
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add nullable metadata without rewriting old payload or effect history."""
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(live_messages)")}
        for column in ("message_contract_json", "disposition_json"):
            if column not in columns:
                conn.execute(f"ALTER TABLE live_messages ADD COLUMN {column} TEXT")
        if owns_transaction:
            conn.commit()
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise


def producer_contract(kind: str, *, type_version: int | None = None, requires: tuple[str, ...] = ()) -> str:
    """Stamp known producer contracts; zero explicitly means unversioned unknown."""
    if type_version is not None and (type(type_version) is not int or type_version < 0):
        raise RuntimeCustodyError("Message revision must be a nonnegative integer")
    if len(requires) > 32 or any(
        not isinstance(item, str) or not item or len(item) > 128 for item in requires
    ):
        raise RuntimeCustodyError("Message requirements must be bounded nonempty names")
    version = TYPE_VERSIONS.get(kind, 0)
    if re.fullmatch(r"startup:prompt:[1-9][0-9]*", kind):
        version = 1
    return encode({
        "base_version": BASE_VERSION, "type": kind,
        "type_version": version if type_version is None else type_version,
        "requires": list(requires),
    })


def disposition_guards() -> dict[str, str]:
    """Keep terminal audit safe from legacy cleanup and mutation paths."""
    immutable = (
        "session_id", "exact_session", "sender", "body", "reply_to", "kind", "delivery",
        "idempotency_key", "created_at", "claimed_at", "message_contract_json",
    )
    changed = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in immutable)
    return {
        "live_messages_disposition_retained":
            "CREATE TRIGGER IF NOT EXISTS live_messages_disposition_retained "
            "BEFORE DELETE ON live_messages WHEN OLD.disposition_json IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT,'bridge_message_disposition_retained'); END",
        "live_messages_disposition_input":
            "CREATE TRIGGER IF NOT EXISTS live_messages_disposition_input BEFORE UPDATE OF "
            + ",".join(immutable) + " ON live_messages WHEN OLD.disposition_json IS NOT NULL AND ("
            + changed + ") BEGIN SELECT RAISE(ABORT,'bridge_message_disposition_input'); END",
        "live_messages_disposition_sdk":
            "CREATE TRIGGER IF NOT EXISTS live_messages_disposition_sdk "
            "BEFORE UPDATE OF delivered_at,consumed_at,handoff_started_at ON live_messages "
            "WHEN OLD.disposition_json IS NOT NULL AND ("
            "NEW.delivered_at IS NOT OLD.delivered_at OR NEW.consumed_at IS NOT OLD.consumed_at "
            "OR NEW.handoff_started_at IS NOT OLD.handoff_started_at) "
            "BEGIN SELECT RAISE(ABORT,'bridge_message_disposition_not_sdk'); END",
        "live_messages_disposition_terminal":
            "CREATE TRIGGER IF NOT EXISTS live_messages_disposition_terminal "
            "BEFORE UPDATE OF disposition_json ON live_messages "
            "WHEN OLD.disposition_json IS NOT NULL AND COALESCE(("
            "json_extract(OLD.disposition_json,'$.state')='started' AND "
            "json_extract(NEW.disposition_json,'$.state') IN ('completed','unknown') AND "
            "json_extract(NEW.disposition_json,'$.receiver')=json_extract(OLD.disposition_json,'$.receiver') AND "
            "json_extract(NEW.disposition_json,'$.message')=json_extract(OLD.disposition_json,'$.message')),0)=0 "
            "BEGIN SELECT RAISE(ABORT,'bridge_message_disposition_terminal'); END",
    }


def ensure_guards(conn: sqlite3.Connection) -> None:
    """Install guards only after the full owning inbox schema exists."""
    for sql in disposition_guards().values():
        conn.execute(sql)


def validate_schema(conn: sqlite3.Connection) -> None:
    """Require prepared metadata and guards before passive writable adoption."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(live_messages)")}
    if not {"message_contract_json", "disposition_json"} <= columns:
        raise RuntimeCustodyError("Versioned messages require writable schema preparation")
    installed = dict(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='live_messages'",
    ))
    if any(_normalized_schema(installed.get(name, "")) != _normalized_schema(sql)
           for name, sql in disposition_guards().items()):
        raise RuntimeCustodyError("Versioned message audit guards are unprepared or changed")


def prepare_schema(conn: sqlite3.Connection) -> None:
    """Prepare versioned metadata after the existing inbox owner schema is ready."""
    ensure_schema(conn)
    ensure_guards(conn)
    validate_schema(conn)


def envelope(conn: sqlite3.Connection, row: sqlite3.Row, consumer: InboxConsumer) -> dict[str, Any]:
    """Interpret only the trusted correlated outer row, never execute a payload."""
    raw = row["message_contract_json"]
    if raw is None:
        value = json.loads(producer_contract(row["kind"]))
        # The historical replay schema was an empty body; do not guess a new
        # payload meaning for an unversioned nonempty replay command.
        if row["kind"] == "control:replay-history" and row["body"] != "":
            value["type_version"] = 0
        value["legacy"] = True
        raw = encode(value)
        conn.execute(
            "UPDATE live_messages SET message_contract_json=? "
            "WHERE id=? AND message_contract_json IS NULL", (raw, row["id"]),
        )
    try:
        value = json.loads(raw)
        if (
            not isinstance(value, dict) or type(value.get("base_version")) is not int
            or type(value.get("type_version")) is not int
            or value.get("type") != row["kind"]
            or not isinstance(value.get("requires"), list)
            or any(not isinstance(item, str) or not item for item in value["requires"])
        ):
            raise ValueError("Malformed contract")
    except (ValueError, TypeError):
        value = {
            "base_version": BASE_VERSION, "type": row["kind"], "type_version": 0,
            "requires": [], "contract_error": "invalid_contract",
        }
    registration = conn.execute(
        "SELECT worktree_id FROM live_sessions WHERE session_id=?", (consumer.session_id,),
    ).fetchone()
    return {
        **value, "message_id": row["id"], "sender": row["sender"],
        "session_id": row["session_id"], "worktree_id": registration["worktree_id"],
        "receiver_incarnation": consumer.incarnation,
    }


def pending_controls(
    conn: sqlite3.Connection, consumer: InboxConsumer, limit: int, *, now: float,
) -> list[dict[str, Any]]:
    """Offer original unstarted controls to a versioned reader, oldest first."""
    rows = conn.execute(
        "SELECT * FROM live_messages WHERE session_id=? AND kind LIKE 'control:%' "
        "AND kind NOT IN ('control:plugin-readiness','control:answer-question') AND delivered_at IS NULL "
        "AND claimed_at IS NULL AND handoff_started_at IS NULL AND consumed_at IS NULL "
        "AND outcome IS NULL AND disposition_json IS NULL ORDER BY id LIMIT ?",
        (consumer.session_id, limit),
    ).fetchall()
    result = []
    for row in rows:
        header = envelope(conn, row, consumer)
        if consumer.message_base != BASE_VERSION:
            dispose(conn, consumer, row["id"], "rejected", "unsupported_reader",
                    "The enrolled reader does not support versioned retained controls.", now=now)
            continue
        result.append({
            **dict(row), "message_contract": header, "control": True,
            "handoff_stage": "offered", "consumer": consumer.document(),
        })
    return result


def dispose(
    conn: sqlite3.Connection, consumer: InboxConsumer, message_id: int,
    state: str, code: str, reason: str, *, now: float,
) -> dict[str, Any]:
    """Retain a pre-effect rejection or the original one-shot control outcome."""
    if state not in ("started", "completed", "unknown", "rejected"):
        raise RuntimeCustodyError("Unsupported message disposition")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        raise RuntimeCustodyError("Disposition requires a bounded machine code")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise RuntimeCustodyError("Disposition requires a bounded human reason")
    observed_time(now)
    row = conn.execute(
        "SELECT * FROM live_messages WHERE id=? AND session_id=?", (message_id, consumer.session_id),
    ).fetchone()
    if row is None:
        raise RuntimeCustodyError("Original message is unavailable")
    previous: dict[str, Any] | None = json.loads(row["disposition_json"]) if row["disposition_json"] else None
    if previous is not None:
        if previous["receiver"]["incarnation"] != consumer.incarnation:
            raise RuntimeCustodyError("Original disposition belongs to another receiver")
        if previous["state"] == state and previous["code"] == code and previous["reason"] == reason:
            return {**previous, "begin": False} if state == "started" else previous
        if previous["state"] != "started" or state not in ("completed", "unknown"):
            raise RuntimeCustodyError("Original message disposition is terminal")
    else:
        if state not in ("started", "rejected"):
            raise RuntimeCustodyError("Control outcome requires original start admission")
        if (
            row["claimed_at"] is not None or row["handoff_started_at"] is not None
            or row["delivered_at"] is not None or row["consumed_at"] is not None
            or row["outcome"] is not None
        ):
            raise RuntimeCustodyError("Message may already have effects; rejection or replay refused")
    header = previous["message"] if previous is not None else envelope(conn, row, consumer)
    modes = ("interactive", "plan", "autopilot")
    control_supported = row["kind"] == "control:replay-history" and row["body"] == ""
    if row["kind"] == "control:set-mode":
        control_supported = row["body"].strip() in modes
    elif row["kind"] == "control:set-mode-ready":
        try:
            payload = json.loads(row["body"])
        except (ValueError, TypeError):
            payload = None
        control_supported = (
            isinstance(payload, dict) and payload.get("mode") in modes
            and isinstance(payload.get("plugin_readiness"), dict)
        )
    if state == "started" and (
        consumer.message_base != BASE_VERSION or header["base_version"] != BASE_VERSION
        or header["type"] != row["kind"] or header["type_version"] != 1
        or header.get("requires") != [] or not control_supported
        or header.get("contract_error") or row["delivery"] not in ("queue", "steer")
    ):
        raise RuntimeCustodyError("Only supported original history or mode controls may start")
    if state in ("started", "completed", "unknown") and not control_supported:
        raise RuntimeCustodyError("This message has no retained control executor")
    disposition = {
        "state": state, "code": code, "reason": reason, "at": now,
        "receiver": previous["receiver"] if previous is not None else consumer.document(), "message": header,
    }
    if state == "rejected" and not row["kind"].startswith("control:") and row["handoff_json"] is None:
        conn.execute(
            "UPDATE live_messages SET handoff_json=? WHERE id=?",
            (encode({
                "version": 1, "id": uuid.uuid4().hex, "message_id": message_id,
                "consumer": consumer.document(), "admission": row["runtime_custody_json"],
            }), message_id),
        )
    # Older retained readers understand only the conservative unknown fence.
    # The separate disposition carries the actual proved pre-effect rejection.
    conn.execute(
        "UPDATE live_messages SET disposition_json=?, "
        "claimed_at=CASE WHEN ? IN ('started','rejected') THEN ? ELSE claimed_at END, "
        "outcome=CASE WHEN ?='rejected' THEN 'unknown' "
        "WHEN ?='completed' THEN 'applied' WHEN ?='unknown' THEN 'unknown' ELSE outcome END "
        "WHERE id=?",
        (encode(disposition), state, now, state, state, state, message_id),
    )
    return {**disposition, "begin": True} if state == "started" else disposition


def require_supported(header: dict[str, Any], kind: str) -> None:
    """Enforce understood semantics before final effect admission."""
    expected = TYPE_VERSIONS.get(kind)
    if re.fullmatch(r"startup:prompt:[1-9][0-9]*", kind):
        expected = 1
    if (
        header.get("base_version") != BASE_VERSION or header.get("type") != kind
        or expected is None or header.get("type_version") != expected
        or header.get("requires") != [] or header.get("contract_error")
    ):
        raise RuntimeCustodyError("Unsupported message contract; reject before effects")


def receipt_disposition(receipt: dict[str, Any], raw: str | None) -> None:
    """Project the original disposition without inferring delivery or consumption."""
    if raw is not None:
        receipt["disposition"] = json.loads(raw)
        receipt["stage"] = {
            "rejected": "rejected", "started": "control_started",
            "completed": "control_completed", "unknown": "unknown",
            # Older receipt readers know only the conservative unknown stage;
            # the disposition carries the explicit terminal failure code.
            "failed": "unknown",
        }[receipt["disposition"]["state"]]


def project_offer(row: dict[str, Any]) -> dict[str, Any]:
    """Keep trusted outer correlation serializable when payload syntax is unknown."""
    header = row.get("message_contract")
    if header is not None and (
        not isinstance(row["body"], str) or row["delivery"] not in ("queue", "steer", "interrupt")
    ):
        return {
            **row, "body": "", "delivery": "queue",
            "message_contract": {**header, "contract_error": "unsupported_payload"},
        }
    return row
