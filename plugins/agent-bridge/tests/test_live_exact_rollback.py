"""Execute captured schema-38 startup and alias owners against admitted inputs."""

import hashlib
import json
from pathlib import Path
import sys
import time
import types

import pytest

from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.db_schema import _SchemaMixin
from agent_bridge.inbox_custody import schema_triggers
from tests.test_live_keyed_receipt import rollover
from legacy_inbox_schema import restore_prior_inbox_guards


def prior_owners(monkeypatch):
    packet = json.loads(
        (Path(__file__).parent / "fixtures/schema38-exact-rollback-source.json").read_text(
            encoding="utf-8",
        )
    )
    assert packet["source_commit"] == "de0495f9eaa1e3d1b888c5c2ea24ce543cd117d4"
    modules = {}
    for source in packet["sources"]:
        fixture = Path(__file__).parent / "fixtures" / source["fixture_path"]
        body = fixture.read_text(encoding="utf-8")
        assert hashlib.sha256(body.encode()).hexdigest() == source["sha256"]
        name = Path(source["source_path"]).stem
        module = types.ModuleType(f"agent_bridge._schema38_{name}")
        module.__package__ = "agent_bridge"
        monkeypatch.setitem(sys.modules, module.__name__, module)
        exec(compile(body, str(fixture), "exec"), module.__dict__)
        modules[name] = module
    modules["db_schema"].SCHEMA_VERSION = 38
    return modules


def test_prior_owners_use_declared_utf8_not_locale(monkeypatch):
    read_text = Path.read_text

    def locale_read(path, encoding=None, errors=None):
        return read_text(path, encoding=encoding or "cp1252", errors=errors)

    monkeypatch.setattr(Path, "read_text", locale_read)
    assert set(prior_owners(monkeypatch)) == {
        "db_schema", "inbox_custody", "db_live_session_aliases",
    }


def admitted(tmp_db, kind):
    now = time.time()
    assert tmp_db.register_live_session(
        "live-1", machine="host", cwd="/workspace", worktree_id="wt-1", repo="repo",
        branch="dev", pid=None, role=None, now=now,
    ) == "live"
    mid, reason = tmp_db.enqueue_live_message_if_fresh(
        "live-1", sender="supervisor", body="original exact input", now=now,
        kind=kind, delivery="steer", exact_session=True,
        expected_session_id="live-1", idempotency_key=f"exact-{kind}",
    )
    assert mid is not None and reason is None
    return mid


@pytest.mark.parametrize("kind", ["prompt", "notify"])
def test_actual_schema38_cold_start_refuses_before_aliasing_admitted_exact_input(
    tmp_db, monkeypatch, kind,
):
    mid = admitted(tmp_db, kind)
    before = dict(tmp_db.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0])
    prior = prior_owners(monkeypatch)
    restore_prior_inbox_guards(tmp_db._get_conn(), version=46)
    with monkeypatch.context() as old:
        old.setitem(sys.modules, "agent_bridge.inbox_custody", prior["inbox_custody"])
        old.setattr(_SchemaMixin, "_init_schema", prior["db_schema"]._SchemaMixin._init_schema)
        with pytest.raises(ValueError, match="live_messages_handoff_input_immutable"):
            Database(tmp_db.db_path)
    assert dict(tmp_db.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]) == before
    assert not tmp_db.execute_read("SELECT * FROM live_session_aliases")
    rollover(tmp_db)
    assert tmp_db.list_pending_live_messages("successor") == []
    assert tmp_db.list_pending_live_messages("live-1") == []
    assert dict(tmp_db.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]) == before
    assert before["runtime_custody_json"] is None and before["handoff_json"] is None


def test_control_actual_schema38_startup_accepts_old_guard_and_moves_exact_row_on_rollover(
    tmp_db, monkeypatch,
):
    mid = admitted(tmp_db, "prompt")
    prior = prior_owners(monkeypatch)
    tmp_db.execute_write("DROP TRIGGER live_messages_handoff_input_immutable")
    tmp_db.execute_write(
        schema_triggers(exact_native=False)["live_messages_handoff_input_immutable"][1]
    )
    tmp_db.execute_write("UPDATE schema_version SET version=39")
    restore_prior_inbox_guards(tmp_db._get_conn(), version=39)
    with monkeypatch.context() as old:
        old.setitem(sys.modules, "agent_bridge.inbox_custody", prior["inbox_custody"])
        old.setattr(_SchemaMixin, "_init_schema", prior["db_schema"]._SchemaMixin._init_schema)
        reopened = Database(tmp_db.db_path)
        reopened.close()
        old.setattr(
            "agent_bridge.db_live_sessions.register_live_session_atomic",
            prior["db_live_session_aliases"].register_live_session_atomic,
        )
        rollover(tmp_db)
    assert tmp_db.execute_read("SELECT session_id FROM live_messages WHERE id=?", (mid,))[0][0] == "successor"


@pytest.mark.parametrize("version", [38, 39])
def test_writable_migration_replaces_only_known_guard_and_preserves_legacy_bytes(tmp_db, version):
    mid = tmp_db.enqueue_live_message("live-1", "peer", "legacy input", time.time())
    before = dict(tmp_db.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0])
    tmp_db.execute_write("DROP TRIGGER live_messages_handoff_input_immutable")
    tmp_db.execute_write(
        schema_triggers(exact_native=False)["live_messages_handoff_input_immutable"][1]
    )
    tmp_db.execute_write("UPDATE schema_version SET version=?", (version,))
    restore_prior_inbox_guards(tmp_db._get_conn(), version=version)
    migrated = Database(tmp_db.db_path)
    try:
        assert migrated.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        assert dict(migrated.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]) == before
    finally:
        migrated.close()


@pytest.mark.parametrize("column,value", [
    ("session_id", "successor"), ("exact_session", 0), ("body", "changed input"),
])
def test_exact_native_input_guard_refuses_legacy_updates(tmp_db, column, value):
    import sqlite3

    mid = admitted(tmp_db, "prompt")
    with pytest.raises(sqlite3.IntegrityError, match="bridge_inbox_handoff_input_immutable"):
        tmp_db.execute_write(f"UPDATE live_messages SET {column}=? WHERE id=?", (value, mid))


def test_interrupted_guard_upgrade_rolls_back_both_guard_and_schema_marker(tmp_db):
    import sqlite3

    legacy = schema_triggers(exact_native=False)["live_messages_handoff_input_immutable"][1]
    tmp_db.execute_write("DROP TRIGGER live_messages_handoff_input_immutable")
    tmp_db.execute_write(legacy)
    tmp_db.execute_write("UPDATE schema_version SET version=39")
    restore_prior_inbox_guards(tmp_db._get_conn(), version=39)
    conn = tmp_db._get_conn()
    before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='live_messages_handoff_input_immutable'"
    ).fetchone()[0]
    conn.set_authorizer(
        lambda action, name, *_: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_CREATE_TRIGGER and name == "live_messages_handoff_input_immutable"
        else sqlite3.SQLITE_OK
    )
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            tmp_db._migrate(conn, 39)
    finally:
        conn.set_authorizer(None)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 39
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='live_messages_handoff_input_immutable'"
    ).fetchone()[0] == before
    resumed = Database(tmp_db.db_path)
    resumed.close()


@pytest.mark.parametrize("start_version", [28, 29])
@pytest.mark.parametrize("checkpoint", range(30, 40))
def test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint(
    tmp_db, monkeypatch, start_version, checkpoint,
):
    from agent_bridge import db_schema

    mid = tmp_db.enqueue_live_message("live-1", "peer", "legacy input", time.time())
    before = dict(tmp_db.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0])
    tmp_db.execute_write("DROP TRIGGER live_messages_handoff_input_immutable")
    tmp_db.execute_write("UPDATE schema_version SET version=?", (start_version,))
    restore_prior_inbox_guards(tmp_db._get_conn(), version=start_version)
    info = db_schema.log.info

    def interrupt_after_commit(message, *args, **kwargs):
        info(message, *args, **kwargs)
        if message.startswith(f"Schema migrated to version {checkpoint}:"):
            raise RuntimeError("interrupted after committed migration checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(db_schema.log, "info", interrupt_after_commit)
        with pytest.raises(RuntimeError, match="committed migration checkpoint"):
            tmp_db._migrate(tmp_db._get_conn(), start_version)
    assert tmp_db.execute_read("SELECT version FROM schema_version")[0][0] == checkpoint
    from agent_bridge.inbox_custody import validate_schema
    validate_schema(tmp_db._get_conn(), exact_native=False, compatible_messages=False)
    resumed = Database(tmp_db.db_path)
    try:
        assert resumed.execute_read("SELECT version FROM schema_version")[0][0] == SCHEMA_VERSION
        validate_schema(resumed._get_conn())
        assert dict(resumed.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]) == before
    finally:
        resumed.close()
