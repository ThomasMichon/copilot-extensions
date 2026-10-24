"""Migration-free database resource acquisition for a fenced candidate."""

from __future__ import annotations

import sqlite3

from .db import Database, SCHEMA_VERSION
from .runtime_custody import validate_schema
from .inbox_custody import validate_schema as validate_inbox_schema
from .inbox_consumer_schema import validate_schema as validate_consumer_schema
from .turn_custody_schema import validate_schema as validate_turn_schema


def open_existing_writable(source: Database) -> Database:
    """Open the prepared existing WAL database, without creating or migrating it.

    The caller owns active-route authorization. This performs no writes, starts
    no writer, and grants no serving readiness. SQLite lock contention refuses
    immediately rather than blocking the receiver's routing fence.
    """
    if (
        type(source) is not Database or source.read_only is not True
        or source._read_only_file_id is None
        or source._writer_thread is not None
        or source._event_write_q.unfinished_tasks
    ):
        raise ValueError("Writable adoption requires a passive database owner")
    before = source.db_path.stat()
    identity = (before.st_dev, before.st_ino)
    if identity != source._read_only_file_id:
        raise ValueError("Prepared database was replaced")
    conn = sqlite3.connect(
        source.db_path.absolute().as_uri() + "?mode=rw",
        uri=True, timeout=0, check_same_thread=False,
    )
    candidate = None
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN")
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        if len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
            raise ValueError(
                "Writable adoption requires the prepared schema; "
                "supported writable preparation or migration is needed"
            )
        validate_schema(conn)
        validate_inbox_schema(conn)
        validate_consumer_schema(conn)
        validate_turn_schema(conn)
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise ValueError("Writable adoption requires an existing WAL database")
        conn.rollback()
        # Construct only through the read-only owner: no mkdir, bootstrap or
        # migration is permitted on this path, even for an existing database.
        candidate = Database(source.db_path, read_only=True)
        after = source.db_path.stat()
        if (
            (after.st_dev, after.st_ino) != identity
            or candidate._read_only_file_id != identity
        ):
            raise ValueError("Prepared database changed during writable adoption")
        readonly = candidate._local.conn
        readonly.close()
        candidate._local.conn = conn
        candidate._read_only = False
        return candidate
    except BaseException:
        try:
            if candidate is not None:
                candidate.close()
        finally:
            conn.close()
        raise
