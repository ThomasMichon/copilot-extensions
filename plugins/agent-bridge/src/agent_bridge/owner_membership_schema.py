"""Canonical lookup indexes for bounded, owner-selected membership reads."""

from __future__ import annotations

import sqlite3


def owner_expression(column: str) -> str:
    """Keep persisted lookup keys identical to the reader's owner selection."""
    return f"CASE WHEN instr({column}, '#')>0 THEN substr({column},1,instr({column},'#')-1) ELSE {column} END"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare optional lookup indexes only through ordinary writable bootstrap."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_membership_owner ON sessions ("
        + owner_expression("json_extract(target_json,'$.caller_owner_ref')")
        + ") WHERE json_valid(target_json)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_relationships_membership_owner "
        "ON remote_acp_relationships (" + owner_expression("owner") + ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_sessions_membership_owner ON live_sessions ("
        + owner_expression("json_extract(venue,'$.supervisor_ref')")
        + ") WHERE json_valid(venue)"
    )
