"""Existing-only writable adoption preserves passive state and schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable


def test_pre_relationship_v26_requires_preparation_before_passive_adoption(tmp_path):
    path = tmp_path / "sessions.db"
    # Frozen v26 core projection, not a database bootstrapped by today's schema.
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (26);
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, agent_name TEXT,
                caller_id TEXT, caller_session_id TEXT, target_dir TEXT,
                target_type TEXT NOT NULL DEFAULT 'local', target_json TEXT,
                status TEXT NOT NULL DEFAULT 'created', pid INTEGER,
                acp_session_id TEXT, config_json TEXT, context_size INTEGER,
                context_used INTEGER, usage_model TEXT, last_usage_at REAL,
                predecessor_id TEXT, successor_id TEXT, handoff_at REAL,
                background_recovery_enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            INSERT INTO sessions (id,name,status,created_at,updated_at)
                VALUES ('retained','retained','stopped',1,1);
        """)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 26
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'remote_acp_relationship%'"
        ).fetchall() == []

    prepared = Database(path)
    try:
        assert prepared.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        assert prepared.get_session("retained")["status"] == "stopped"
        identity = prepared.execute_read(
            "SELECT id FROM remote_acp_relationship_authority WHERE singleton=1"
        )[0]["id"]
        assert len(identity) == 32
        assert prepared.execute_read("SELECT * FROM remote_acp_relationships") == []
    finally:
        prepared.close()
    passive = Database(path, read_only=True)
    writable = open_existing_writable(passive)
    try:
        assert writable.execute_read(
            "SELECT id FROM remote_acp_relationship_authority WHERE singleton=1"
        )[0]["id"] == identity
        assert writable.get_session("retained")["status"] == "stopped"
        assert writable._writer_thread is None
    finally:
        writable.close()
        passive.close()


def test_existing_writable_owner_never_migrates_or_starts_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sessions.db"
    original = Database(path)
    original.close()
    readonly = Database(path, read_only=True)
    monkeypatch.setattr(Database, "_init_schema", lambda *a: pytest.fail("migration"))
    monkeypatch.setattr(Database, "start_writer", lambda *a: pytest.fail("writer"))
    writable = open_existing_writable(readonly)
    try:
        assert readonly.read_only and not writable.read_only
        assert writable._writer_thread is None
        assert readonly._local.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 0
        assert writable._local.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 0
        assert writable._local.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        writable.execute_write("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        assert readonly.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        with pytest.raises(PermissionError):
            readonly.execute_write("UPDATE schema_version SET version=0")
    finally:
        writable.close()
        readonly.close()


@pytest.mark.parametrize("drift", ["schema", "journal", "missing", "replaced", "active"])
def test_writable_open_refuses_missing_changed_or_unprepared_database(
    tmp_path: Path, drift: str,
) -> None:
    path = tmp_path / "sessions.db"
    original = Database(path)
    original.close()
    readonly = Database(path, read_only=True)
    try:
        if drift == "active":
            source = Database(path)
            try:
                with pytest.raises(ValueError):
                    open_existing_writable(source)
            finally:
                source.close()
            return
        if drift == "schema":
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE schema_version SET version=0")
        elif drift == "journal":
            # Release the passive reader before changing the fixture journal.
            readonly._local.conn.close()
            readonly._local.conn = None
            with sqlite3.connect(path) as conn:
                conn.execute("PRAGMA journal_mode=DELETE")
        else:
            replacement = tmp_path / "replacement.db"
            if drift == "replaced":
                other = Database(replacement)
                other.close()
            # Close before renaming on Windows; retain the prepared file id.
            readonly._local.conn.close()
            readonly._local.conn = None
            path.rename(tmp_path / "removed.db")
            if drift == "replaced":
                replacement.rename(path)
        with pytest.raises((ValueError, OSError, sqlite3.Error)):
            open_existing_writable(readonly)
        assert readonly.read_only
        assert readonly._writer_thread is None
        if drift == "missing":
            assert not path.exists()
    finally:
        readonly.close()
