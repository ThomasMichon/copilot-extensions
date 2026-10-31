"""Real SQLite ordering/reopen controls; no native transport is invoked."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.runtime_custody import RuntimeCustodyError
from agent_bridge.turn_custody_schema import COLUMNS, schema_triggers
from test_native_turn_custody import begin, outcome, prepare
from test_native_turn_custody import native_turn as native_turn
from test_retained_turn_durability import observing_commits, keyed_request
from test_runtime_custody import close, enrolled, session


@pytest.mark.parametrize("adopted", [False, True])
def test_native_turn_custody_uses_full_commits_and_passive_reads(native_turn, adopted) -> None:
    original, owner, producer, admission, consumer = native_turn
    passive = Database(original.db_path, read_only=True) if adopted else None
    db = open_existing_writable(passive) if passive else original
    selected = db, owner, producer, admission, consumer
    try:
        with observing_commits(db) as observed:
            handoff = prepare(selected)
            assert begin(selected, handoff)
            assert outcome(selected, handoff, "unknown") == "unknown"
            assert outcome(selected, handoff, "native_accepted", now=4.0) == "native_accepted"
            assert outcome(selected, handoff, "consumed", now=5.0) == "consumed"
        assert observed == [1, 2, 2, 2, 2]
        assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
        assert db._writer_thread is None
        readonly = Database(db.db_path, read_only=True)
        try:
            policy = readonly._get_conn().execute("PRAGMA synchronous").fetchone()[0]
            with observing_commits(readonly) as reads:
                assert readonly.read_native_turn_handoff(consumer, 0, handoff["id"])["stage"] == "consumed"
            assert reads == [policy]
            assert readonly._writer_thread is None
        finally:
            readonly.close()
    finally:
        if passive:
            db.close()
            passive.close()


def test_real_process_lost_native_begin_reply_cannot_grant_again(native_turn) -> None:
    db, owner, _, admission, consumer = native_turn
    handoff = prepare(native_turn)
    child = """
import json, os, sys
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeInstallation, RuntimeAdmission
from agent_bridge.turn_custody import NativeTurnConsumer
owner = RuntimeInstallation(**json.loads(sys.argv[1]))
db = Database(owner.database)
assert db.begin_native_turn_handoff(
    owner, RuntimeAdmission(**json.loads(sys.argv[2])),
    NativeTurnConsumer(**json.loads(sys.argv[3])), 0, sys.argv[4],
    expected_prompt="original prompt", now=2.0)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", child, json.dumps(asdict(owner)), admission.document(),
         json.dumps(consumer.document()), handoff["id"]],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    reopened = Database(db.db_path)
    try:
        selected = reopened, owner, native_turn[2], admission, consumer
        assert begin(selected, handoff) is False
        assert reopened.read_native_turn_handoff(consumer, 0, handoff["id"])["stage"] == "sending"
        assert outcome(selected, handoff, "unknown") == "unknown"
        assert len(reopened.get_turns("s")) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("winner", ["begin", "closure"])
def test_real_sqlite_native_begin_and_closure_have_one_serial_order(native_turn, winner) -> None:
    db, owner, producer, admission, consumer = native_turn
    handoff = prepare(native_turn)
    other = Database(db.db_path)
    entered, release = threading.Event(), threading.Event()

    def first() -> object:
        conn = db._get_conn()
        timed_out: list[bool] = []

        def trace(statement: str) -> None:
            if statement == "COMMIT" and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    timed_out.append(True)

        conn.set_trace_callback(trace)
        try:
            if winner == "begin":
                result = begin(native_turn, handoff)
            else:
                # Avoid holding the read-only epoch observation instead of the write.
                expected = db._runtime_epoch(conn, owner.document())
                result = close(db, owner, producer, expected=expected)
            assert not timed_out
            return result
        finally:
            conn.set_trace_callback(None)
            conn.close()
            db._local.conn = None

    def second() -> object:
        try:
            if winner == "begin":
                return close(other, owner, producer)
            return begin((other, owner, producer, admission, consumer), handoff)
        finally:
            conn = getattr(other._local, "conn", None)
            if conn is not None:
                conn.close()
                other._local.conn = None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(first)
            assert entered.wait(5)
            waiting = pool.submit(second)
            release.set()
            result = held.result(timeout=10)
            if winner == "begin":
                assert result is True
                try:
                    waiting.result(timeout=10)
                except RuntimeCustodyError as exc:
                    assert "changed before closure" in str(exc)
                    close(other, owner, producer)
                assert db.get_turn("s", 0)["native_handoff_started_at"] == 2.0
            else:
                with pytest.raises(RuntimeCustodyError, match="open epoch"):
                    waiting.result(timeout=10)
                assert db.get_turn("s", 0)["native_handoff_started_at"] is None
        assert db.runtime_epoch(owner).state == "closed"
        assert db.get_turn("s", 0)["native_handoff_json"] is not None
    finally:
        release.set()
        other.close()


@pytest.mark.parametrize("mode", ["readonly", "adopted", "normal"])
def test_damaged_current_native_custody_is_never_repaired(tmp_db, mode) -> None:
    tmp_db.execute_write("DROP TRIGGER turns_native_handoff_immutable")
    with pytest.raises(RuntimeCustodyError, match="writable preparation"):
        if mode == "normal":
            Database(tmp_db.db_path)
        elif mode == "readonly":
            Database(tmp_db.db_path, read_only=True)
        else:
            # Prepare first, then damage the same prepared file before adoption.
            tmp_db.execute_write(schema_triggers()["turns_native_handoff_immutable"])
            passive = Database(tmp_db.db_path, read_only=True)
            tmp_db.execute_write("DROP TRIGGER turns_native_handoff_immutable")
            try:
                open_existing_writable(passive)
            finally:
                passive.close()
    assert tmp_db.execute_read(
        "SELECT 1 FROM sqlite_master WHERE name='turns_native_handoff_immutable'",
    ) == []


def test_frozen_legacy_turn_migration_preserves_unknown_native_history(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    frozen = Path(__file__).with_name("fixtures") / "pre-custody-schema27.sql"
    with sqlite3.connect(path) as conn:
        conn.executescript(frozen.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    db = Database(path)
    try:
        assert db.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        row, = db.get_turns("retained")
        assert row["prompt"] == "original prompt" and row["response_text"] == "original result"
        assert row["acquisition_delivery_id"] == "original-delivery"
        assert row["runtime_custody_json"] is None
        assert all(row[name] is None for name in COLUMNS)
        assert db.peek_pending_prompt("retained")["prompt"] == "retained follow-up"
    finally:
        db.close()


def test_native_handoff_preserves_original_keyed_receipt(tmp_db: Database) -> None:
    from agent_bridge.native_keyed_admission import stored_receipt
    from agent_bridge.turn_custody import NativeTurnConsumer

    owner, _, admission = enrolled(tmp_db)
    session(tmp_db, "s", admission)
    tmp_db.update_session_acp_id("s", "native-a")
    request = keyed_request()
    consumer = NativeTurnConsumer("s", "native-a", "transport-a", 102, "birth-a")
    original = tmp_db.create_turn(
        "s", 0, request.prompt, 1.0, native_admission=request,
        delivery_id=request.delivery_id(), runtime_admission=admission, native_consumer=consumer,
    )
    raw = tmp_db.get_turn("s", 0)["native_admission_json"]
    selected = tmp_db, owner, None, admission, consumer
    handoff = prepare(selected)
    assert begin(selected, handoff)
    assert outcome(selected, handoff, "unknown") == "unknown"
    assert tmp_db.get_turn("s", 0)["native_admission_json"] == raw
    assert stored_receipt(tmp_db, "s", request) == original["receipt"]


@pytest.mark.parametrize("kind", ["direct", "queued", "keyed"])
@pytest.mark.parametrize("closed", [False, True])
def test_new_native_handoff_commits_with_original_turn_and_queue(tmp_db, kind, closed) -> None:
    from agent_bridge.turn_custody import NativeTurnConsumer

    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "s", admission)
    tmp_db.update_session_acp_id("s", "native-a")
    consumer = NativeTurnConsumer("s", "native-a", "transport-a", 102, "birth-a")
    queue_id = tmp_db.enqueue_prompt("s", "original prompt", 1.0) if kind == "queued" else None
    request = keyed_request() if kind == "keyed" else None
    if closed:
        close(tmp_db, owner, producer)

    def insert() -> None:
        tmp_db.create_turn(
            "s", 0, "original prompt", 1.0, runtime_admission=admission, native_consumer=consumer,
            pending_prompt_id=queue_id, native_admission=request,
            delivery_id=request.delivery_id() if request else None,
        )

    with observing_commits(tmp_db) as commits:
        if closed:
            with pytest.raises((sqlite3.IntegrityError, ValueError)):
                insert()
        else:
            insert()
    assert commits == ([] if closed else [2])
    row = tmp_db.get_turn("s", 0)
    assert (row is None) == closed
    if not closed:
        handoff = json.loads(row["native_handoff_json"])
        assert handoff["consumer"] == consumer.document()
        assert handoff["admission"] == admission.document()
    if queue_id is not None:
        assert (tmp_db.peek_pending_prompt("s") is not None) == closed


def test_keyed_history_cannot_acquire_native_handoff_on_retry(tmp_db: Database) -> None:
    from agent_bridge.turn_custody import NativeTurnConsumer

    _, _, admission = enrolled(tmp_db)
    session(tmp_db, "s", admission)
    tmp_db.update_session_acp_id("s", "native-a")
    request = keyed_request()
    tmp_db.create_turn(
        "s", 0, request.prompt, 1.0, native_admission=request,
        delivery_id=request.delivery_id(), runtime_admission=admission,
    )
    consumer = NativeTurnConsumer("s", "native-a", "transport-a", 102, "birth-a")
    with pytest.raises(ValueError, match="history_unproven"):
        tmp_db.create_turn(
            "s", 0, request.prompt, 1.0, native_admission=request,
            delivery_id=request.delivery_id(), runtime_admission=admission, native_consumer=consumer,
        )
    row, = tmp_db.get_turns("s")
    assert row["native_handoff_json"] is None
