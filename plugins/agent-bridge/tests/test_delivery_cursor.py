"""Tests for the delivery-cursor DB layer and schema migration."""

from __future__ import annotations

import time

from agent_bridge.db import SCHEMA_VERSION, Database


def _seed_session(db: Database, sid: str = "s1") -> None:
    db.create_session(sid, "test", None, ".", "local", "idle", time.time())


class TestDeliveryCursor:
    def test_default_cursor_is_zero(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        assert tmp_db.get_cursor("caller-a", "s1") == 0

    def test_set_and_get_cursor(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        tmp_db.set_cursor("caller-a", "s1", 5, time.time())
        assert tmp_db.get_cursor("caller-a", "s1") == 5

    def test_cursor_is_monotonic(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        tmp_db.set_cursor("caller-a", "s1", 10, time.time())
        # A lower (stale / duplicate) ack must not regress the cursor.
        effective = tmp_db.set_cursor("caller-a", "s1", 4, time.time())
        assert effective == 10
        assert tmp_db.get_cursor("caller-a", "s1") == 10

    def test_cursors_are_per_caller(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        tmp_db.set_cursor("caller-a", "s1", 7, time.time())
        tmp_db.set_cursor("caller-b", "s1", 2, time.time())
        assert tmp_db.get_cursor("caller-a", "s1") == 7
        assert tmp_db.get_cursor("caller-b", "s1") == 2

    def test_cursor_upsert_advances(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        tmp_db.set_cursor("caller-a", "s1", 3, time.time())
        eff = tmp_db.set_cursor("caller-a", "s1", 9, time.time())
        assert eff == 9


class TestEventsRange:
    def test_range_inclusive(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        now = time.time()
        for i in range(1, 6):
            tmp_db.append_event("s1", i, "agent_message", {"text": str(i)}, now)
        rows = tmp_db.get_events_range("s1", 2, 4)
        assert [r["event_id"] for r in rows] == [2, 3, 4]

    def test_range_open_ended(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        now = time.time()
        for i in range(1, 4):
            tmp_db.append_event("s1", i, "agent_message", {"text": str(i)}, now)
        rows = tmp_db.get_events_range("s1", 2)
        assert [r["event_id"] for r in rows] == [2, 3]

    def test_range_deserializes_data(self, tmp_db: Database) -> None:
        _seed_session(tmp_db)
        tmp_db.append_event("s1", 1, "agent_message", {"text": "hi"}, time.time())
        rows = tmp_db.get_events_range("s1", 1, 1)
        assert rows[0]["data"] == {"text": "hi"}


class TestSchemaMigration:
    def test_fresh_db_is_current_version(self, tmp_path) -> None:
        db = Database(tmp_path / "fresh.db")
        rows = db.execute_read("SELECT version FROM schema_version")
        assert rows[0]["version"] == SCHEMA_VERSION

    def test_delivery_cursors_table_exists(self, tmp_path) -> None:
        db = Database(tmp_path / "fresh.db")
        rows = db.execute_read(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='delivery_cursors'"
        )
        assert len(rows) == 1

    def test_migration_from_v4_adds_table(self, tmp_path) -> None:
        """A v4 DB (no delivery_cursors) migrates cleanly to v5."""
        import sqlite3

        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (4);
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, agent_name TEXT,
                caller_id TEXT, target_dir TEXT,
                target_type TEXT NOT NULL DEFAULT 'local', target_json TEXT,
                status TEXT NOT NULL DEFAULT 'created', pid INTEGER,
                acp_session_id TEXT, config_json TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE events (
                session_id TEXT NOT NULL, event_id INTEGER NOT NULL,
                event_type TEXT NOT NULL, data_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                PRIMARY KEY (session_id, event_id)
            );
            """
        )
        conn.commit()
        conn.close()

        # Opening via Database triggers the v4->v5 migration.
        db = Database(db_path)
        ver = db.execute_read("SELECT version FROM schema_version")[0]["version"]
        assert ver == SCHEMA_VERSION
        _seed_session(db)
        db.set_cursor("c", "s1", 3, time.time())
        assert db.get_cursor("c", "s1") == 3
