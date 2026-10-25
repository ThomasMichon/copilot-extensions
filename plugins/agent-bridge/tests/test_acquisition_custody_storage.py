"""Real SQLite/process controls; no native session is started or resumed."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge.acquisition_custody_schema import COLUMNS
from agent_bridge.acquisition_journal import AcquisitionJournal
from agent_bridge.acquisition_store import AcquisitionStore
from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_acquisition_custody import launch as launch
from test_acquisition_foundation import request, result
from test_retained_turn_durability import observing_commits
from test_runtime_custody import close, enrolled


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize("associated", [False, True])
def test_acquisition_original_claim_and_stages_commit_with_required_durability(
    tmp_db: Database, adopted: bool, associated: bool,
) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    passive = Database(tmp_db.db_path, read_only=True) if adopted else None
    db = open_existing_writable(passive) if passive else tmp_db
    try:
        journal = AcquisitionJournal(db)
        store = AcquisitionStore(db)
        original = request()
        with observing_commits(db) as commits:
            claim = journal.claim(original, runtime_admission=admission if associated else None)
            store.stage(original, "launching", session_id="bridge-a")
            if associated:
                handoff = journal.prepare_launch(
                    original, owner, admission, session_id="bridge-a",
                    native_session_id="native-a", consumer_incarnation="executor-a",
                )
                assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
                journal.report_launch(original, handoff["id"], native_session_id="native-a", now=3.0)
            journal.complete(original, claim.token, result(original))
        assert commits == ([2] * 6 if associated else [1] * 3)
        assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
        assert db._writer_thread is None
        if associated:
            readonly = Database(db.db_path, read_only=True)
            try:
                reader = AcquisitionJournal(readonly)
                with observing_commits(readonly) as reads:
                    assert reader.read_launch(original)["handoff"] == handoff
                assert len(reads) == 1
                with pytest.raises(PermissionError):
                    reader.begin_launch(original, owner, admission, handoff["id"], now=4.0)
                assert readonly._writer_thread is None
            finally:
                readonly.close()
    finally:
        if passive:
            db.close()
            passive.close()


def test_lost_acquisition_begin_reply_survives_real_child_exit(launch) -> None:
    db, owner, producer, admission, journal, original, _claim, handoff = launch
    child = """
import json, os, sys
from agent_bridge.db import Database
from agent_bridge.acquisition_journal import AcquisitionJournal
from agent_bridge.acquisition_contract import AcquisitionRequest
from agent_bridge.runtime_custody import RuntimeInstallation, RuntimeAdmission
owner = RuntimeInstallation(**json.loads(sys.argv[1]))
db = Database(owner.database)
journal = AcquisitionJournal(db)
assert journal.begin_launch(
    AcquisitionRequest.model_validate_json(sys.argv[2]), owner,
    RuntimeAdmission(**json.loads(sys.argv[3])), sys.argv[4], now=2.0)
os._exit(0)
"""
    from dataclasses import asdict

    subprocess.run(
        [sys.executable, "-c", child, json.dumps(asdict(owner)),
         original.model_dump_json(), admission.document(), handoff["id"]],
        check=True, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    reopened = Database(db.db_path)
    try:
        recovered = AcquisitionJournal(reopened)
        assert recovered.read_launch(original)["stage"] == "starting"
        assert recovered.begin_launch(original, owner, admission, handoff["id"], now=3.0) is False
        close(db, owner, producer)
        assert recovered.read_launch(original)["handoff"] == handoff
        assert recovered.report_launch(
            original, handoff["id"], native_session_id="native-a", now=4.0,
        )["stage"] == "native_accepted"
        assert len(db.execute_read("SELECT * FROM acquisition_attempts")) == 1
        assert journal.read_launch(original)["stage"] == "native_accepted"
    finally:
        reopened.close()


def test_two_sqlite_owners_cannot_both_begin_original_acquisition(launch) -> None:
    db, owner, _producer, admission, _journal, original, _claim, handoff = launch

    def begin() -> bool:
        other = Database(db.db_path)
        try:
            return AcquisitionJournal(other).begin_launch(
                original, owner, admission, handoff["id"], now=2.0,
            )
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: begin(), range(2))) == [False, True]


@pytest.mark.parametrize("mode", ["normal", "readonly", "adopted"])
def test_damaged_acquisition_custody_is_refused_not_repaired(tmp_db: Database, mode: str) -> None:
    name = "acquisition_launch_handoff_json_retained"
    passive = Database(tmp_db.db_path, read_only=True) if mode == "adopted" else None
    tmp_db.execute_write(f"DROP TRIGGER {name}")
    try:
        with pytest.raises(RuntimeCustodyError, match="writable preparation"):
            if passive:
                open_existing_writable(passive)
            else:
                Database(tmp_db.db_path, read_only=mode == "readonly")
        assert not tmp_db.execute_read("SELECT 1 FROM sqlite_master WHERE name=?", (name,))
    finally:
        if passive:
            passive.close()


def test_legacy_acquisition_migration_preserves_original_unknown_history(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    frozen = Path(__file__).with_name("fixtures") / "pre-custody-schema27.sql"
    with sqlite3.connect(path) as conn:
        conn.executescript(frozen.read_text(encoding="utf-8"))
        conn.executescript("""
            CREATE TABLE acquisition_attempts (
                delivery_id TEXT PRIMARY KEY, target TEXT NOT NULL, worktree_id TEXT NOT NULL,
                request_digest TEXT NOT NULL, token TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending','completed','refused')),
                result_json TEXT, refusal_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE acquisition_effects (
                delivery_id TEXT PRIMARY KEY, stage TEXT NOT NULL, data_json TEXT NOT NULL);
            INSERT INTO acquisition_attempts VALUES
                ('original-delivery','worker','original-worktree','original-digest',
                 'original-token','pending',NULL,NULL,1,2);
            INSERT INTO acquisition_effects VALUES ('original-delivery','launching','{"original":true}');
        """)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    db = Database(path)
    try:
        assert db.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION == 36
        row, = db.execute_read("SELECT * FROM acquisition_attempts")
        assert (row["delivery_id"], row["request_digest"], row["token"], row["state"]) == (
            "original-delivery", "original-digest", "original-token", "pending",
        )
        assert all(row[name] is None for name in COLUMNS)
        effect, = db.execute_read("SELECT * FROM acquisition_effects")
        assert (effect["stage"], effect["data_json"]) == ("launching", '{"original":true}')
    finally:
        db.close()


def test_acquisition_launch_inventory_refuses_corrupt_outcome(launch) -> None:
    db, owner, _producer, admission, journal, original, _claim, handoff = launch
    assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
    db.execute_write(
        "UPDATE acquisition_attempts SET launch_result_json=?",
        ('{"native_session_id":"other","accepted_at":3}',),
    )
    with pytest.raises(RuntimeCustodyError, match="changed native identity"):
        db.runtime_custody_inventory(owner)
