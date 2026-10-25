"""Same-row creation native observations; no native effect or enrollment grant."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from agent_bridge.creation_custody import CreationJournal
from agent_bridge.creation_launch_schema import COLUMNS, schema_triggers
from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_creation_custody import creation_request
from test_retained_turn_durability import observing_commits
from test_runtime_custody import close, enrolled


@pytest.fixture
def native_creation(tmp_db):
    installation, producer, admission = enrolled(tmp_db)
    request = creation_request()
    journal = CreationJournal(tmp_db)
    journal.claim(request, installation, admission)
    journal.begin_allocation(request, installation, admission)
    journal.report_allocation(request, {
        "id": "workspace-a", "repo": "fixture", "path": "/synthetic/workspace",
        "creation_nonce": "allocation-a",
    })
    handoff = journal.prepare_launch(
        request, installation, admission, consumer_incarnation="consumer-a",
    )
    return tmp_db, installation, producer, admission, journal, request, handoff


def test_creation_launch_keeps_all_original_inputs_and_one_unknown(native_creation):
    db, owner, producer, admission, journal, request, handoff = native_creation
    original = journal.read(request)
    assert original["creation_facts_json"] == request.facts_json
    assert original["session_id"] == handoff["consumer"]["session_id"]
    assert original["state"] == "preparing"
    assert journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    assert not journal.begin_launch(request, owner, admission, handoff["id"], now=11)
    close(db, owner, producer)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        journal.begin_launch(request, owner, admission, handoff["id"], now=12)
    passive = Database(db.db_path, read_only=True)
    try:
        report = CreationJournal(passive).read_launch(request)
        assert report["handoff"] == handoff and report["started_at"] == 10
        assert report["result"] is report["binding_started_at"] is None
        assert not passive.execute_read("SELECT * FROM sessions")
        assert not passive.execute_read("SELECT * FROM acquisition_attempts")
    finally:
        passive.close()


def test_creation_native_and_binding_replies_are_immutable_after_closure(native_creation):
    db, owner, producer, admission, journal, request, handoff = native_creation
    assert journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
    assert journal.begin_binding(request, owner, admission, handoff["id"], now=12)
    assert not journal.begin_binding(request, owner, admission, handoff["id"], now=13)
    close(db, owner, producer)
    report = journal.report_binding(request, handoff["id"], now=14)
    assert report["binding_accepted_at"] == 14
    assert journal.report_binding(request, handoff["id"], now=15) == report
    assert journal.report_launch(
        request, handoff["id"], native_session_id="native-a", now=16,
    ) == report
    with pytest.raises(RuntimeCustodyError, match="cannot be replaced"):
        journal.report_launch(request, handoff["id"], native_session_id="native-b", now=17)
    assert journal.read(request)["state"] == "bound"


def test_creation_native_acceptance_can_be_observed_after_closed_reply(native_creation):
    db, owner, producer, admission, journal, request, handoff = native_creation
    assert journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    close(db, owner, producer)
    report = journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
    assert report["result"] == {"native_session_id": "native-a", "accepted_at": 11}
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        journal.begin_binding(request, owner, admission, handoff["id"], now=12)
    assert journal.read_launch(request) == report


def test_creation_launch_prepare_replay_is_observation_not_new_owner(native_creation):
    db, owner, producer, admission, journal, request, handoff = native_creation
    close(db, owner, producer)
    before = db.runtime_epoch(owner)
    assert journal.prepare_launch(
        request, owner, admission, consumer_incarnation="consumer-a",
    ) == handoff
    assert db.runtime_epoch(owner) == before
    with pytest.raises(RuntimeCustodyError, match="cannot be replaced"):
        journal.prepare_launch(request, owner, admission, consumer_incarnation="consumer-b")
    with pytest.raises(RuntimeCustodyError):
        journal.prepare_launch(
            request, replace(owner, source_fingerprint="another"), admission,
            consumer_incarnation="consumer-a",
        )


@pytest.mark.parametrize("field", [
    "launch_handoff_json", "launch_started_at", "launch_result_json",
    "binding_started_at", "binding_accepted_at", "native_session_id", "worktree_json",
])
def test_creation_native_observations_cannot_be_removed_or_replaced(native_creation, field):
    db, owner, _producer, admission, journal, request, handoff = native_creation
    journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
    journal.begin_binding(request, owner, admission, handoff["id"], now=12)
    journal.report_binding(request, handoff["id"], now=13)
    before = journal.read(request)
    for value in (None, "replacement"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute_write(
                f"UPDATE workspace_embodiments SET {field}=? WHERE key_digest=?",
                (value, request.key_digest),
            )
    assert journal.read(request) == before


def test_creation_native_effect_commit_is_full_on_adopted_connection(native_creation):
    db, owner, _producer, admission, _journal, request, handoff = native_creation
    passive = Database(db.db_path, read_only=True)
    writable = open_existing_writable(passive)
    try:
        journal = CreationJournal(writable)
        with observing_commits(writable) as commits:
            assert journal.begin_launch(request, owner, admission, handoff["id"], now=10)
            journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
            assert journal.begin_binding(request, owner, admission, handoff["id"], now=12)
            journal.report_binding(request, handoff["id"], now=13)
        assert commits == [2, 2, 2, 2]
        assert writable._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
        assert writable._writer_thread is None
    finally:
        writable.close()
        passive.close()


def test_creation_launch_two_sqlite_owners_admit_only_original_once(native_creation):
    db, owner, _producer, admission, _journal, request, handoff = native_creation

    def begin(_index):
        other = Database(db.db_path)
        try:
            return CreationJournal(other).begin_launch(request, owner, admission, handoff["id"], now=10)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(begin, range(2))) == [False, True]


def test_creation_first_prompt_keeps_original_zero_index_and_unknown(native_creation):
    db, owner, producer, admission, journal, request, handoff = native_creation
    with pytest.raises(RuntimeCustodyError, match="remains held"):
        journal.begin_delivery(request, owner, admission, handoff["id"], turn_index=0)
    journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
    journal.begin_binding(request, owner, admission, handoff["id"], now=12)
    journal.report_binding(request, handoff["id"], now=13)
    for index in (-1, 1, True):
        with pytest.raises(RuntimeCustodyError, match="zero turn"):
            journal.begin_delivery(request, owner, admission, handoff["id"], turn_index=index)
    journal.begin_delivery(request, owner, admission, handoff["id"], turn_index=0)
    with pytest.raises(RuntimeCustodyError, match="remains held"):
        journal.begin_delivery(request, owner, admission, handoff["id"], turn_index=0)
    for index in (None, 1):
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            db.execute_write(
                "UPDATE workspace_embodiments SET turn_index=? WHERE key_digest=?",
                (index, request.key_digest),
            )
    close(db, owner, producer)
    with pytest.raises(RuntimeCustodyError, match="prompt admission"):
        journal.complete_launch(request, handoff["id"])
    assert journal.read(request)["state"] == "delivering"
    assert journal.read(request)["turn_index"] == 0
    assert not db.execute_read("SELECT * FROM turns")


@pytest.mark.parametrize("mode", ["normal", "readonly", "adopted"])
def test_creation_launch_damaged_current_schema_never_repairs(native_creation, mode):
    db, _owner, _producer, _admission, _journal, _request, _handoff = native_creation
    passive = Database(db.db_path, read_only=True) if mode == "adopted" else None
    name = next(iter(schema_triggers()))
    db.execute_write(f"DROP TRIGGER {name}")
    try:
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            if passive:
                open_existing_writable(passive)
            else:
                Database(db.db_path, read_only=mode == "readonly")
        assert not db.execute_read("SELECT 1 FROM sqlite_master WHERE name=?", (name,))
    finally:
        if passive:
            passive.close()


def test_creation_launch_strict_inventory_keeps_corrupt_terminal_row_visible(native_creation):
    db, owner, _producer, _admission, _journal, request, _handoff = native_creation
    db.execute_write(
        "UPDATE workspace_embodiments SET state='terminal' WHERE key_digest=?", (request.key_digest,),
    )
    with pytest.raises(RuntimeCustodyError, match="state disagrees"):
        db.runtime_custody_inventory(owner)
    assert set(COLUMNS) <= {row[1] for row in db.execute_read("PRAGMA table_info(workspace_embodiments)")}


def test_creation_launch_migration_from_36_preserves_original_allocation(tmp_db):
    owner, _producer, admission = enrolled(tmp_db)
    request = creation_request()
    journal = CreationJournal(tmp_db)
    journal.claim(request, owner, admission)
    journal.begin_allocation(request, owner, admission)
    journal.report_allocation(request, {
        "id": "workspace-a", "repo": "fixture", "path": "/synthetic/workspace",
        "creation_nonce": "allocation-a",
    })
    before = journal.read(request)
    for name in schema_triggers():
        tmp_db.execute_write(f"DROP TRIGGER {name}")
    for name in COLUMNS:
        tmp_db.execute_write(f"ALTER TABLE workspace_embodiments DROP COLUMN {name}")
    tmp_db.execute_write("UPDATE schema_version SET version=36")
    with pytest.raises(ValueError, match="current schema"):
        Database(tmp_db.db_path, read_only=True)
    migrated = Database(tmp_db.db_path)
    try:
        assert CreationJournal(migrated).read(request) == before
        assert migrated.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        assert all(before[name] is None for name in COLUMNS)
    finally:
        migrated.close()
