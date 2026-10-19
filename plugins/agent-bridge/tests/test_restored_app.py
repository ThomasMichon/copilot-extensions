"""Candidate startup has no ordinary application ownership side effects."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from credential_relay.registry import RelayBuilder
from credential_relay.sources.injected_token import InjectedTokenSource
from fastapi.testclient import TestClient
from zdd.private_protocol import PrivateIPCError, Refusal

from agent_bridge.app import create_app
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_restore import restore_runtime
from test_runtime_restore import captured as captured


def forbid(*args, **kwargs):
    raise AssertionError("candidate entered an ordinary ownership path")


def test_candidate_lifespan_preserves_live_state_without_ordinary_startup(captured, monkeypatch):
    import agent_bridge.app as ordinary
    import agent_bridge.restored_app as candidate

    predecessor, _origins, capture = captured
    db = predecessor.state.db
    db.create_session("live", "live", None, ".", "local", "running", time.time())
    db.create_turn("live", 0, "synthetic open turn", time.time())
    db.set_cursor("reader", "live", 7, time.time())
    before_sessions = db.list_sessions()
    before_turn = db.get_turn("live", 0)
    restored = restore_runtime(capture.capture().private_payload())
    environment = dict(os.environ)

    for name in (
        "load_config", "load_or_create_auth_token", "daemon_resolver",
        "_start_credential_relay", "session_manager_from_config", "lifespan",
        "start_agent_roster_cache", "stop_agent_roster_cache", "shutdown_ssh",
    ):
        monkeypatch.setattr(ordinary, name, forbid)
    monkeypatch.setattr(ordinary.telemetry, "load_sink_from_config", forbid)
    monkeypatch.setattr(ordinary.telemetry, "load_sink_from_env", forbid)
    for name in (
        "write_running_version", "record_manager_generation_id", "stage_manager_generation_id",
    ):
        monkeypatch.setattr(f"agent_bridge.runtime_version.{name}", forbid)
    monkeypatch.setattr("agent_bridge.session_manager.SessionManager.gc", forbid)
    monkeypatch.setattr(
        "agent_bridge.session_manager.SessionManager.graceful_cancel_for_redeploy", forbid,
    )
    opened = []
    real_db = candidate.Database

    def open_db(path, *, read_only):
        assert read_only
        result = real_db(path, read_only=read_only)
        opened.append(result)
        return result

    monkeypatch.setattr(candidate, "Database", open_db)
    app = create_app(restored_runtime=restored)
    assert app.state.resolver is restored.resolver
    assert app.state.credential_relay is restored.relay
    assert app.state.runtime_origins is restored.origins
    assert not app.state.candidate_ready
    with TestClient(app) as client:
        assert client.get("/health").json() == {
            "status": "candidate", "service": "agent-bridge", "ready": False,
            "candidate_ready": True, "passive": True,
        }
        assert app.state.session_manager._resolver is restored.resolver
        assert app.state.session_manager.get_session("live").status is SessionStatus.RUNNING
        assert app.state.db._writer_thread is None
        assert app.state.db.read_only
        if os.name == "nt":
            from zdd.private_windows_native import Native

            native = Native()
            expected_image = Path(native.process_image(native.k.GetCurrentProcess())).resolve()
        else:
            expected_image = Path(sys.executable).resolve()
        assert app.state.runtime_executable == expected_image
        assert not app.state.publish_on_ready
        assert not app.state.topology_ready
        assert not app.state.credential_relay_ready
        assert dict(os.environ) == environment
    assert not app.state.candidate_ready
    assert len(opened) == 1
    assert db.list_sessions() == before_sessions
    assert db.get_turn("live", 0) == before_turn
    assert db.get_cursor("reader", "live") == 7
    assert dict(os.environ) == environment


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/sessions"), ("POST", "/sessions"),
    ("POST", "/admin/drain"), ("POST", "/admin/shutdown"),
    ("POST", "/admin/credential-relay/adopt"),
    ("GET", "/agents"), ("GET", "/docs"), ("GET", "/openapi.json"),
])
def test_candidate_exposes_only_health(captured, method, path):
    _app, _origins, capture = captured
    app = create_app(restored_runtime=restore_runtime(capture.capture().private_payload()))
    with TestClient(app) as client:
        assert client.request(method, path).status_code == 404
    assert {route.path for route in app.routes} == {"/health"}


def test_candidate_keeps_actual_relay_unstarted(captured, monkeypatch):
    predecessor, origins, _capture = captured
    predecessor.state.config.enable_credential_relay = True
    builder = RelayBuilder()
    builder.add_source(InjectedTokenSource(env_var="RUNTIME_FIXTURE_SECRET"))
    predecessor.state.credential_relay = builder.build()
    capture = ServingRuntimeCapture(
        predecessor, replace(origins, relay_policy=True),
        include_resolver=True, include_relay=True,
    )
    restored = restore_runtime(capture.capture().private_payload())
    monkeypatch.setattr(type(restored.relay), "start", forbid)
    monkeypatch.setattr(type(restored.relay), "stop", forbid)
    monkeypatch.setattr(InjectedTokenSource, "resolve", forbid)
    app = create_app(restored_runtime=restored)
    with TestClient(app):
        assert app.state.credential_relay is restored.relay
        assert not restored.relay.running
        assert app.state.config.enable_credential_relay
        assert app.state.runtime_origins.relay_policy
    assert not restored.relay.running


@pytest.mark.parametrize("kwargs", [{"config": object()}, {"token": ""}, {"token": "other"}])
def test_candidate_refuses_ambiguous_override(captured, kwargs):
    _app, _origins, capture = captured
    runtime = restore_runtime(capture.capture().private_payload())
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        create_app(restored_runtime=runtime, **kwargs)


def test_candidate_refuses_mutation_before_opening_database(captured, monkeypatch):
    _app, _origins, capture = captured
    runtime = restore_runtime(capture.capture().private_payload())
    app = create_app(restored_runtime=runtime)
    runtime.config.db_path = "/different-private-path.db"
    monkeypatch.setattr("agent_bridge.restored_app.Database", forbid)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        with TestClient(app):
            pass
    assert not app.state.candidate_ready


def test_candidate_database_failure_is_secret_redacted(captured, monkeypatch):
    _app, _origins, capture = captured
    runtime = restore_runtime(capture.capture().private_payload())

    def broken_db(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic private database path")

    monkeypatch.setattr("agent_bridge.restored_app.Database", broken_db)
    app = create_app(restored_runtime=runtime)
    with pytest.raises(PrivateIPCError) as error:
        with TestClient(app):
            pass
    assert str(error.value) == Refusal.AUTHORITY.value
    assert error.value.__suppress_context__
    assert not app.state.candidate_ready


def test_candidate_failed_hydration_closes_read_only_connection(captured, monkeypatch):
    import agent_bridge.restored_app as module

    _app, _origins, capture = captured
    runtime = restore_runtime(capture.capture().private_payload())
    opened = []
    closed = []
    original = module.Database
    original_close = original.close

    def open_db(path, *, read_only):
        result = original(path, read_only=read_only)
        opened.append(result)
        return result

    def refuse(*args, **kwargs):
        raise PrivateIPCError(Refusal.AUTHORITY)

    def close_db(db):
        original_close(db)
        assert db._local.conn is None
        closed.append(db)

    monkeypatch.setattr(module, "Database", open_db)
    monkeypatch.setattr(original, "close", close_db)
    monkeypatch.setattr(type(runtime), "build_manager", refuse)
    with pytest.raises(PrivateIPCError):
        with TestClient(create_app(restored_runtime=runtime)):
            pass
    assert len(opened) == 1
    assert closed == opened
