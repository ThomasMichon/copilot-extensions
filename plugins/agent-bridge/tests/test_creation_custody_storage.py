"""Isolated SQLite/process controls, not native or installation qualification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge.creation_custody import CreationJournal, CreationKind, creation_table
from agent_bridge.creation_custody_schema import COLUMNS
from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_creation_custody import creation_request
from test_retained_turn_durability import observing_commits
from test_runtime_custody import close, enrolled


@pytest.mark.parametrize("kind", ["workspace", "provider"])
@pytest.mark.parametrize("adopted", [False, True])
def test_original_creation_claim_is_full_sync_and_passive_read_is_write_free(
    tmp_db: Database, kind: CreationKind, adopted: bool,
) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    original = creation_request(kind)
    passive = Database(tmp_db.db_path, read_only=True) if adopted else None
    db = open_existing_writable(passive) if passive else tmp_db
    try:
        with observing_commits(db) as commits:
            row = CreationJournal(db).claim(original, owner, admission)
        assert commits == [2]
        assert db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1
        assert db._writer_thread is None
        readonly = Database(db.db_path, read_only=True)
        try:
            assert CreationJournal(readonly).read(original) == row
            assert readonly._writer_thread is None
            with pytest.raises(PermissionError):
                CreationJournal(readonly).claim(original, owner, admission)
        finally:
            readonly.close()
    finally:
        if passive:
            db.close()
            passive.close()


@pytest.mark.parametrize("kind", ["workspace", "provider"])
def test_creation_claim_lost_reply_survives_real_child_exit(tmp_db: Database, kind: CreationKind) -> None:
    owner, producer, admission = enrolled(tmp_db)
    original = creation_request(kind)
    child = """
import json, os, sys
from agent_bridge.db import Database
from agent_bridge.creation_custody import CreationJournal, CreationRequest
from agent_bridge.runtime_custody import RuntimeInstallation, RuntimeAdmission
owner = RuntimeInstallation(**json.loads(sys.argv[1]))
db = Database(owner.database)
CreationJournal(db).claim(
    CreationRequest(**json.loads(sys.argv[2])), owner,
    RuntimeAdmission(**json.loads(sys.argv[3])))
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", child, json.dumps(asdict(owner)),
         json.dumps(asdict(original)), admission.document()],
        check=True, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    row = CreationJournal(tmp_db).read(original)
    close(tmp_db, owner, producer)
    reopened = Database(tmp_db.db_path)
    try:
        assert CreationJournal(reopened).claim(original, owner, admission) == row
        assert len(reopened.execute_read(f"SELECT * FROM {creation_table(kind)}")) == 1
        assert not reopened.execute_read("SELECT * FROM sessions")
        assert not reopened.execute_read("SELECT * FROM turns")
    finally:
        reopened.close()


@pytest.mark.parametrize("kind", ["workspace", "provider"])
def test_two_sqlite_owners_recover_one_original_creation(tmp_db: Database, kind: CreationKind) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    original = creation_request(kind)

    def claim(_: int) -> dict:
        other = Database(tmp_db.db_path)
        try:
            return CreationJournal(other).claim(original, owner, admission)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(claim, range(2)))
    assert rows[0] == rows[1]
    assert len(tmp_db.execute_read(f"SELECT * FROM {creation_table(kind)}")) == 1


@pytest.mark.parametrize("mode", ["normal", "readonly", "adopted"])
@pytest.mark.parametrize("kind", ["workspace", "provider"])
def test_damaged_current_creation_schema_refuses_without_repair(
    tmp_db: Database, mode: str, kind: CreationKind,
) -> None:
    name = f"{creation_table(kind)}_original_input_retained"
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


@pytest.mark.parametrize("kind", ["workspace", "provider"])
def test_legacy_creation_migration_preserves_unknown_original_history(
    tmp_path: Path, kind: CreationKind,
) -> None:
    path = tmp_path / "legacy.db"
    frozen = Path(__file__).with_name("fixtures") / "pre-custody-schema27.sql"
    with sqlite3.connect(path) as conn:
        conn.executescript(frozen.read_text(encoding="utf-8"))
        conn.execute(
            f"INSERT INTO {creation_table(kind)} (key_digest,facts_digest,state,session_id,"
            "native_session_id) VALUES ('original-key','original-facts','launching','bridge-a','native-a')",
        )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    db = Database(path)
    try:
        assert db.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION == 36
        row, = db.execute_read(f"SELECT * FROM {creation_table(kind)}")
        assert (row["key_digest"], row["facts_digest"], row["state"], row["session_id"],
                row["native_session_id"]) == (
            "original-key", "original-facts", "launching", "bridge-a", "native-a",
        )
        assert all(row[name] is None for name in COLUMNS)
    finally:
        db.close()
