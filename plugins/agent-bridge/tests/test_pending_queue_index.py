"""Actual page plans/work counts over large synthetic settled history."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_bridge import compatible_inbox, pending_queue, retained_attempts
from agent_bridge.db import Database
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.live_message_schema import INDEX
from agent_bridge.runtime_custody import RuntimeCustodyError


def _register(db: Database) -> None:
    db.register_live_session(
        "native",
        machine=None,
        cwd=None,
        worktree_id="worktree",
        repo=None,
        branch=None,
        pid=123,
        process_started_at=100,
        role=None,
        now=time.time(),
    )


@pytest.mark.parametrize("operation", ["inspect", "reconcile"])
def test_original_page_uses_partial_id_index_not_settled_history(
    tmp_db: Database,
    operation: str,
) -> None:
    db = tmp_db
    _register(db)
    with db._runtime_transaction(write=True) as conn:
        conn.executemany(
            "INSERT INTO live_messages(session_id,sender,body,created_at,delivered_at,"
            "consumed_at) VALUES ('native','sender','settled',1,2,3)",
            [()] * 10_000,
        )
    ids = [db.enqueue_live_message("native", "sender", "pending", time.time()) for _ in range(3)]
    consumer = InboxConsumer("native", "receiver")
    compatible_inbox.enroll(db, consumer, expected_incarnation=None, now=time.time())
    conn = db._get_conn()
    pages: list[str] = []
    counts: list[int] = []
    active = False

    def progress() -> int:
        counts[-1] += 1
        return 0

    def trace(sql: str) -> None:
        nonlocal active
        if active:
            conn.set_progress_handler(None, 0)
            active = False
        if (
            sql.startswith("SELECT id,")
            and "FROM live_messages WHERE session_id=" in sql
            and "ORDER BY id LIMIT " in sql
        ):
            pages.append(sql)
            counts.append(0)
            conn.set_progress_handler(progress, 1)
            active = True

    conn.set_trace_callback(trace)
    try:
        if operation == "inspect":
            result = pending_queue.inspect(db, "native", "worktree", limit=2)
            assert [row["message_id"] for row in result["messages"]] == ids[:2]
        else:
            assert (
                compatible_inbox.reconcile_offers(
                    db,
                    "native",
                    "worktree",
                    "receiver",
                    limit=2,
                    after=0,
                )
                == ids[:2]
            )
    finally:
        conn.set_trace_callback(None)
        conn.set_progress_handler(None, 0)
    assert len(pages) == 1
    plan = " ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + pages[0]))
    assert INDEX in plan and "TEMP B-TREE" not in plan
    assert counts[0] < 1_000, (operation, counts, plan)


def test_failed_attempts_use_session_index_not_whole_ledger(tmp_db: Database) -> None:
    db = tmp_db
    _register(db)
    with db._runtime_transaction(write=True) as conn:
        assert retained_attempts.available(conn)
        conn.executemany(
            f"INSERT INTO {retained_attempts.TABLE}(message_id,attempt,session_id,incarnation,at) "
            "VALUES (?,2,'other','old',1)",
            [(index,) for index in range(1, 10_001)],
        )
    conn = db._get_conn()
    counts = [0]

    def progress() -> int:
        counts[0] += 1
        return 0

    conn.set_progress_handler(progress, 1)
    try:
        assert retained_attempts.failures(conn, "native") == []
    finally:
        conn.set_progress_handler(None, 0)
    plan = " ".join(
        str(row[3]) for row in conn.execute(
            f"EXPLAIN QUERY PLAN SELECT DISTINCT message_id FROM {retained_attempts.TABLE} "
            "WHERE session_id=?", ("native",),
        )
    )
    assert retained_attempts.INDEX in plan, plan
    assert counts[0] < 3_000, (counts, plan)


def test_existing_read_only_database_refuses_page_until_writable_preparation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "existing.db"
    db = Database(path)
    _register(db)
    mid = db.enqueue_live_message("native", "sender", "original", time.time())
    version = db.execute_read("SELECT version FROM schema_version")[0][0]
    db.execute_write(f"DROP INDEX {INDEX}")
    db.close()
    reader = Database(path, read_only=True)
    try:
        assert reader.get_live_session_exact("native") is not None
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            pending_queue.inspect(reader, "native", "worktree")
    finally:
        reader.close()
    prepared = Database(path)
    try:
        assert prepared.execute_read("SELECT version FROM schema_version")[0][0] == version
        assert (
            pending_queue.inspect(prepared, "native", "worktree")["messages"][0]["message_id"]
            == mid
        )
        row = prepared.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]
        assert row["body"] == "original" and row["handoff_json"] is None
        assert row["delivered_at"] is None and row["consumed_at"] is None
    finally:
        prepared.close()
