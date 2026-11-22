"""Bounded native submission attempts for an original retained handoff.

The original ``handoff_started_at`` is attempt one. A started record that was
never marked submitted may be granted again to the current enrolled consumer
while that consumer is idle; every grant is recorded here. Once the bound is
reached the original row receives an explicit terminal ``failed`` disposition
instead of lingering in an ambiguous started state.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .runtime_custody import RuntimeCustodyError, _normalized_schema, encode

TABLE = "live_message_attempts"
MAX_ATTEMPTS = 3
EXHAUSTED = "delivery_attempts_exhausted"
_SQL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} ("
    "message_id INTEGER NOT NULL, attempt INTEGER NOT NULL CHECK(attempt>=2), "
    "session_id TEXT NOT NULL, incarnation TEXT NOT NULL, at REAL NOT NULL, "
    "PRIMARY KEY(message_id,attempt))"
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare the additive attempt ledger during writable preparation only."""
    conn.execute(_SQL)


def available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT type,sql FROM sqlite_master WHERE name=?", (TABLE,),
    ).fetchone()
    return (
        row is not None and row[0] == "table" and isinstance(row[1], str)
        and _normalized_schema(row[1]) == _normalized_schema(_SQL)
    )


def require_schema(conn: sqlite3.Connection) -> None:
    if not available(conn):
        raise RuntimeCustodyError(
            "Retained re-attempt ledger is unavailable; writable preparation is required"
        )


def count(conn: sqlite3.Connection, message_id: int) -> int:
    """Return completed grants, counting the original start as attempt one."""
    if not available(conn):
        return 1
    row = conn.execute(
        f"SELECT MAX(attempt) FROM {TABLE} WHERE message_id=?", (message_id,),
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 1


def regrant(
    conn: sqlite3.Connection, row: sqlite3.Row, consumer: dict[str, Any],
    header: dict[str, Any], expected: int, *, now: float,
) -> bool:
    """Grant one more attempt, or terminally fail the original once bounded."""
    require_schema(conn)
    current = count(conn, row["id"])
    if expected != current:
        return False
    if current >= MAX_ATTEMPTS:
        conn.execute(
            "UPDATE live_messages SET disposition_json=?, "
            "outcome=CASE WHEN outcome IS NULL THEN 'unknown' ELSE outcome END WHERE id=?",
            (encode({
                "state": "failed", "code": EXHAUSTED,
                "reason": f"Native submission was not confirmed after {current} attempts.",
                "at": now, "attempts": current, "receiver": consumer,
                "message": header,
            }), row["id"]),
        )
        return False
    conn.execute(
        f"INSERT INTO {TABLE}(message_id,attempt,session_id,incarnation,at) VALUES(?,?,?,?,?)",
        (row["id"], current + 1, row["session_id"], consumer["incarnation"], now),
    )
    conn.execute("UPDATE live_messages SET outcome=NULL WHERE id=?", (row["id"],))
    return True


def failures(conn: sqlite3.Connection, session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Visible terminal failures for one session, newest first and bounded."""
    if not available(conn):
        return []
    rows = conn.execute(
        "SELECT m.id,m.kind,m.delivery,m.created_at,m.handoff_started_at,"
        "json_extract(m.disposition_json,'$.at') AS failed_at,"
        "json_extract(m.disposition_json,'$.attempts') AS attempts "
        f"FROM live_messages m WHERE m.id IN (SELECT DISTINCT message_id FROM {TABLE} "
        "WHERE session_id=?) AND m.session_id=? AND m.consumed_at IS NULL "
        "AND json_extract(m.disposition_json,'$.code')=? ORDER BY m.id DESC LIMIT ?",
        (session_id, session_id, EXHAUSTED, limit),
    ).fetchall()
    return [{
        "message_id": row["id"], "stage": "failed", "code": EXHAUSTED,
        "kind": row["kind"][:128], "delivery": row["delivery"],
        "admitted_at": row["created_at"], "handoff_started_at": row["handoff_started_at"],
        "failed_at": row["failed_at"], "attempts": row["attempts"],
    } for row in rows]
