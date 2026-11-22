"""Bounded native submission attempts for an original retained handoff.

The original ``handoff_started_at`` is attempt one. A started record that was
never marked submitted may be granted again to the current enrolled consumer
while that consumer is idle; every grant is recorded here. Once the bound is
reached the original row receives an explicit terminal ``failed`` disposition
instead of lingering in an ambiguous started state. Late acceptance or
consumption evidence for an exhausted original is kept on its final attempt so
the receiver's report settles while the terminal disposition stays unchanged.
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
    "late_outcome TEXT CHECK(late_outcome IS NULL OR late_outcome IN ('sdk_accepted','consumed')), "
    "late_at REAL, PRIMARY KEY(message_id,attempt))"
)
INDEX = "live_message_attempts_session"
_INDEX_SQL = f"CREATE INDEX IF NOT EXISTS {INDEX} ON {TABLE}(session_id,message_id)"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare the additive attempt ledger during writable preparation only."""
    conn.execute(_SQL)
    # Pending-queue failure reads are per session; never scan the whole ledger.
    conn.execute(_INDEX_SQL)


def available(conn: sqlite3.Connection) -> bool:
    rows = dict(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE (name=? AND type='table') "
        "OR (name=? AND type='index' AND tbl_name=?)", (TABLE, INDEX, TABLE),
    ).fetchall())
    return (
        isinstance(rows.get(TABLE), str) and isinstance(rows.get(INDEX), str)
        and _normalized_schema(rows[TABLE]) == _normalized_schema(_SQL)
        and _normalized_schema(rows[INDEX]) == _normalized_schema(_INDEX_SQL)
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


def late_stage(conn: sqlite3.Connection, message_id: int) -> str | None:
    """Late SDK evidence observed after the original was exhausted, if any."""
    if not available(conn):
        return None
    row = conn.execute(
        f"SELECT late_outcome FROM {TABLE} WHERE message_id=? ORDER BY attempt DESC LIMIT 1",
        (message_id,),
    ).fetchone()
    return row[0] if row is not None else None


def record_late(conn: sqlite3.Connection, message_id: int, outcome: str, *, now: float) -> str | None:
    """Record late acceptance/consumption for an exhausted original; consumed wins."""
    if outcome not in ("sdk_accepted", "consumed") or not available(conn):
        return late_stage(conn, message_id)
    conn.execute(
        f"UPDATE {TABLE} SET late_outcome=?, late_at=? WHERE message_id=? "
        f"AND attempt=(SELECT MAX(attempt) FROM {TABLE} WHERE message_id=?) "
        "AND (late_outcome IS NULL OR (late_outcome='sdk_accepted' AND ?='consumed'))",
        (outcome, now, message_id, message_id, outcome),
    )
    return late_stage(conn, message_id)


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
        "AND m.delivered_at IS NULL "
        f"AND (SELECT late_outcome FROM {TABLE} a WHERE a.message_id=m.id "
        "ORDER BY a.attempt DESC LIMIT 1) IS NULL "
        "AND json_extract(m.disposition_json,'$.code')=? ORDER BY m.id DESC LIMIT ?",
        (session_id, session_id, EXHAUSTED, limit),
    ).fetchall()
    return [{
        "message_id": row["id"], "stage": "failed", "code": EXHAUSTED,
        "kind": row["kind"][:128], "delivery": row["delivery"],
        "admitted_at": row["created_at"], "handoff_started_at": row["handoff_started_at"],
        "failed_at": row["failed_at"], "attempts": row["attempts"],
    } for row in rows]
