"""Strict private restoration preserves effective state without rediscovery."""

from __future__ import annotations

import copy
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest
from credential_relay.registry import RelayBuilder
from credential_relay.sources.injected_token import InjectedTokenSource
from fastapi import FastAPI
from zdd.private_protocol import MAX_FRAME_BYTES, PrivateIPCError, Refusal

from agent_bridge.agent_registry_common import AgentConfig
from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.db import Database
from agent_bridge.effective_runtime import RuntimeOrigins, ServingRuntimeCapture
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.runtime_restore import restore_runtime
from agent_bridge.session_manager import session_manager_from_config


@pytest.fixture
def captured(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_FIXTURE_SECRET", "private-fixture-value")
    monkeypatch.delenv("ABSENT_RUNTIME_FIXTURE", raising=False)
    db = Database(tmp_path / "actual.db")
    cfg = ServiceConfig(
        db_path=str(tmp_path / "obsolete.db"), enable_credential_relay=False,
    )
    manager = session_manager_from_config(
        db, cfg, session_host_state_dir=str(tmp_path / "custom-hosts"),
        drain_auto_release_s=7.5, drain_warn_interval_s=1.25,
    )
    manager._idle_reap_ttl_seconds = 123
    manager._thresholds.warning = 42
    resolver = AgentResolver({"actual": AgentConfig("actual")}, {})
    manager.set_resolver(resolver)
    app = FastAPI()
    app.state.config, app.state.db = cfg, db
    app.state.session_manager, app.state.resolver = manager, resolver
    app.state.auth_token = "private-fixture-auth"
    app.state.ready = app.state.topology_ready = True
    app.state.credential_relay = None
    origins = RuntimeOrigins(
        tmp_path / "config", tmp_path / "install", Path(__file__).absolute(),
        False, 0, ("RUNTIME_FIXTURE_SECRET", "ABSENT_RUNTIME_FIXTURE"),
    )
    capture = ServingRuntimeCapture(app, origins, include_resolver=True, include_relay=True)
    yield app, origins, capture
    db.close()


def test_complete_state_roundtrip_uses_factory_and_preserves_manager_tunables(captured):
    app, origins, capture = captured
    payload = capture.capture().private_payload()
    restored = restore_runtime(payload)
    manager = restored.build_manager(app.state.db)
    assert manager.effective_runtime_settings() == app.state.session_manager.effective_runtime_settings()
    assert manager._resolver is restored.resolver
    assert manager._draining is False
    assert restored.config.db_path == str(app.state.db.db_path)
    assert restored.config.idle_reap_ttl_seconds == 123
    assert restored.auth_token == "private-fixture-auth"
    assert restored.origins == origins
    assert restored.identity == payload["identity"]
    assert set(restored.resolver.agents) == {"actual"}
    assert restored.relay is None

    app.state.config = restored.config
    app.state.session_manager, app.state.resolver = manager, restored.resolver
    roundtrip = ServingRuntimeCapture(
        app, restored.origins, include_resolver=True, include_relay=True,
    ).capture().private_payload()
    assert roundtrip["runtime"] == payload["runtime"]


def test_decode_does_not_open_database_read_config_or_discover_providers(captured, monkeypatch):
    import agent_bridge.config as config
    import agent_bridge.agent_registry as registry
    import agent_bridge.runtime_restore as module

    _app, _origins, capture = captured
    payload = capture.capture().private_payload()

    def forbidden(*args, **kwargs):
        raise AssertionError("restoration accessed an external resource")

    monkeypatch.setattr(module, "Database", forbidden)
    monkeypatch.setattr(config, "load_config", forbidden)
    monkeypatch.setattr(registry, "_detect_local_machine", forbidden)
    monkeypatch.setattr("agent_bridge.agent_registry_discovery.scan_provider_registry", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    restored = restore_runtime(payload)
    assert restored.config.db_path == payload["runtime"]["config"]["db_path"]


def test_environment_is_child_only_and_retains_required_absence(captured, monkeypatch):
    _app, _origins, capture = captured
    restored = restore_runtime(capture.capture().private_payload())
    monkeypatch.setenv("RUNTIME_FIXTURE_SECRET", "caller-must-not-win")
    monkeypatch.setenv("ABSENT_RUNTIME_FIXTURE", "caller-must-not-fill")
    before = dict(os.environ)
    child = restored.environment_for_child(before)
    assert child["RUNTIME_FIXTURE_SECRET"] == "private-fixture-value"
    assert "ABSENT_RUNTIME_FIXTURE" not in child
    assert dict(os.environ) == before
    assert child["AGENT_BRIDGE_CONFIG_DIR"] == str(restored.origins.config_root)
    assert child["AGENT_BRIDGE_INSTALL_DIR"] == str(restored.origins.install_root)
    child["RUNTIME_FIXTURE_SECRET"] = "modified-child-copy"
    assert restored.environment_for_child({})["RUNTIME_FIXTURE_SECRET"] == "private-fixture-value"
    assert "private-fixture" not in repr(restored)


def test_actual_relay_is_restored_unstarted_without_minting(captured, monkeypatch):
    app, origins, _capture = captured
    app.state.config.enable_credential_relay = True
    builder = RelayBuilder()
    builder.add_source(InjectedTokenSource(env_var="RUNTIME_FIXTURE_SECRET"))
    app.state.credential_relay = builder.build()
    origins = replace(origins, relay_policy=True)
    capture = ServingRuntimeCapture(app, origins, include_resolver=True, include_relay=True)
    payload = capture.capture().private_payload()

    def forbidden(*args, **kwargs):
        raise AssertionError("restoration minted or bound a credential")

    monkeypatch.setattr(InjectedTokenSource, "resolve", forbidden)
    restored = restore_runtime(payload)
    assert restored.relay is not app.state.credential_relay
    assert not restored.relay.running
    assert restored.relay.sources[0]._env_var == "RUNTIME_FIXTURE_SECRET"


def test_persistent_origins_survive_passive_activation(captured):
    app, origins, _capture = captured
    origins = replace(origins, port_policy=12345)
    app.state.passive = True
    app.state.config.port = 54321
    app.state.config.enable_credential_relay = False
    app.state.session_manager._draining = True
    capture = ServingRuntimeCapture(app, origins, include_resolver=True, include_relay=True)
    restored = restore_runtime(capture.capture().private_payload())
    assert restored.origins.port_policy == 12345
    assert restored.config.port == 12345
    assert not restored.origins.relay_policy
    assert not restored.build_manager(app.state.db)._draining


@pytest.mark.parametrize(("path", "value"), [
    (("identity",), "not-a-native-binding"),
    (("unexpected",), "private-fixture-secret"),
    (("runtime", "schema"), 2),
    (("runtime", "coverage"), "config-manager-v1"),
    (("runtime", "auth_token"), ""),
    (("runtime", "config_root"), "relative"),
    (("runtime", "install_root"), "/root/../elsewhere"),
    (("runtime", "port_policy"), True),
    (("runtime", "relay_policy"), True),
    (("runtime", "config", "unexpected"), "private-fixture-secret"),
    (("runtime", "config", "port"), "123"),
    (("runtime", "config", "port"), 65536),
    (("runtime", "config", "idle_reap_ttl_seconds"), 124),
    (("runtime", "config", "context_thresholds", "unexpected"), 1),
    (("runtime", "manager", "session_host_state_dir"), "relative"),
    (("runtime", "manager", "drain_auto_release_s"), -1.0),
    (("runtime", "manager", "drain_warn_interval_s"), float("nan")),
    (("runtime", "required_environment"), ["DUP", "DUP"]),
    (("runtime", "required_environment"), ["A=B"]),
    (("runtime", "environment"), {"UNDECLARED_SECRET": "private-fixture-secret"}),
    (("runtime", "environment"), {"RUNTIME_FIXTURE_SECRET": "null\0value"}),
    (("runtime", "resolver", "schema"), 99),
])
def test_malformed_private_state_refuses_with_no_secret_error(captured, path, value):
    _app, _origins, capture = captured
    payload = copy.deepcopy(capture.capture().private_payload())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(PrivateIPCError) as caught:
        restore_runtime(payload)
    assert str(caught.value) == Refusal.AUTHORITY.value
    assert caught.value.__cause__ is None
    assert "private-fixture" not in repr(caught.value)


@pytest.mark.parametrize("field", ["manager", "config", "environment", "relay", "resolver", "port_policy"])
def test_missing_fields_never_fall_through_defaults(captured, field):
    _app, _origins, capture = captured
    payload = capture.capture().private_payload()
    del payload["runtime"][field]
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        restore_runtime(payload)


def test_oversized_private_payload_refuses(captured):
    _app, _origins, capture = captured
    payload = capture.capture().private_payload()
    payload["runtime"]["auth_token"] = "x" * MAX_FRAME_BYTES
    with pytest.raises(PrivateIPCError, match=Refusal.SIZE.value):
        restore_runtime(payload)


def test_wrong_database_refuses_before_factory(captured, tmp_path, monkeypatch):
    import agent_bridge.runtime_restore as module

    _app, _origins, capture = captured
    restored = restore_runtime(capture.capture().private_payload())
    other = Database(tmp_path / "other.db")

    def forbidden(*args, **kwargs):
        raise AssertionError("wrong database reached construction owner")

    monkeypatch.setattr(module, "session_manager_from_config", forbidden)
    try:
        with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
            restored.build_manager(other)
    finally:
        other.close()


def test_config_mutation_refuses_before_factory(captured, monkeypatch):
    import agent_bridge.runtime_restore as module

    app, _origins, capture = captured
    restored = restore_runtime(capture.capture().private_payload())
    restored.config.idle_reap_ttl_seconds = 321

    def forbidden(*args, **kwargs):
        raise AssertionError("mutated configuration reached construction")

    monkeypatch.setattr(module, "session_manager_from_config", forbidden)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        restored.build_manager(app.state.db)


def test_declared_root_environment_cannot_override_captured_namespace(captured):
    _app, _origins, capture = captured
    payload = capture.capture().private_payload()
    payload["runtime"]["required_environment"].append("AGENT_BRIDGE_CONFIG_DIR")
    payload["runtime"]["environment"]["AGENT_BRIDGE_CONFIG_DIR"] = "/different-namespace"
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        restore_runtime(payload)


def test_passive_manager_does_not_create_missing_host_root(captured, tmp_path):
    app, _origins, capture = captured
    payload = capture.capture().private_payload()
    missing = tmp_path / "missing-hosts"
    payload["runtime"]["manager"]["session_host_state_dir"] = str(missing)
    restored = restore_runtime(payload)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        restored.build_manager(app.state.db)
    assert not missing.exists()


def test_passive_manager_preserves_live_turns_replay_cursors_and_ended_rows(captured, monkeypatch):
    app, _origins, capture = captured
    db = app.state.db
    now = time.time()
    for status in ("running", "idle", "starting", "ended"):
        db.create_session(status, status, None, ".", "local", status, now)
    db.create_turn("running", 0, "synthetic open turn", now)
    events = EventLog(db=db, session_id="running")
    events.append("user_message", {"content": "synthetic open turn"})
    events.append("session_state_changed", {"status": "running"})
    db.flush()
    db.set_cursor("reader", "running", 1, now)
    before_sessions = db.list_sessions()
    before_events = db.get_events("running")
    before_turn = db.get_turn("running", 0)
    restored = restore_runtime(capture.capture().private_payload())

    def forbidden(*args, **kwargs):
        raise AssertionError("passive hydration performed a persistent mutation")

    monkeypatch.setattr(db, "execute_write", forbidden)
    monkeypatch.setattr(db, "delete_session", forbidden)
    monkeypatch.setattr("agent_bridge.session_manager.SessionManager.gc", forbidden)
    manager = restored.build_manager(db)
    for status in ("running", "idle", "starting"):
        assert manager.get_session(status).status is SessionStatus(status)
        assert manager.get_session(status).client is None
    assert db.list_sessions() == before_sessions
    assert db.get_events("running") == before_events
    assert db.get_turn("running", 0) == before_turn
    assert db.get_cursor("reader", "running") == 1
    assert [event.id for event in manager.get_session("running").event_log.get_events(1)] == [2]


def test_native_read_only_database_supports_passive_hydration_without_writer(captured):
    app, _origins, capture = captured
    db = app.state.db
    db.create_session("live", "live", None, ".", "local", "running", time.time())
    db.create_turn("live", 0, "synthetic open turn", time.time())
    restored = restore_runtime(capture.capture().private_payload())
    passive_db = Database(db.db_path, read_only=True)
    try:
        manager = restored.build_manager(passive_db)
        assert manager.get_session("live").status is SessionStatus.RUNNING
        assert passive_db.get_turn("live", 0)["completed_at"] is None
        assert passive_db.read_only
        assert passive_db._writer_thread is None
        with pytest.raises(PermissionError, match="Read-only"):
            passive_db.start_writer()
        with pytest.raises(PermissionError, match="Read-only"):
            passive_db.update_session_status("live", "stopped", time.time())
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            passive_db._get_conn().execute("DELETE FROM sessions")
    finally:
        passive_db.close()
    assert db.get_session("live")["status"] == "running"


def test_read_only_database_never_creates_or_migrates_missing_database(tmp_path):
    missing = tmp_path / "absent-root" / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        Database(missing, read_only=True)
    assert not missing.parent.exists()
    outdated = tmp_path / "outdated.db"
    with sqlite3.connect(outdated) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER)")
        conn.execute("INSERT INTO schema_version VALUES (-1)")
    before = outdated.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(outdated, read_only=True)
    assert outdated.read_bytes() == before
