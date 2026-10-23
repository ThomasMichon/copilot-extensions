"""Session deletion serializes custody and rolls back every cleanup statement."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from typing import Any

import pytest

from agent_bridge.db import Database
from agent_bridge.runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer,
)

HISTORY_TABLES = (
    "sessions", "turns", "events", "delivery_cursors", "delivery_cursor_invalidations",
)


def seed(db: Database) -> None:
    db.create_session("s", "original", None, ".", "local", "idle", 1.0)
    db.create_turn("s", 0, "original prompt", 1.0)
    db.append_event("s", 1, "agent_message", {"text": "original"}, 1.0)
    db.flush()
    db.set_cursor("caller", "s", 1, 1.0)
    db.execute_write(
        "INSERT INTO delivery_cursor_invalidations "
        "(caller_id,session_id,prior_last_acked_id,prior_head_id,invalidated_at) "
        "VALUES ('caller','s',0,1,1.0)"
    )


def admission(db: Database) -> RuntimeAdmission:
    owner = RuntimeInstallation("installation-a", "source-a", str(db.db_path.parent),
                                str(db.db_path.absolute()))
    epoch = db.initialize_runtime_epoch(owner)
    return db.enroll_runtime_producer(
        owner, epoch.epoch, RuntimeProducer("frontend-a", 101, "native-birth-a"),
    )


def history(db: Database) -> dict[str, list[dict[str, Any]]]:
    return {
        table: [dict(row) for row in db.execute_read(f"SELECT * FROM {table}")]
        for table in HISTORY_TABLES
    }


def close_thread_connection(db: Database) -> None:
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.set_trace_callback(None)
        conn.close()
        db._local.conn = None


def test_delete_reserves_sqlite_writer_before_custody_read(tmp_db):
    seed(tmp_db)
    association = admission(tmp_db)
    other = Database(tmp_db.db_path)
    observed = threading.Event()
    release = threading.Event()
    transaction_states = []
    timed_out = []

    def deleting():
        conn = tmp_db._get_conn()

        def before_custody_read(statement):
            if " ".join(statement.split()).startswith("SELECT 1 FROM sessions WHERE id="):
                transaction_states.append(conn.in_transaction)
                observed.set()
                if not release.wait(5):
                    timed_out.append(True)

        conn.set_trace_callback(before_custody_read)
        try:
            tmp_db.delete_session("s")
        finally:
            close_thread_connection(tmp_db)

    def foreign_admission():
        conn = other._get_conn()
        conn.execute("PRAGMA busy_timeout=0")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.create_turn("s", 1, "foreign original", 2.0,
                                  runtime_admission=association)
            assert not conn.in_transaction
        finally:
            close_thread_connection(other)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            deleting_future = pool.submit(deleting)
            try:
                assert observed.wait(5)
                pool.submit(foreign_admission).result(timeout=5)
                assert transaction_states == [True]
            finally:
                release.set()
            deleting_future.result(timeout=5)
        assert not timed_out
        assert all(not rows for rows in history(other).values())
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            other.create_turn("s", 1, "foreign original", 2.0,
                              runtime_admission=association)
        assert not other._get_conn().in_transaction
    finally:
        release.set()
        other.close()


def test_foreign_committed_custody_refuses_delete_without_partial_history(tmp_db):
    seed(tmp_db)
    association = admission(tmp_db)
    other = Database(tmp_db.db_path)
    try:
        other.create_turn("s", 1, "foreign original", 2.0, runtime_admission=association)
        before = history(other)
        with pytest.raises(RuntimeCustodyError, match="outcome reconciliation"):
            tmp_db.delete_session("s")
        assert not tmp_db._get_conn().in_transaction
        other._get_conn().execute("PRAGMA busy_timeout=0")
        other.execute_write("UPDATE sessions SET name=name WHERE id='s'")
        tmp_db.execute_write("UPDATE sessions SET name=name WHERE id='s'")
        assert history(other) == before
    finally:
        other.close()


@pytest.mark.parametrize("table", [
    "turns", "delivery_cursors", "delivery_cursor_invalidations", "sessions",
])
def test_cleanup_statement_refusal_rolls_back_history_and_releases_writer(tmp_db, table):
    seed(tmp_db)
    other = Database(tmp_db.db_path)
    try:
        tmp_db.execute_write(
            f"CREATE TRIGGER refuse_cleanup BEFORE DELETE ON {table} "
            "BEGIN SELECT RAISE(ABORT,'cleanup_refused'); END"
        )
        before = history(other)
        with pytest.raises(sqlite3.IntegrityError, match="cleanup_refused"):
            tmp_db.delete_session("s")
        assert not tmp_db._get_conn().in_transaction
        other._get_conn().execute("PRAGMA busy_timeout=0")
        other.execute_write("UPDATE sessions SET name=name WHERE id='s'")
        tmp_db.execute_write("UPDATE sessions SET name=name WHERE id='s'")
        assert history(other) == before
    finally:
        other.close()


def test_late_same_transaction_custody_guard_rolls_back_prior_cleanup(tmp_db):
    seed(tmp_db)
    association = admission(tmp_db)
    other = Database(tmp_db.db_path)
    try:
        encoded = association.document().encode("utf-8").hex()
        tmp_db.execute_write(
            "CREATE TRIGGER inject_custody AFTER DELETE ON events BEGIN "
            "INSERT INTO turns(session_id,turn_index,prompt,runtime_custody_json) "
            f"VALUES ('s',1,'late original',CAST(X'{encoded}' AS TEXT)); END"
        )
        before = history(other)
        with pytest.raises(sqlite3.IntegrityError, match="bridge_runtime_custody_retained"):
            tmp_db.delete_session("s")
        assert not tmp_db._get_conn().in_transaction
        other._get_conn().execute("PRAGMA busy_timeout=0")
        other.execute_write("UPDATE sessions SET name=name WHERE id='s'")
        tmp_db.execute_write("UPDATE sessions SET name=name WHERE id='s'")
        assert history(other) == before
        assert other.get_turn("s", 1) is None
    finally:
        other.close()
