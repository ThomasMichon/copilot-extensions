"""Retained inbox owner observations; not consumer authentication or SDK grants."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import sqlite3

from .runtime_custody import (
    RuntimeCustodyError, _normalized_schema, nonblank, operation_triggers, positive,
)

COLUMNS = {
    "runtime_custody_json": "TEXT",
    "handoff_json": "TEXT",
    "handoff_started_at": "REAL",
}


def observed_time(value: float) -> None:
    """A null/nonfinite timestamp must never reopen a one-shot attempt."""
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise RuntimeCustodyError("Inbox custody requires a finite positive observation time")


@dataclass(frozen=True)
class InboxConsumer:
    """Exact native and consumer-incarnation observations supplied by its owner."""

    session_id: str
    incarnation: str
    pid: int
    process_started_at: float

    def __post_init__(self) -> None:
        nonblank(self.session_id)
        nonblank(self.incarnation)
        positive(self.pid)
        observed_time(self.process_started_at)

    def document(self) -> dict[str, object]:
        return asdict(self)


def decode_handoff(raw: str) -> dict[str, object]:
    """Reject unknown or ambiguous retained handoff formats rather than retry."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeCustodyError("Duplicate inbox handoff field")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or set(value) != {
            "version", "id", "message_id", "consumer", "admission",
        } or type(value["version"]) is not int or value["version"] != 1:
            raise RuntimeCustodyError("Unsupported inbox handoff")
        nonblank(value["id"])
        positive(value["message_id"])
        if type(value["consumer"]) is not dict or type(value["admission"]) is not str:
            raise RuntimeCustodyError("Unsupported inbox handoff observations")
        InboxConsumer(**value["consumer"])
        return value
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeCustodyError("Malformed retained inbox handoff") from exc


def handoff_stage(row: sqlite3.Row | dict) -> str:
    """SDK acceptance, observed consumption and unknown send outcome stay distinct."""
    if row["consumed_at"] is not None:
        return "consumed"
    if row["delivered_at"] is not None:
        return "sdk_accepted"
    if row["outcome"] == "unknown":
        return "unknown"
    return "sending" if row["handoff_started_at"] is not None else "offered"


def schema_triggers() -> dict[str, tuple[str, str]]:
    triggers = operation_triggers(("live_messages",))
    for operation in ("INSERT", "UPDATE"):
        name = f"live_messages_control_{operation.lower()}_unsupported"
        triggers[name] = (
            "live_messages",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON live_messages "
            "WHEN NEW.kind LIKE 'control:%' AND "
            "(NEW.runtime_custody_json IS NOT NULL OR NEW.handoff_json IS NOT NULL) "
            "BEGIN SELECT RAISE(ABORT,'bridge_inbox_control_custody_unsupported'); END",
        )
    triggers["live_messages_handoff_immutable"] = (
        "live_messages",
        "CREATE TRIGGER IF NOT EXISTS live_messages_handoff_immutable "
        "BEFORE UPDATE OF handoff_json ON live_messages "
        "WHEN OLD.handoff_json IS NOT NULL AND NEW.handoff_json IS NOT OLD.handoff_json "
        "BEGIN SELECT RAISE(ABORT,'bridge_inbox_handoff_immutable'); END",
    )
    triggers["live_messages_handoff_retained"] = (
        "live_messages",
        "CREATE TRIGGER IF NOT EXISTS live_messages_handoff_retained "
        "BEFORE DELETE ON live_messages WHEN OLD.handoff_json IS NOT NULL "
        "BEGIN SELECT RAISE(ABORT,'bridge_inbox_handoff_retained'); END",
    )
    triggers["live_messages_handoff_input_immutable"] = (
        "live_messages",
        "CREATE TRIGGER IF NOT EXISTS live_messages_handoff_input_immutable "
        "BEFORE UPDATE OF sender,body,reply_to,kind,delivery,idempotency_key ON live_messages "
        "WHEN OLD.handoff_json IS NOT NULL AND ("
        "NEW.sender IS NOT OLD.sender OR NEW.body IS NOT OLD.body OR "
        "NEW.reply_to IS NOT OLD.reply_to OR NEW.kind IS NOT OLD.kind OR "
        "NEW.delivery IS NOT OLD.delivery OR NEW.idempotency_key IS NOT OLD.idempotency_key) "
        "BEGIN SELECT RAISE(ABORT,'bridge_inbox_handoff_input_immutable'); END",
    )
    for column in ("handoff_started_at", "delivered_at", "consumed_at"):
        name = f"live_messages_handoff_{column}_monotonic"
        triggers[name] = (
            "live_messages",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF {column} ON live_messages "
            f"WHEN OLD.handoff_json IS NOT NULL AND OLD.{column} IS NOT NULL "
            f"AND NEW.{column} IS NOT OLD.{column} "
            "BEGIN SELECT RAISE(ABORT,'bridge_inbox_handoff_outcome_retained'); END",
        )
    return triggers


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare additive inbox storage only through the writable migration owner."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(live_messages)")}
    for name, kind in COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE live_messages ADD COLUMN {name} {kind}")
    for _table, statement in schema_triggers().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    """Require exact prepared custody guards without creating or repairing them."""
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(live_messages)")}
    for name, kind in COLUMNS.items():
        row = columns.get(name)
        if row is None or row[2].upper() != kind or row[3] != 0 or row[5] != 0:
            raise RuntimeCustodyError(f"Inbox custody requires writable preparation: {name}")
    for name, (table, statement) in schema_triggers().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != table or not isinstance(row[2], str)
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Inbox custody requires writable preparation: {name}")
