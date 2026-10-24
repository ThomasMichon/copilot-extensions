"""DB owner contracts only: no authenticated consumer or actual SDK qualification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeInstallation, RuntimeProducer


@pytest.fixture
def inbox(tmp_db):
    owner = RuntimeInstallation(
        "installation-a", "source-a", str(tmp_db.db_path.parent), str(tmp_db.db_path.absolute()),
    )
    epoch = tmp_db.initialize_runtime_epoch(owner)
    producer = RuntimeProducer("frontend-a", 101, "birth-a")
    admission = tmp_db.enroll_runtime_producer(owner, epoch.epoch, producer)
    consumer = InboxConsumer("native-a", "consumer-a", 102, 1.0)
    register(tmp_db, consumer)
    return tmp_db, owner, producer, admission, consumer


def register(db, consumer, *, now=10.0):
    assert db.register_live_session(
        consumer.session_id, machine="machine-a", cwd=".", worktree_id=None,
        repo="repo-a", branch=None, pid=consumer.pid, role=None, now=now,
        process_started_at=consumer.process_started_at,
    ) == "live"


def enqueue(inbox, *, associated=True):
    db, _, _, admission, consumer = inbox
    return db.enqueue_live_message(
        consumer.session_id, "sender", "original body", 10.0,
        runtime_admission=admission if associated else None,
    )


def poll(inbox):
    db, owner, _, admission, consumer = inbox
    return db.poll_live_message_handoffs(owner, admission, consumer, now=11.0)


def begin(inbox, row):
    db, owner, _, admission, consumer = inbox
    return db.begin_live_message_handoff(
        owner, admission, consumer, row["id"], row["handoff"]["id"], now=12.0,
    )


def close(inbox, *, db=None):
    original, owner, producer, _, _ = inbox
    db = db or original
    return db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="transition-a",
        target_assembly="assembly-a", predecessor=producer,
    )


def outcome(inbox, row, value, *, now=13.0):
    db, _, _, _, consumer = inbox
    return db.record_live_message_handoff(
        consumer, row["id"], row["handoff"]["id"], outcome=value, now=now,
    )


def test_poll_retains_original_attempt_before_body_and_never_grants_replay(inbox):
    db, owner, _, _, consumer = inbox
    message_id = enqueue(inbox)
    before = db.runtime_epoch(owner)
    row, = poll(inbox)
    assert row["id"] == message_id and row["body"] == "original body"
    stored = db.execute_read("SELECT handoff_json FROM live_messages WHERE id=?", (message_id,))[0]
    assert json.loads(stored["handoff_json"]) == row["handoff"]
    assert row["handoff"]["consumer"] == consumer.document()
    assert poll(inbox)[0]["handoff"] == row["handoff"]
    assert db.runtime_epoch(owner).revision > before.revision
    assert begin(inbox, row) is True
    assert begin(inbox, row) is False
    assert poll(inbox)[0]["handoff_stage"] == "sending"
    assert db.list_pending_live_messages(consumer.session_id) == []
    assert db.ack_live_messages(consumer.session_id, [message_id], 14.0) == 0
    assert db.consume_live_message(consumer.session_id, message_id, 14.0) == 0
    assert poll(inbox)[0]["handoff_stage"] == "sending"


def test_restart_and_unknown_sdk_outcome_keep_the_same_attempt(inbox):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    assert begin(inbox, row)
    other = Database(db.db_path)
    try:
        retained, = other.poll_live_message_handoffs(owner, admission, consumer, now=13.0)
        assert retained["handoff"] == row["handoff"] and retained["handoff_stage"] == "sending"
        assert other.begin_live_message_handoff(
            owner, admission, consumer, row["id"], row["handoff"]["id"], now=13.0,
        ) is False
        assert outcome(inbox, row, "unknown") == "unknown"
        assert poll(inbox)[0]["handoff_stage"] == "unknown"
        assert begin(inbox, row) is False
    finally:
        other.close()


@pytest.mark.parametrize("first", ["sdk_accepted", "consumed"])
def test_acceptance_consumption_and_late_reports_remain_distinct_after_closure(inbox, first):
    db, owner, _, _, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    assert begin(inbox, row)
    close(inbox)
    assert outcome(inbox, row, first) == first
    stored = db.live_message_receipt(consumer.session_id, row["id"])
    assert stored["sdk_accepted_at"] == (13.0 if first == "sdk_accepted" else None)
    assert stored["consumed_at"] == (13.0 if first == "consumed" else None)
    second = "consumed" if first == "sdk_accepted" else "sdk_accepted"
    assert outcome(inbox, row, second, now=14.0) == "consumed"
    assert outcome(inbox, row, first, now=15.0) == "consumed"
    assert outcome(inbox, row, "unknown", now=16.0) == "consumed"
    report = db.runtime_custody_inventory(owner)
    retained, = report["rows"]["live_messages"]
    assert retained["id"] == row["id"] and retained["handoff_json"] == row["handoff_json"]
    assert report["restore_qualified"] is False and report["acknowledged"] is False
    assert "inbox/control SDK handoffs" in report["unmediated_cohorts"]


def test_closure_before_final_admission_refuses_and_retains_original_body(inbox):
    db, _, _, _, _ = inbox
    enqueue(inbox)
    row, = poll(inbox)
    close(inbox)
    with pytest.raises(RuntimeCustodyError, match="original open epoch"):
        begin(inbox, row)
    with pytest.raises(RuntimeCustodyError, match="final admission"):
        outcome(inbox, row, "sdk_accepted")
    original = db.execute_read("SELECT * FROM live_messages WHERE id=?", (row["id"],))[0]
    assert original["body"] == "original body" and original["handoff_started_at"] is None


@pytest.mark.parametrize("method", ["ordinary", "fresh"])
@pytest.mark.parametrize("associated", [False, True])
def test_closed_epoch_fences_actual_inbox_insertions(inbox, method, associated):
    db, _, _, admission, consumer = inbox
    close(inbox)
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        if method == "ordinary":
            enqueue(inbox, associated=associated)
        else:
            db.enqueue_live_message_if_fresh(
                consumer.session_id, sender="sender", body="late", now=11.0,
                runtime_admission=admission if associated else None,
            )
    assert db.execute_read("SELECT id FROM live_messages") == []


def test_legacy_and_partially_claimed_batches_roll_back_without_fabricating_custody(inbox):
    db, _, _, _, _ = inbox
    associated = enqueue(inbox)
    legacy = enqueue(inbox, associated=False)
    with pytest.raises(RuntimeCustodyError, match="Legacy or foreign"):
        poll(inbox)
    assert [row["handoff_json"] for row in db.execute_read("SELECT * FROM live_messages")] == [None, None]
    assert [row["id"] for row in db.execute_read("SELECT id FROM live_messages ORDER BY id")] == [
        associated, legacy,
    ]
    assert [row["id"] for row in db.list_pending_live_messages("native-a")] == [legacy]


@pytest.mark.parametrize("change", [
    {"incarnation": "consumer-b"}, {"pid": 103}, {"process_started_at": 2.0},
    {"session_id": "native-b"},
])
def test_wrong_consumer_never_inherits_original_attempt(inbox, change):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    other = replace(consumer, **change)
    with pytest.raises(RuntimeCustodyError):
        db.begin_live_message_handoff(
            owner, admission, other, row["id"], row["handoff"]["id"], now=12.0,
        )
    assert begin(inbox, row)
    with pytest.raises(RuntimeCustodyError, match="observations changed"):
        db.record_live_message_handoff(
            other, row["id"], row["handoff"]["id"], outcome="sdk_accepted", now=13.0,
        )


def test_missing_expired_and_aliased_registration_cannot_mint_final_authority(inbox):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='native-a'")
    with pytest.raises(RuntimeCustodyError, match="registration changed"):
        begin(inbox, row)
    register(db, consumer)
    db.execute_write("INSERT INTO live_session_aliases VALUES ('native-a','native-b',12)")
    db.execute_write("UPDATE live_sessions SET session_id='native-b' WHERE session_id='native-a'")
    db.execute_write("UPDATE live_messages SET session_id='native-b' WHERE session_id='native-a'")
    successor = replace(consumer, session_id="native-b")
    with pytest.raises(RuntimeCustodyError, match="observations changed"):
        db.begin_live_message_handoff(
            owner, admission, successor, row["id"], row["handoff"]["id"], now=12.0,
        )
    with pytest.raises(RuntimeCustodyError, match="registration changed"):
        begin(inbox, row)


@pytest.mark.parametrize("cleanup", ["deregister", "takeover", "reap"])
def test_registration_cleanup_preserves_associated_and_handed_off_rows(inbox, cleanup):
    db, owner, _, _, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    assert begin(inbox, row)
    second_id = enqueue(inbox)
    if cleanup == "deregister":
        assert db.deregister_live_session(consumer.session_id)
    elif cleanup == "takeover":
        db.execute_write("UPDATE live_sessions SET worktree_id='worktree-a'")
        assert db.expire_live_sessions_for_worktree("worktree-a", now=14.0) == 1
    else:
        db.execute_write("UPDATE live_sessions SET status='expired'")
        db.reap_stale_live_sessions(now=1000000.0, pid_alive=lambda _: False)
    rows = db.runtime_custody_inventory(owner)["rows"]["live_messages"]
    assert {item["id"] for item in rows} == {row["id"], second_id}
    assert outcome(inbox, row, "sdk_accepted") == "sdk_accepted"
    assert db.list_pending_live_messages(consumer.session_id) == []


@pytest.mark.parametrize("sql", [
    "DELETE FROM live_messages",
    "UPDATE live_messages SET handoff_json=NULL",
    "UPDATE live_messages SET runtime_custody_json=NULL",
    "UPDATE live_messages SET handoff_started_at=NULL",
    "UPDATE live_messages SET body='replacement input'",
])
def test_original_history_and_final_attempt_cannot_be_reset(inbox, sql):
    db, _, _, _, _ = inbox
    enqueue(inbox)
    row, = poll(inbox)
    assert begin(inbox, row)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute_write(sql)
    assert begin(inbox, row) is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1, True])
def test_invalid_observation_time_cannot_leave_a_replayable_attempt(inbox, value):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    with pytest.raises(RuntimeCustodyError, match="observation time"):
        db.begin_live_message_handoff(
            owner, admission, consumer, row["id"], row["handoff"]["id"], now=value,
        )
    assert poll(inbox)[0]["handoff_stage"] == "offered"


@pytest.mark.parametrize("winner", ["begin", "closure"])
def test_real_two_owner_sqlite_final_admission_and_closure_serialize(inbox, winner):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    other = Database(db.db_path)
    entered, release = threading.Event(), threading.Event()
    timeouts = []

    def operate(selected, action, *, pause=False):
        conn = selected._get_conn()
        def trace(statement):
            if pause and statement == "COMMIT":
                entered.set()
                if not release.wait(5):
                    timeouts.append(True)
        conn.set_trace_callback(trace)
        try:
            if action == "closure":
                return close(inbox, db=selected)
            return selected.begin_live_message_handoff(
                owner, admission, consumer, row["id"], row["handoff"]["id"], now=12.0,
            )
        finally:
            conn.set_trace_callback(None)
            conn.close()
            selected._local.conn = None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(operate, db, winner, pause=True)
            assert entered.wait(5)
            second = pool.submit(operate, other, "closure" if winner == "begin" else "begin")
            release.set()
            first.result(timeout=5)
            if winner == "closure":
                with pytest.raises(RuntimeCustodyError, match="original open epoch"):
                    second.result(timeout=5)
            else:
                second.result(timeout=5)
            assert not timeouts
        retained = db.runtime_custody_inventory(owner)["rows"]["live_messages"][0]
        assert (retained["handoff_started_at"] is not None) == (winner == "begin")
        assert retained["handoff_json"] == row["handoff_json"]
    finally:
        release.set()
        other.close()


@pytest.mark.parametrize("damage", ["column", "guard"])
def test_current_schema_adoption_never_repairs_missing_handoff_storage(inbox, damage):
    db, _, _, _, _ = inbox
    if damage == "column":
        db.execute_write("DROP TRIGGER live_messages_handoff_handoff_started_at_monotonic")
        db.execute_write("ALTER TABLE live_messages DROP COLUMN handoff_started_at")
    else:
        db.execute_write("DROP TRIGGER live_messages_handoff_immutable")
    for readonly in (True, False):
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            Database(db.db_path, read_only=readonly)


def test_existing_writable_adoption_preserves_handoff_without_bootstrap(inbox):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    assert begin(inbox, row)
    passive = Database(db.db_path, read_only=True)
    try:
        adopted = open_existing_writable(passive)
        try:
            retained, = adopted.poll_live_message_handoffs(owner, admission, consumer, now=13.0)
            assert retained["handoff"] == row["handoff"] and retained["handoff_stage"] == "sending"
            assert adopted._writer_thread is None
        finally:
            adopted.close()
    finally:
        passive.close()


def test_real_process_exit_after_final_admission_cannot_authorize_a_second_send(inbox):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    row, = poll(inbox)
    child = """
import json, os, sys
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeInstallation, RuntimeAdmission
from agent_bridge.inbox_custody import InboxConsumer
owner = RuntimeInstallation(**json.loads(sys.argv[1]))
admission = RuntimeAdmission(**json.loads(sys.argv[2]))
consumer = InboxConsumer(**json.loads(sys.argv[3]))
db = Database(owner.database)
assert db.begin_live_message_handoff(
    owner, admission, consumer, int(sys.argv[4]), sys.argv[5], now=12.0)
os._exit(0)
"""
    from dataclasses import asdict
    subprocess.run(
        [sys.executable, "-c", child, json.dumps(asdict(owner)), admission.document(),
         json.dumps(consumer.document()), str(row["id"]), row["handoff"]["id"]],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert begin(inbox, row) is False
    assert poll(inbox)[0]["handoff_stage"] == "sending"
    assert db.live_message_receipt(consumer.session_id, row["id"])["stage"] == "admitted"


def test_frozen_legacy_migration_preserves_returned_body_uncertainty(tmp_path):
    path = tmp_path / "legacy.db"
    frozen = Path(__file__).with_name("fixtures") / "pre-custody-schema27.sql"
    with sqlite3.connect(path) as conn:
        conn.executescript(frozen.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO live_messages (session_id,sender,body,created_at) "
            "VALUES ('native-a','sender','possibly already polled',1)",
        )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    db = Database(path)
    try:
        assert db.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION == 30
        row, = db.execute_read("SELECT * FROM live_messages")
        assert row["body"] == "possibly already polled"
        assert row["runtime_custody_json"] is None and row["handoff_json"] is None
        owner = RuntimeInstallation("installation-a", "source-a", str(tmp_path), str(path))
        epoch = db.initialize_runtime_epoch(owner)
        admission = db.enroll_runtime_producer(owner, epoch.epoch, RuntimeProducer("a", 101, "birth"))
        consumer = InboxConsumer("native-a", "consumer-a", 102, 1.0)
        register(db, consumer)
        with pytest.raises(RuntimeCustodyError, match="Legacy or foreign"):
            db.poll_live_message_handoffs(owner, admission, consumer, now=11.0)
        assert any("unassociated:live_messages" in item
                   for item in db.runtime_custody_inventory(owner)["blockers"])
    finally:
        db.close()


def test_legacy_poll_and_lost_ack_cannot_expose_an_associated_body_before_handoff(inbox):
    db, _, _, _, consumer = inbox
    message_id = enqueue(inbox)
    assert db.list_pending_live_messages(consumer.session_id) == []
    assert db.ack_live_messages(consumer.session_id, [message_id], 11.0) == 0
    assert db.consume_live_message(consumer.session_id, message_id, 11.0) == 0
    original = db.execute_read("SELECT * FROM live_messages WHERE id=?", (message_id,))[0]
    assert original["handoff_json"] is None
    assert original["delivered_at"] is None and original["consumed_at"] is None
    row, = poll(inbox)
    assert row["id"] == message_id and begin(inbox, row)


def test_mixed_enrolled_producers_preserve_each_original_association(inbox):
    db, owner, _, admission, consumer = inbox
    first_id = enqueue(inbox)
    replacement = db.enroll_runtime_producer(
        owner, admission.epoch, RuntimeProducer("frontend-b", 103, "birth-b"),
    )
    second_id = db.enqueue_live_message(
        consumer.session_id, "second sender", "second body", 10.0,
        runtime_admission=replacement,
    )
    rows = poll(inbox)
    assert [row["id"] for row in rows] == [first_id, second_id]
    assert [row["handoff"]["admission"] for row in rows] == [
        admission.document(), replacement.document(),
    ]
    repeated = db.poll_live_message_handoffs(owner, replacement, consumer, now=11.0)
    assert [row["handoff"] for row in repeated] == [row["handoff"] for row in rows]
    assert db.begin_live_message_handoff(
        owner, replacement, consumer, first_id, rows[0]["handoff"]["id"], now=12.0,
    )
    assert begin(inbox, rows[0]) is False
    assert begin(inbox, rows[1]) is True


def test_unknown_original_producer_refuses_the_entire_mixed_batch(inbox):
    db, owner, _, admission, consumer = inbox
    enqueue(inbox)
    missing = db.enroll_runtime_producer(
        owner, admission.epoch, RuntimeProducer("frontend-b", 103, "birth-b"),
    )
    db.enqueue_live_message(
        consumer.session_id, "second sender", "second body", 10.0, runtime_admission=missing,
    )
    db.execute_write("DELETE FROM bridge_runtime_producers WHERE id=?", (missing.producer,))
    with pytest.raises(RuntimeCustodyError, match="not enrolled"):
        poll(inbox)
    assert [row["handoff_json"] for row in db.execute_read("SELECT * FROM live_messages")] == [None, None]


@pytest.mark.parametrize("original,retry", [
    ("legacy", "first"), ("first", "legacy"), ("first", "second"), ("first", "first"),
])
def test_keyed_retry_requires_the_same_original_custody_association(inbox, original, retry):
    db, owner, _, admission, consumer = inbox
    second = db.enroll_runtime_producer(
        owner, admission.epoch, RuntimeProducer("frontend-b", 103, "birth-b"),
    )
    admissions = {"legacy": None, "first": admission, "second": second}
    def keyed(value):
        return db.enqueue_live_message_if_fresh(
            consumer.session_id, sender="sender", body="same body", now=10.0,
            idempotency_key="original-key", report_duplicate=True,
            runtime_admission=admissions[value],
        )
    original_id, reason = keyed(original)
    assert original_id is not None and reason is None
    replay_id, reason = keyed(retry)
    if original == retry:
        assert replay_id == original_id and reason == "duplicate"
    else:
        assert replay_id is None and reason == "idempotency_conflict"
    row, = db.execute_read("SELECT * FROM live_messages")
    expected = admissions[original]
    assert row["runtime_custody_json"] == (expected.document() if expected else None)


@pytest.mark.parametrize("adopted", [False, True])
def test_custody_commit_is_full_sync_and_ordinary_writes_keep_normal_policy(inbox, adopted):
    original, owner, producer, admission, consumer = inbox
    passive = Database(original.db_path, read_only=True) if adopted else None
    db = open_existing_writable(passive) if passive else original
    selected = (db, owner, producer, admission, consumer)
    conn = db._get_conn()
    observed = []
    def trace(statement):
        if statement == "COMMIT":
            observed.append(conn.execute("PRAGMA synchronous").fetchone()[0])
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    conn.set_trace_callback(trace)
    try:
        enqueue(selected)
        row, = poll(selected)
        assert begin(selected, row)
        close(selected)
        assert outcome(selected, row, "sdk_accepted") == "sdk_accepted"
        db.execute_write("UPDATE live_sessions SET latest_progress='ordinary metadata'")
        assert observed == [2, 2, 2, 1, 2, 2, 1]
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        with pytest.raises(RuntimeCustodyError, match="observations changed"):
            db.record_live_message_handoff(
                replace(consumer, incarnation="not-the-original"), row["id"],
                row["handoff"]["id"], outcome="consumed", now=14.0,
            )
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    finally:
        conn.set_trace_callback(None)
        if adopted:
            db.close()
            passive.close()


@pytest.mark.parametrize("method", ["ordinary", "fresh"])
@pytest.mark.parametrize("kind", ["control:mode", "CONTROL:MODE"])
def test_associated_controls_refuse_before_either_enqueue_can_admit(inbox, method, kind):
    db, _, _, admission, consumer = inbox
    with pytest.raises(RuntimeCustodyError, match="qualified control handoff"):
        if method == "ordinary":
            db.enqueue_live_message(
                consumer.session_id, "sender", "control body", 10.0,
                kind=kind, runtime_admission=admission,
            )
        else:
            db.enqueue_live_message_if_fresh(
                consumer.session_id, sender="sender", body="control body", now=10.0,
                kind=kind, runtime_admission=admission,
            )
    assert db.execute_read("SELECT id FROM live_messages") == []


@pytest.mark.parametrize("kind", ["control:mode", "CONTROL:MODE"])
def test_sql_owner_cannot_admit_an_associated_control(inbox, kind):
    db, _, _, admission, consumer = inbox
    with pytest.raises(sqlite3.IntegrityError, match="control_custody_unsupported"):
        db.execute_write(
            "INSERT INTO live_messages "
            "(session_id,sender,body,kind,created_at,runtime_custody_json) VALUES (?,?,?,?,?,?)",
            (consumer.session_id, "sender", "control body", kind, 10.0, admission.document()),
        )
    assert db.execute_read("SELECT id FROM live_messages") == []


def test_associated_prompt_cannot_be_reclassified_as_a_legacy_control(inbox):
    db, _, _, _, _ = inbox
    message_id = enqueue(inbox)
    with pytest.raises(sqlite3.IntegrityError, match="control_custody_unsupported"):
        db.execute_write("UPDATE live_messages SET kind='control:mode' WHERE id=?", (message_id,))
    assert db.execute_read("SELECT kind FROM live_messages")[0]["kind"] == "prompt"


@pytest.mark.parametrize("claimed", [False, True])
def test_legacy_controls_cannot_settle_unsupported_retained_custody(inbox, claimed):
    db, _, _, _, consumer = inbox
    message_id = enqueue(inbox)
    ordinary_id = db.enqueue_live_message(
        consumer.session_id, "sender", "ordinary control", 10.0, kind="control:mode",
    )
    # Model an older owner that admitted a control before the new guard existed.
    db.execute_write("DROP TRIGGER live_messages_control_update_unsupported")
    db.execute_write(
        "UPDATE live_messages SET kind='control:mode',claimed_at=? WHERE id=?",
        (9.0 if claimed else None, message_id),
    )
    close(inbox)
    with pytest.raises(RuntimeCustodyError, match="qualified control handoff"):
        db.claim_live_controls(consumer.session_id, 100.0, 1.0)
    assert db.ack_live_messages(consumer.session_id, [message_id], 100.0, controls=True) == 0
    assert not db.withdraw_live_control(consumer.session_id, message_id, 100.0)
    rows = db.execute_read("SELECT id,claimed_at,delivered_at,outcome FROM live_messages ORDER BY id")
    assert [row["id"] for row in rows] == [message_id, ordinary_id]
    assert all(row["delivered_at"] is None and row["outcome"] is None for row in rows)
    assert rows[1]["claimed_at"] is None
