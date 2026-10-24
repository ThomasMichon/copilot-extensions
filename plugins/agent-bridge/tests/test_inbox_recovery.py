"""Read-only original-handoff recovery, not SDK or native qualification."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_inbox_custody import begin, close, enqueue, inbox as inbox, outcome, poll


def test_recovery_retains_original_observations_after_reload_closure_and_reopen(inbox):
    db, owner, _, admission, consumer = inbox
    mid = enqueue(inbox)
    original, = poll(inbox)
    assert begin(inbox, original)
    successor = replace(consumer, incarnation="successor")
    db.enroll_inbox_consumer(
        owner, admission, successor, expected_incarnation=consumer.incarnation, now=12.5,
    )
    close(inbox)
    db.deregister_live_session(consumer.session_id)
    before = db.runtime_epoch(owner)
    passive = Database(db.db_path, read_only=True)
    try:
        expected = {
            "session_id": consumer.session_id, "message_id": mid,
            "handoff_id": original["handoff"]["id"], "consumer": consumer.document(),
            "stage": "sending", "admitted_at": 10.0, "handoff_started_at": 12.0,
            "sdk_accepted_at": None, "consumed_at": None,
        }
        assert passive.live_message_handoff_receipt(consumer.session_id, mid) == expected
        assert passive.inspect_live_handoff_recovery(consumer.session_id, limit=1) == {
            "messages": [expected], "next_after": None,
        }
        assert db.runtime_epoch(owner) == before
        assert outcome(inbox, original, "sdk_accepted") == "sdk_accepted"
        assert passive.live_message_handoff_receipt(consumer.session_id, mid)["stage"] == "sdk_accepted"
        assert outcome(inbox, original, "consumed", now=14.0) == "consumed"
        assert passive.inspect_live_handoff_recovery(consumer.session_id, limit=1) == {
            "messages": [], "next_after": None,
        }
        assert passive.live_message_handoff_receipt(consumer.session_id, mid)["stage"] == "consumed"
        # Reading history never makes the successor the owner of an old send.
        with pytest.raises(RuntimeCustodyError):
            db.record_live_message_handoff(
                successor, mid, expected["handoff_id"], outcome="consumed", now=15.0,
            )
    finally:
        passive.close()


def test_recovery_is_exact_native_scoped_and_does_not_expose_legacy_or_unoffered(inbox):
    db, _, _, _, consumer = inbox
    mid = enqueue(inbox)
    assert db.live_message_handoff_receipt(consumer.session_id, mid) is None
    original, = poll(inbox)
    legacy = enqueue(inbox, associated=False)
    db.execute_write("INSERT INTO live_session_aliases VALUES ('alias','native-a',12)")
    assert db.live_message_handoff_receipt("alias", mid) is None
    assert db.live_message_handoff_receipt("other", mid) is None
    assert db.live_message_handoff_receipt(consumer.session_id, legacy) is None
    found = db.inspect_live_handoff_recovery(consumer.session_id, limit=10)
    assert [item["message_id"] for item in found["messages"]] == [original["id"]]
    assert db.inspect_live_handoff_recovery("alias", limit=10)["messages"] == []
    assert "original body" not in json.dumps(found)


def test_pending_recovery_keyset_pages_skip_consumed_history_without_mutation(inbox):
    db, owner, _, _, consumer = inbox
    for _ in range(7):
        enqueue(inbox)
    originals = poll(inbox)
    for row in originals[:4]:
        assert begin(inbox, row)
        outcome(inbox, row, "consumed")
    before = db.runtime_epoch(owner)
    page = db.inspect_live_handoff_recovery(consumer.session_id, limit=2)
    assert [item["message_id"] for item in page["messages"]] == [
        row["id"] for row in originals[4:6]
    ]
    assert page["next_after"] == originals[5]["id"]
    last = db.inspect_live_handoff_recovery(
        consumer.session_id, limit=2, after=page["next_after"],
    )
    assert [item["message_id"] for item in last["messages"]] == [originals[6]["id"]]
    assert last["next_after"] is None
    assert db.runtime_epoch(owner) == before
    statements = []
    conn = db._get_conn()
    conn.set_trace_callback(statements.append)
    try:
        db.inspect_live_handoff_recovery(consumer.session_id, limit=1)
    finally:
        conn.set_trace_callback(None)
    select, = [sql for sql in statements if "FROM live_messages" in sql]
    assert "SELECT *" not in select and "body" not in select
    plan = conn.execute("EXPLAIN QUERY PLAN " + select).fetchall()
    assert any("live_messages_handoff_pending" in item[3] for item in plan)
    assert not any("TEMP B-TREE" in item[3] for item in plan)


@pytest.mark.parametrize("limit,after", [
    (0, 0), (101, 0), (True, 0), (1, -1), (1, True), (1, 1 << 63),
])
def test_recovery_refuses_unbounded_or_invalid_page_arguments(inbox, limit, after):
    db, _, _, _, consumer = inbox
    with pytest.raises(RuntimeCustodyError):
        db.inspect_live_handoff_recovery(consumer.session_id, limit=limit, after=after)


@pytest.mark.parametrize("field,value", [
    ("message_id", 999), ("admission", "foreign"), ("consumer", {
        "session_id": "foreign", "incarnation": "consumer-a", "pid": 102,
        "process_started_at": 1.0,
    }),
])
def test_recovery_refuses_corrupt_original_association_without_repair(inbox, field, value):
    db, owner, _, _, consumer = inbox
    mid = enqueue(inbox)
    original, = poll(inbox)
    changed = {**original["handoff"], field: value}
    db.execute_write("DROP TRIGGER live_messages_handoff_immutable")
    db.execute_write("UPDATE live_messages SET handoff_json=? WHERE id=?", (json.dumps(changed), mid))
    before = db.runtime_epoch(owner)
    with pytest.raises(RuntimeCustodyError, match="observations changed"):
        db.live_message_handoff_receipt(consumer.session_id, mid)
    with pytest.raises(RuntimeCustodyError, match="observations changed"):
        db.inspect_live_handoff_recovery(consumer.session_id, limit=1)
    assert db.runtime_epoch(owner) == before


def test_empty_recovery_does_not_initialize_runtime_or_consumer(tmp_db):
    assert tmp_db.inspect_live_handoff_recovery("native-a", limit=1) == {
        "messages": [], "next_after": None,
    }
    assert tmp_db.live_message_handoff_receipt("native-a", 1) is None
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    assert tmp_db.execute_read("SELECT * FROM bridge_inbox_consumers") == []


def test_v32_preparation_adds_only_recovery_index_and_preserves_all_rows(inbox):
    db, _, _, _, consumer = inbox
    enqueue(inbox)
    original, = poll(inbox)
    assert begin(inbox, original)
    outcome(inbox, original, "sdk_accepted")
    before = [dict(row) for row in db.execute_read("SELECT * FROM live_messages")]
    consumers = [dict(row) for row in db.execute_read("SELECT * FROM bridge_inbox_consumers")]
    db.execute_write("DROP INDEX IF EXISTS live_messages_handoff_pending")
    db.execute_write("UPDATE schema_version SET version=32")
    with pytest.raises((ValueError, RuntimeCustodyError), match="prepar|schema"):
        Database(db.db_path, read_only=True)
    assert db.execute_read("SELECT version FROM schema_version")[0][0] == 32
    prepared = Database(db.db_path)
    try:
        assert SCHEMA_VERSION >= 33
        assert prepared.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        assert [dict(row) for row in prepared.execute_read("SELECT * FROM live_messages")] == before
        assert [dict(row) for row in prepared.execute_read("SELECT * FROM bridge_inbox_consumers")] == consumers
        assert prepared.inspect_live_handoff_recovery(consumer.session_id, limit=1)["messages"]
    finally:
        prepared.close()


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("damaged", ["missing", "wrong-definition"])
def test_prepared_recovery_index_damage_is_not_silently_repaired(tmp_db, read_only, damaged):
    tmp_db.execute_write("DROP INDEX IF EXISTS live_messages_handoff_pending")
    if damaged == "wrong-definition":
        tmp_db.execute_write(
            "CREATE INDEX live_messages_handoff_pending ON live_messages(session_id)",
        )
    with pytest.raises(RuntimeCustodyError, match="writable preparation"):
        Database(tmp_db.db_path, read_only=read_only)
    rows = tmp_db.execute_read(
        "SELECT sql FROM sqlite_master WHERE name='live_messages_handoff_pending'",
    )
    assert (len(rows) == 0) == (damaged == "missing")
