"""Provider same-row SQLite durability, migration and strict inventory controls."""

from __future__ import annotations

from dataclasses import asdict
import json
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge.creation_custody import CreationJournal, CreationRequest, legacy_creation
from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.provider_creation_launch_schema import COLUMNS, schema_triggers
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_creation_custody import creation_request
from test_retained_turn_durability import observing_commits
from test_runtime_custody import close, enrolled


@pytest.mark.parametrize("adopted", [False, True])
def test_provider_native_all_writes_are_full_and_passive_read_is_write_free(
    tmp_db: Database, adopted: bool,
) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    request = creation_request("provider")
    passive = Database(tmp_db.db_path, read_only=True)
    db = open_existing_writable(passive) if adopted else tmp_db
    try:
        journal = CreationJournal(db)
        journal.claim(request, owner, admission)
        with observing_commits(db) as commits:
            handoff = journal.prepare_launch(request, owner, admission, consumer_incarnation="consumer-a")
            assert journal.begin_launch(request, owner, admission, handoff["id"], now=10)
            report = journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
        assert commits == [2, 2, 2]
        assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
        assert CreationJournal(passive).read_launch(request) == report
        assert passive._writer_thread is db._writer_thread is None
        rows = db.runtime_custody_inventory(owner)["rows"]["provider_embodiments"]
        assert len(rows) == 1 and rows[0]["launch_result_json"] is not None
        assert "native-outcome-unqualified:provider_embodiments:" + request.key_digest in (
            db.runtime_custody_inventory(owner)["blockers"]
        )
    finally:
        if adopted:
            db.close()
        passive.close()


@pytest.mark.parametrize("accepted", [False, True])
def test_provider_committed_native_observation_survives_child_exit(
    tmp_db: Database, accepted: bool,
) -> None:
    owner, producer, admission = enrolled(tmp_db)
    request = creation_request("provider")
    journal = CreationJournal(tmp_db)
    journal.claim(request, owner, admission)
    handoff = journal.prepare_launch(request, owner, admission, consumer_incarnation="consumer-a")
    child = """
import json, os, sys
from agent_bridge.creation_custody import CreationJournal, CreationRequest
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeInstallation, RuntimeAdmission
owner = RuntimeInstallation(**json.loads(sys.argv[1]))
request = CreationRequest(**json.loads(sys.argv[2]))
admission = RuntimeAdmission(**json.loads(sys.argv[3]))
journal = CreationJournal(Database(owner.database))
assert journal.begin_launch(request, owner, admission, sys.argv[4], now=10)
if sys.argv[5] == 'accepted':
    journal.report_launch(request, sys.argv[4], native_session_id='native-a', now=11)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", child, json.dumps(asdict(owner)), json.dumps(asdict(request)),
         admission.document(), handoff["id"], "accepted" if accepted else "unknown"],
        check=True, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    close(tmp_db, owner, producer)
    passive = Database(tmp_db.db_path, read_only=True)
    try:
        report = CreationJournal(passive).read_launch(request)
        assert report["handoff"] == handoff and report["started_at"] == 10
        assert report["result"] == ({"native_session_id": "native-a", "accepted_at": 11} if accepted else None)
        assert CreationJournal(passive).read(request)["state"] == ("complete" if accepted else "launching")
    finally:
        passive.close()


@pytest.mark.parametrize("mode", ["normal", "readonly", "adopted"])
@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_provider_current_schema_corruption_refuses_without_repair(
    tmp_db: Database, mode: str, damage: str,
) -> None:
    passive = Database(tmp_db.db_path, read_only=True) if mode == "adopted" else None
    name = next(iter(schema_triggers()))
    tmp_db.execute_write(f"DROP TRIGGER {name}")
    if damage == "altered":
        tmp_db.execute_write(f"CREATE TRIGGER {name} BEFORE UPDATE ON provider_embodiments BEGIN SELECT 1; END")
    before = [tuple(row) for row in tmp_db.execute_read(
        "SELECT name,sql FROM sqlite_master ORDER BY name",
    )]
    try:
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            if passive:
                open_existing_writable(passive)
            else:
                Database(tmp_db.db_path, read_only=mode == "readonly")
        assert [tuple(row) for row in tmp_db.execute_read(
            "SELECT name,sql FROM sqlite_master ORDER BY name",
        )] == before
    finally:
        if passive:
            passive.close()


def test_provider_migration_from_37_preserves_legacy_unknown_and_original_inputs(tmp_db: Database) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    request = creation_request("provider")
    journal = CreationJournal(tmp_db)
    before = journal.claim(request, owner, admission)
    legacy = legacy_creation(tmp_db, "provider", "f" * 64, "legacy-facts")
    tmp_db.execute_write(
        "UPDATE provider_embodiments SET state='launching',native_session_id='legacy-native' WHERE key_digest=?",
        (legacy["key_digest"],),
    )
    for name in schema_triggers():
        tmp_db.execute_write(f"DROP TRIGGER {name}")
    for name in COLUMNS:
        tmp_db.execute_write(f"ALTER TABLE provider_embodiments DROP COLUMN {name}")
    tmp_db.execute_write("UPDATE schema_version SET version=37")
    with pytest.raises(ValueError, match="current schema"):
        Database(tmp_db.db_path, read_only=True)
    migrated = Database(tmp_db.db_path)
    try:
        assert migrated.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION == 38
        assert CreationJournal(migrated).read(request) == before
        rows = migrated.runtime_custody_inventory(owner)["rows"]["provider_embodiments"]
        row = next(row for row in rows if row["key_digest"] == legacy["key_digest"])
        assert row["state"] == "launching" and row["native_session_id"] == "legacy-native"
        assert row["runtime_custody_json"] is row["creation_facts_json"] is None
        assert all(row[name] is None for name in COLUMNS)
        with pytest.raises(RuntimeCustodyError, match="original admission"):
            CreationJournal(migrated).claim(
                CreationRequest.capture(
                    "provider", legacy["key_digest"], json.loads(request.facts_json),
                ), owner, admission,
            )
    finally:
        migrated.close()


@pytest.mark.parametrize("mode", ["normal", "readonly", "adopted"])
def test_provider_missing_current_column_refuses_without_adding_it(
    tmp_db: Database, mode: str,
) -> None:
    passive = Database(tmp_db.db_path, read_only=True) if mode == "adopted" else None
    for name in schema_triggers():
        tmp_db.execute_write(f"DROP TRIGGER {name}")
    tmp_db.execute_write("ALTER TABLE provider_embodiments DROP COLUMN launch_result_json")
    try:
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            if passive:
                open_existing_writable(passive)
            else:
                Database(tmp_db.db_path, read_only=mode == "readonly")
        assert "launch_result_json" not in {
            row[1] for row in tmp_db.execute_read("PRAGMA table_info(provider_embodiments)")
        }
    finally:
        if passive:
            passive.close()
