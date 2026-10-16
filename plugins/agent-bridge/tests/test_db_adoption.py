"""Existing-only writable adoption preserves passive state and schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_adoption import open_existing_writable


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
