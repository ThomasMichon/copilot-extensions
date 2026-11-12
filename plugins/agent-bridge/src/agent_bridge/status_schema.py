"""Additive, rollback-preserving host status storage."""

import sqlite3
import uuid

CAPABILITY = "host_status_feed"
RETENTION = 4096
MAX_SUBSCRIPTIONS = 128

_SQL = """
CREATE TABLE IF NOT EXISTS bridge_status_meta (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bridge_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
    source_key TEXT NOT NULL, session_id TEXT NOT NULL, kind TEXT NOT NULL,
    scope TEXT NOT NULL, observed_at REAL NOT NULL, evidence TEXT NOT NULL,
    UNIQUE(source,source_key)
);
CREATE TABLE IF NOT EXISTS bridge_status_subscriptions (
    id TEXT PRIMARY KEY, generation TEXT NOT NULL, watcher TEXT NOT NULL, selection TEXT NOT NULL,
    acked INTEGER NOT NULL, unread INTEGER NOT NULL,
    notice_epoch INTEGER NOT NULL DEFAULT 0, notice_id INTEGER,
    notice_error TEXT
);
CREATE TABLE IF NOT EXISTS bridge_status_hosts (
    host TEXT PRIMARY KEY, epoch TEXT, cursor INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'unavailable', observed_at REAL NOT NULL DEFAULT 0
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO bridge_status_meta VALUES (1,?)", (uuid.uuid4().hex,),
    )


def validate_schema(conn: sqlite3.Connection) -> None:
    for statement in _SQL.split(";"):
        if not statement.strip():
            continue
        expected = statement.replace("IF NOT EXISTS ", "").strip()
        table = expected.split()[2]
        row = conn.execute("SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone()
        if row is None or "".join(row[0].split()).lower() != "".join(expected.split()).lower():
            raise ValueError("Host status feed requires supported writable schema preparation")
    for table, columns in (
        ("bridge_status_meta", "epoch"),
        ("bridge_status_events", "id,source,source_key,session_id,kind,scope,observed_at,evidence"),
        ("bridge_status_subscriptions", "id,generation,watcher,selection,acked,unread,notice_epoch,notice_id,notice_error"),
        ("bridge_status_hosts", "host,epoch,cursor,availability,observed_at"),
    ):
        conn.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    if conn.execute("SELECT COUNT(*) FROM bridge_status_meta WHERE singleton=1").fetchone()[0] != 1:
        raise ValueError("Host status feed requires writable schema preparation")
