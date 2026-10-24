"""Durable enrollment contracts, not actual extension/native qualification."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import threading

import pytest

from agent_bridge.db import Database
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.inbox_consumer_schema import definitions, validate_schema
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeInstallation, RuntimeProducer


@pytest.fixture
def enrolled(tmp_db):
    owner = RuntimeInstallation(
        "installation-a", "source-a", str(tmp_db.db_path.parent), str(tmp_db.db_path.absolute()),
    )
    epoch = tmp_db.initialize_runtime_epoch(owner)
    admission = tmp_db.enroll_runtime_producer(owner, epoch.epoch, RuntimeProducer("a", 101, "birth"))
    consumer = InboxConsumer("native-a", "consumer-a", 102, 1.0)
    register(tmp_db, consumer)
    enroll(tmp_db, owner, admission, consumer)
    return tmp_db, owner, admission, consumer


def register(db, consumer, **kwargs):
    worktree_id = kwargs.pop("worktree_id", None)
    assert db.register_live_session(
        consumer.session_id, machine="machine-a", cwd=".", worktree_id=worktree_id, repo="repo-a",
        branch=None, pid=consumer.pid, role=None, now=10.0,
        process_started_at=consumer.process_started_at, **kwargs,
    ) == "live"


def enroll(db, owner, admission, consumer, expected=None):
    db.enroll_inbox_consumer(
        owner, admission, consumer, expected_incarnation=expected, now=11.0,
    )


def message(db, owner, admission, consumer):
    db.enqueue_live_message(
        consumer.session_id, "sender", "original body", 10.0, runtime_admission=admission,
    )
    row, = db.poll_live_message_handoffs(owner, admission, consumer, now=11.0)
    return row


def test_reload_cas_retires_original_and_exact_retry_recovers_lost_reply(enrolled):
    db, owner, admission, consumer = enrolled
    original = message(*enrolled)
    before = db.runtime_epoch(owner)
    successor = replace(consumer, incarnation="consumer-b")
    enroll(db, owner, admission, successor, consumer.incarnation)
    after = db.runtime_epoch(owner)
    assert after.revision > before.revision
    enroll(db, owner, admission, successor, consumer.incarnation)
    assert db.runtime_epoch(owner) == after
    for old_expected in (None, successor.incarnation):
        with pytest.raises(RuntimeCustodyError, match="retired or reused"):
            enroll(db, owner, admission, consumer, old_expected)
    with pytest.raises(RuntimeCustodyError, match="current enrolled"):
        db.begin_live_message_handoff(
            owner, admission, consumer, original["id"], original["handoff"]["id"], now=12.0,
        )
    with pytest.raises(RuntimeCustodyError, match="observations changed"):
        db.poll_live_message_handoffs(owner, admission, successor, now=12.0)
    retained, = db.execute_read("SELECT * FROM live_messages")
    assert retained["body"] == "original body" and retained["handoff_json"] == original["handoff_json"]
    assert retained["handoff_started_at"] is None


def test_reload_before_poll_keeps_unoffered_messages_and_uses_new_incarnation(enrolled):
    db, owner, admission, consumer = enrolled
    original_id = db.enqueue_live_message(
        consumer.session_id, "sender", "original body", 10.0, runtime_admission=admission,
    )
    successor = replace(consumer, incarnation="consumer-b")
    enroll(db, owner, admission, successor, consumer.incarnation)
    fresh, = db.poll_live_message_handoffs(owner, admission, successor, now=12.0)
    assert fresh["id"] == original_id and fresh["body"] == "original body"
    assert fresh["handoff"]["consumer"] == successor.document()


def test_legacy_heartbeat_does_not_enroll_or_resurrect_retired_consumer(enrolled):
    db, owner, admission, consumer = enrolled
    successor = replace(consumer, incarnation="consumer-b")
    enroll(db, owner, admission, successor, consumer.incarnation)
    register(db, consumer)
    with pytest.raises(RuntimeCustodyError, match="current enrolled"):
        db.poll_live_message_handoffs(owner, admission, consumer, now=12.0)
    other = replace(consumer, session_id="legacy", incarnation="legacy")
    register(db, other)
    with pytest.raises(RuntimeCustodyError, match="current enrolled"):
        db.poll_live_message_handoffs(owner, admission, other, now=12.0)


@pytest.mark.parametrize("mutation", [
    "DELETE FROM live_sessions",
    "UPDATE live_sessions SET status='expired'",
    "UPDATE live_sessions SET session_id='native-b'",
    "UPDATE live_sessions SET pid=103",
    "UPDATE live_sessions SET process_started_at=2",
    "UPDATE live_sessions SET process_started_at=NULL",
    "UPDATE live_sessions SET machine='machine-b'",
    "UPDATE live_sessions SET venue='remote'",
])
def test_registration_retirement_preserves_original_outcome_after_restart(enrolled, mutation):
    db, owner, admission, consumer = enrolled
    row = message(*enrolled)
    assert db.begin_live_message_handoff(
        owner, admission, consumer, row["id"], row["handoff"]["id"], now=12.0,
    )
    db.execute_write(mutation)
    assert db.execute_read("SELECT current FROM bridge_inbox_consumers")[0][0] == 0
    reopened = Database(db.db_path)
    try:
        assert reopened.record_live_message_handoff(
            consumer, row["id"], row["handoff"]["id"], outcome="sdk_accepted", now=13.0,
        ) == "sdk_accepted"
        assert reopened.record_live_message_handoff(
            consumer, row["id"], row["handoff"]["id"], outcome="consumed", now=14.0,
        ) == "consumed"
        history, = reopened.execute_read("SELECT * FROM live_messages")
        assert history["body"] == "original body" and history["handoff_json"] == row["handoff_json"]
    finally:
        reopened.close()


def test_deleted_registration_cannot_silently_reenroll_old_incarnation(enrolled):
    db, owner, admission, consumer = enrolled
    db.deregister_live_session(consumer.session_id)
    register(db, consumer)
    with pytest.raises(RuntimeCustodyError, match="retired or reused"):
        enroll(*enrolled)
    enroll(db, owner, admission, replace(consumer, incarnation="new"))


@pytest.mark.parametrize("rounding", [-5e-7, 5e-7])
def test_tolerated_heartbeat_birth_rounding_keeps_current_enrollment(enrolled, rounding):
    db, owner, admission, consumer = enrolled
    row = message(*enrolled)
    before = db.runtime_epoch(owner)
    register(db, replace(consumer, process_started_at=consumer.process_started_at + rounding))
    enroll(*enrolled)
    assert db.runtime_epoch(owner) == before
    assert db.execute_read("SELECT current FROM bridge_inbox_consumers")[0][0] == 1
    assert db.poll_live_message_handoffs(owner, admission, consumer, now=12.0)[0]["id"] == row["id"]
    assert db.begin_live_message_handoff(
        owner, admission, consumer, row["id"], row["handoff"]["id"], now=12.0,
    )


def test_concurrent_reload_cas_has_one_winner_and_retains_loser_as_unenrolled(enrolled):
    db, owner, admission, consumer = enrolled
    other = Database(db.db_path)
    def replace_current(selected, incarnation):
        try:
            enroll(selected, owner, admission, replace(consumer, incarnation=incarnation),
                   consumer.incarnation)
            return incarnation
        except RuntimeCustodyError as exc:
            assert "enrollment changed" in str(exc)
            return None
        finally:
            selected._get_conn().close()
            selected._local.conn = None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(replace_current, db, "b"), pool.submit(replace_current, other, "c")]
            results = [item.result(timeout=10) for item in futures]
        winner, = [item for item in results if item is not None]
        rows = db.execute_read("SELECT incarnation,current FROM bridge_inbox_consumers")
        assert {tuple(row) for row in rows} == {(consumer.incarnation, 0), (winner, 1)}
    finally:
        other.close()


@pytest.mark.parametrize("winner", ["begin", "reload"])
def test_final_admission_and_reload_have_one_durable_serial_order(enrolled, winner):
    db, owner, admission, consumer = enrolled
    row = message(*enrolled)
    other = Database(db.db_path)
    successor = replace(consumer, incarnation="consumer-b")
    entered, release = threading.Event(), threading.Event()
    timeouts = []

    def operate(selected, action, pause=False):
        conn = selected._get_conn()
        def trace(statement):
            if pause and statement == "COMMIT":
                entered.set()
                if not release.wait(5):
                    timeouts.append(True)
        conn.set_trace_callback(trace)
        try:
            if action == "reload":
                return enroll(selected, owner, admission, successor, consumer.incarnation)
            return selected.begin_live_message_handoff(
                owner, admission, consumer, row["id"], row["handoff"]["id"], now=12.0,
            )
        finally:
            conn.set_trace_callback(None)
            conn.close()
            selected._local.conn = None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(operate, db, winner, True)
            assert entered.wait(5)
            second = pool.submit(operate, other, "reload" if winner == "begin" else "begin")
            release.set()
            first.result(timeout=10)
            if winner == "reload":
                with pytest.raises(RuntimeCustodyError, match="current enrolled"):
                    second.result(timeout=10)
            else:
                second.result(timeout=10)
            assert not timeouts
        history, = db.execute_read("SELECT * FROM live_messages")
        assert history["handoff_json"] == row["handoff_json"]
        assert (history["handoff_started_at"] is not None) == (winner == "begin")
        if winner == "begin":
            assert db.record_live_message_handoff(
                consumer, row["id"], row["handoff"]["id"], outcome="unknown", now=13.0,
            ) == "unknown"
    finally:
        release.set()
        other.close()


def test_actual_same_process_alias_fold_retires_old_enrollment_without_inheriting_it(enrolled):
    db, owner, admission, consumer = enrolled
    row = message(*enrolled)
    db.execute_write("UPDATE live_sessions SET worktree_id='worktree-a',cli_mode=1")
    successor = replace(consumer, session_id="native-b", incarnation="consumer-b")
    register(db, successor, worktree_id="worktree-a")
    assert db.resolve_live_session_id(consumer.session_id) == successor.session_id
    assert db.execute_read("SELECT current FROM bridge_inbox_consumers")[0][0] == 0
    enroll(db, owner, admission, successor)
    with pytest.raises(RuntimeCustodyError, match="registration changed"):
        db.begin_live_message_handoff(
            owner, admission, consumer, row["id"], row["handoff"]["id"], now=12.0,
        )
    with pytest.raises(RuntimeCustodyError, match="observations changed"):
        db.poll_live_message_handoffs(owner, admission, successor, now=12.0)
    history, = db.execute_read("SELECT * FROM live_messages")
    assert history["body"] == "original body" and history["handoff_json"] == row["handoff_json"]


@pytest.mark.parametrize("damage", [
    "DROP TABLE bridge_inbox_consumers",
    "DROP INDEX bridge_inbox_consumers_current",
    "DROP TRIGGER live_sessions_retire_inbox_consumer_delete",
])
def test_prepared_storage_never_repairs_missing_enrollment_history(enrolled, damage):
    db, _, _, _ = enrolled
    passive = Database(db.db_path, read_only=True)
    try:
        db.execute_write(damage)
        before = "\n".join(db._get_conn().iterdump())
        for readonly in (True, False):
            with pytest.raises(RuntimeCustodyError, match="writable preparation"):
                Database(db.db_path, read_only=readonly)
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            open_existing_writable(passive)
        assert "\n".join(db._get_conn().iterdump()) == before
    finally:
        passive.close()


def test_v30_migration_preserves_handoff_and_leaves_legacy_consumer_unenrolled(enrolled):
    db, owner, admission, consumer = enrolled
    original = message(*enrolled)
    # The prior schema had the same inbox owner but no enrollment storage.
    for name, (kind, _table, _) in reversed(list(definitions().items())):
        db.execute_write(f"DROP {kind} {name}")
    db.execute_write("UPDATE schema_version SET version=30")
    other = Database(db.db_path)
    try:
        validate_schema(other._get_conn())
        assert other.execute_read("SELECT * FROM bridge_inbox_consumers") == []
        row, = other.execute_read("SELECT * FROM live_messages")
        assert row["handoff_json"] == original["handoff_json"] and row["body"] == "original body"
        with pytest.raises(RuntimeCustodyError, match="current enrolled"):
            other.poll_live_message_handoffs(owner, admission, consumer, now=12.0)
        with pytest.raises(RuntimeCustodyError, match="Historical inbox incarnation"):
            enroll(other, owner, admission, consumer)
    finally:
        other.close()


@pytest.mark.parametrize("mutation", [
    "DELETE FROM bridge_inbox_consumers",
    "UPDATE bridge_inbox_consumers SET incarnation='b'",
    "UPDATE bridge_inbox_consumers SET current=1",
])
def test_retired_history_is_immutable(enrolled, mutation):
    db, _, _, _ = enrolled
    db.execute_write("UPDATE bridge_inbox_consumers SET current=0")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute_write(mutation)
    row, = db.execute_read("SELECT * FROM bridge_inbox_consumers")
    assert row["current"] == 0 and json.loads(row["consumer_json"]) == enrolled[3].document()


@pytest.mark.parametrize("change", [
    {"session_id": "retired-native"}, {"pid": 103}, {"process_started_at": 2.0},
])
def test_registration_mismatch_refuses_without_changing_current_owner(enrolled, change):
    db, owner, admission, consumer = enrolled
    with pytest.raises(RuntimeCustodyError, match="registration changed"):
        enroll(db, owner, admission, replace(consumer, incarnation="new", **change),
               consumer.incarnation)
    assert [tuple(row) for row in db.execute_read(
        "SELECT incarnation,current FROM bridge_inbox_consumers",
    )] == [(consumer.incarnation, 1)]


def test_foreign_epoch_installation_and_remote_enrollment_refuse(enrolled):
    db, owner, admission, consumer = enrolled
    for foreign_owner, foreign_admission in (
        (replace(owner, identity="foreign"), admission),
        (owner, replace(admission, epoch=admission.epoch + 1)),
    ):
        with pytest.raises(RuntimeCustodyError):
            enroll(db, foreign_owner, foreign_admission, replace(consumer, incarnation="new"),
                   consumer.incarnation)
    remote = replace(consumer, session_id="remote", incarnation="remote")
    register(db, remote, venue="remote-venue")
    with pytest.raises(RuntimeCustodyError, match="registration changed"):
        enroll(db, owner, admission, remote)
    assert len(db.execute_read("SELECT * FROM bridge_inbox_consumers")) == 1
