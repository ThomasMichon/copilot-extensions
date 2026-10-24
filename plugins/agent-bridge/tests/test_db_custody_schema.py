"""Required custody storage is prepared by migration, never passive adoption."""

from __future__ import annotations

import hashlib
from pathlib import Path
import queue
import sqlite3

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable
from agent_bridge.runtime_custody import RuntimeCustodyError, validate_schema

FROZEN_V27 = Path(__file__).with_name("fixtures") / "pre-custody-schema27.sql"
HISTORY = ("sessions", "turns", "events", "delivery_cursors", "pending_prompts")


def frozen_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(FROZEN_V27.read_text(encoding="utf-8"))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchall() == [(27,)]
        assert "runtime_custody_json" not in {
            row[1] for row in conn.execute("PRAGMA table_info(turns)")
        }
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'bridge_runtime_%'"
        ).fetchall() == []


def snapshot(path: Path) -> tuple[str, str]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
        dump = "\n".join(conn.iterdump())
    return hashlib.sha256(path.read_bytes()).hexdigest(), dump


def retained_passive_owner(path: Path) -> Database:
    # Model a previously prepared owner without invoking or bypassing today's
    # constructor/validation. The adoption API must revalidate its actual file.
    owner = object.__new__(Database)
    owner._db_path = path
    owner._read_only = True
    info = path.stat()
    owner._read_only_file_id = info.st_dev, info.st_ino
    owner._writer_thread = None
    owner._event_write_q = queue.Queue()
    return owner


def test_frozen_v27_refuses_read_only_and_adoption_without_mutation(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    frozen_database(path)
    before = snapshot(path)
    monkeypatch.setattr(Database, "_init_schema", lambda *_: pytest.fail("hidden migration"))
    monkeypatch.setattr(Database, "start_writer", lambda *_: pytest.fail("hidden writer"))
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert snapshot(path) == before
    with pytest.raises(ValueError, match="prepared schema"):
        open_existing_writable(retained_passive_owner(path))
    assert snapshot(path) == before


def test_supported_writable_migration_preserves_v27_history_and_ordinary_operations(tmp_path):
    path = tmp_path / "sessions.db"
    frozen_database(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in HISTORY
        }
    prepared = Database(path)
    try:
        assert SCHEMA_VERSION == 32
        assert prepared.execute_read("SELECT version FROM schema_version")[0][0] == 32
        validate_schema(prepared._get_conn())
        for table, rows in before.items():
            after = [dict(row) for row in prepared.execute_read(f"SELECT * FROM {table}")]
            assert [{key: row[key] for key in rows[0]} for row in after] == rows
        assert prepared.execute_read("SELECT * FROM bridge_runtime_epoch") == []
        assert prepared.execute_read("SELECT * FROM bridge_runtime_producers") == []
        prepared.create_session("ordinary", "ordinary", None, ".", "local", "idle", 3.0)
        prepared.create_turn("ordinary", 0, "ordinary prompt", 3.0)
        prepared.update_session_status("ordinary", "ended", 4.0)
        assert prepared.gc_eligible_session_ids(["ended"], 5.0) == ["retained", "ordinary"]
        prepared.delete_session("ordinary")
        assert prepared.get_turn("retained", 0)["prompt"] == "original prompt"
        assert prepared.peek_pending_prompt("retained")["prompt"] == "retained follow-up"
    finally:
        prepared.close()
    passive = Database(path, read_only=True)
    writable = open_existing_writable(passive)
    try:
        assert writable._writer_thread is None
        writable.create_session("adopted", "adopted", None, ".", "local", "idle", 5.0)
        writable.create_turn("adopted", 0, "after adoption", 5.0)
        assert writable.get_turn("adopted", 0)["runtime_custody_json"] is None
        assert writable.gc_status_summary(["ended"])["count"] == 1
    finally:
        writable.close()
        passive.close()


@pytest.mark.parametrize("damage", [
    "epoch-table", "producer-table", "session-column", "turn-column",
    "native-column", "delivery-column", "missing-trigger", "wrong-trigger", "wrong-type",
])
def test_current_version_partial_shape_refuses_all_new_owners_without_bootstrap(tmp_path, damage):
    path = tmp_path / "sessions.db"
    current = Database(path)
    current.create_session("retained", "retained", None, ".", "local", "ended", 1.0)
    current.close()
    with sqlite3.connect(path) as conn:
        if damage == "epoch-table":
            conn.execute("DROP TABLE bridge_runtime_epoch")
        elif damage == "producer-table":
            conn.execute("DROP TABLE bridge_runtime_producers")
        elif damage in ("missing-trigger", "wrong-trigger"):
            conn.execute("DROP TRIGGER turns_runtime_admission")
            if damage == "wrong-trigger":
                conn.execute(
                    "CREATE TRIGGER turns_runtime_admission BEFORE INSERT ON turns "
                    "BEGIN SELECT 1; END"
                )
        elif damage == "wrong-type":
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='sessions'"
            ).fetchall():
                conn.execute(f"DROP TRIGGER {row[0]}")
            conn.execute("ALTER TABLE sessions ADD COLUMN replacement BLOB")
            conn.execute("ALTER TABLE sessions DROP COLUMN runtime_custody_json")
            conn.execute("ALTER TABLE sessions RENAME COLUMN replacement TO runtime_custody_json")
        else:
            table = "sessions" if damage == "session-column" else "turns"
            column = {
                "session-column": "runtime_custody_json", "turn-column": "runtime_custody_json",
                "native-column": "native_admission_json", "delivery-column": "acquisition_delivery_id",
            }[damage]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,),
            ).fetchall():
                conn.execute(f"DROP TRIGGER {row[0]}")
            if damage == "delivery-column":
                conn.execute("DROP INDEX idx_turn_acquisition_delivery")
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    before = snapshot(path)
    with pytest.raises(RuntimeCustodyError, match="preparation"):
        Database(path, read_only=True)
    assert snapshot(path) == before
    with pytest.raises(RuntimeCustodyError, match="preparation"):
        open_existing_writable(retained_passive_owner(path))
    assert snapshot(path) == before
    with pytest.raises(RuntimeCustodyError, match="preparation"):
        Database(path)
    assert snapshot(path) == before


def test_adoption_rechecks_shape_changed_after_passive_preparation(tmp_path):
    path = tmp_path / "sessions.db"
    current = Database(path)
    current.close()
    passive = Database(path, read_only=True)
    try:
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TRIGGER sessions_runtime_admission")
        before = snapshot(path)
        with pytest.raises(RuntimeCustodyError, match="preparation"):
            open_existing_writable(passive)
        assert snapshot(path) == before
        assert passive.read_only and passive._writer_thread is None
    finally:
        passive.close()
