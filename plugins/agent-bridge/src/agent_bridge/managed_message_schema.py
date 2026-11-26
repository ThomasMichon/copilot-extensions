"""Original managed-message receipts and mixed-version consumer exclusion."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema

TABLE = (
    "CREATE TABLE IF NOT EXISTS managed_message_receipts ("
    "queue_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,"
    "idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL,"
    "native_session_id TEXT, state TEXT NOT NULL, turn_index INTEGER,"
    "UNIQUE(session_id,idempotency_key))"
)


def _declarations() -> dict[str, tuple[str, str, str]]:
    declarations = {"managed_message_receipts": ("table", "managed_message_receipts", TABLE)}
    declarations["idx_managed_message_pending"] = (
        "index", "managed_message_receipts",
        "CREATE INDEX IF NOT EXISTS idx_managed_message_pending "
        "ON managed_message_receipts(session_id,queue_id) WHERE state='queued'",
    )
    declarations["managed_messages_session_ended"] = (
        "trigger", "sessions",
        "CREATE TRIGGER IF NOT EXISTS managed_messages_session_ended "
        "AFTER UPDATE OF status ON sessions WHEN NEW.status='ended' "
        "BEGIN UPDATE managed_message_receipts SET state='removed' "
        "WHERE session_id=NEW.id AND state='queued'; "
        "DELETE FROM pending_prompts WHERE session_id=NEW.id AND id IN "
        "(SELECT queue_id FROM managed_message_receipts WHERE session_id=NEW.id AND state='removed'); END",
    )
    for operation in ("DELETE", "UPDATE"):
        name = f"pending_managed_message_{operation.lower()}"
        condition = " AND state='queued'" if operation == "DELETE" else ""
        declarations[name] = (
            "trigger", "pending_prompts",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON pending_prompts "
            "WHEN EXISTS (SELECT 1 FROM managed_message_receipts "
            f"WHERE queue_id=OLD.id{condition}) "
            "BEGIN SELECT RAISE(ABORT,'managed_message_consumer_required'); END",
        )
    declarations["managed_message_receipt_retained"] = (
        "trigger", "managed_message_receipts",
        "CREATE TRIGGER IF NOT EXISTS managed_message_receipt_retained "
        "BEFORE DELETE ON managed_message_receipts "
        "BEGIN SELECT RAISE(ABORT,'managed_message_receipt_retained'); END",
    )
    declarations["managed_message_receipt_immutable"] = (
        "trigger", "managed_message_receipts",
        "CREATE TRIGGER IF NOT EXISTS managed_message_receipt_immutable "
        "BEFORE UPDATE ON managed_message_receipts "
        "WHEN OLD.state!='queued' OR NEW.queue_id IS NOT OLD.queue_id "
        "OR NEW.session_id IS NOT OLD.session_id OR NEW.idempotency_key IS NOT OLD.idempotency_key "
        "OR NEW.request_digest IS NOT OLD.request_digest "
        "OR NEW.native_session_id IS NOT OLD.native_session_id "
        "OR NEW.state NOT IN ('removed','target_changed','handoff_committed') "
        "BEGIN SELECT RAISE(ABORT,'managed_message_receipt_immutable'); END",
    )
    return declarations


def ensure_schema(conn: sqlite3.Connection) -> None:
    for _, _, statement in _declarations().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    for name, (kind, table, statement) in _declarations().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (row is None or row[0] != kind or row[1] != table
            or not isinstance(row[2], str)
            or _normalized_schema(row[2]) != _normalized_schema(statement)):
            raise RuntimeCustodyError(f"Managed messages require writable preparation: {name}")
