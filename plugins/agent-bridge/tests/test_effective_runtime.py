"""In-memory capture never reloads config or substitutes manager defaults."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from zdd.private_protocol import PrivateIPCError, Refusal

from agent_bridge.db import Database
from agent_bridge.effective_runtime import RuntimeOrigins, ServingRuntimeCapture
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import session_manager_from_config


@pytest.fixture
def serving(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_FIXTURE_SECRET", "fixture-sensitive-value")
    monkeypatch.setenv("UNREQUESTED_FIXTURE_SECRET", "not-in-capture")
    actual_db = Database(tmp_path / "actual.db")
    config = ServiceConfig(db_path=str(tmp_path / "stale-config.db"))
    manager = session_manager_from_config(actual_db, config)
    app = FastAPI()
    app.state.config = config
    app.state.db = actual_db
    app.state.session_manager = manager
    app.state.auth_token = "fixture-auth-value"
    app.state.passive = False
    origins = RuntimeOrigins(
        tmp_path / "config", tmp_path / "installation", Path(__file__).absolute(),
        True, 0, ("CAPTURE_FIXTURE_SECRET", "ABSENT_FIXTURE_VARIABLE"),
    )
    capture = ServingRuntimeCapture(app, origins)
    yield app, origins, capture
    actual_db.close()


def test_actual_database_manager_overrides_and_declared_environment(serving):
    app, _origins, capture = serving
    manager = app.state.session_manager
    manager._idle_reap_ttl_seconds = 123
    manager._thresholds.warning = 42
    captured = capture.capture()
    private = captured.private_payload()["runtime"]
    assert private["coverage"] == "config-manager-v1"
    assert private["config"]["db_path"] == str(app.state.db.db_path)
    assert private["config"]["idle_reap_ttl_seconds"] == 123
    assert private["manager"]["context_thresholds"]["warning"] == 42
    assert private["manager"]["session_host_state_dir"] == str(
        app.state.db.db_path.parent / "hosts",
    )
    assert private["environment"] == {"CAPTURE_FIXTURE_SECRET": "fixture-sensitive-value"}
    assert private["required_environment"] == [
        "CAPTURE_FIXTURE_SECRET", "ABSENT_FIXTURE_VARIABLE",
    ]
    assert private["auth_token"] == "fixture-auth-value"
    assert app.state.config.db_path != private["config"]["db_path"]
    assert app.state.config.idle_reap_ttl_seconds != 123


def test_binding_stable_until_private_state_changes(serving, monkeypatch):
    app, _origins, capture = serving
    first = capture.capture()
    assert capture.capture() is first
    assert capture.matches(first)
    monkeypatch.setenv("CAPTURE_FIXTURE_SECRET", "different-fixture-value")
    assert not capture.matches(first)
    second = capture.capture()
    assert second.identity != first.identity
    assert capture.matches(second)
    app.state.auth_token = "different-fixture-token"
    assert not capture.matches(second)
    assert capture.capture().identity != second.identity


def test_passive_port_and_deferred_relay_do_not_overwrite_policy(serving):
    app, origins, capture = serving
    first = capture.capture()
    app.state.passive = True
    app.state.config.port = 12345
    app.state.bound_port = 12345
    app.state.config.enable_credential_relay = False
    app.state.session_manager._draining = True
    assert capture.capture() is first
    assert first.private_payload()["runtime"]["config"]["enable_credential_relay"] is True
    disabled_policy = ServingRuntimeCapture(app, replace(origins, relay_policy=False))
    assert disabled_policy.capture().private_payload()["runtime"]["config"][
        "enable_credential_relay"
    ] is False


def test_payload_and_repr_are_detached_and_redacted(serving):
    app, _origins, capture = serving
    captured = capture.capture()
    copied = captured.private_payload()
    copied["runtime"]["config"]["bind"] = "changed-fixture"
    assert captured.private_payload()["runtime"]["config"]["bind"] != "changed-fixture"
    app.state.config.bind = "different-fixture"
    assert not capture.matches(captured)
    for value in ("fixture-auth-value", "fixture-sensitive-value", "not-in-capture"):
        assert value not in repr(captured)
        assert value not in captured.identity


def test_missing_origins_or_uninitialized_application_refuses(tmp_path):
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        ServingRuntimeCapture(FastAPI(), None)
    capture = ServingRuntimeCapture(
        FastAPI(), RuntimeOrigins(
            tmp_path / "config", tmp_path / "install", tmp_path / "python", True, 0, (),
        ),
    )
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture.capture()


def test_inconsistent_database_owner_refuses(serving):
    app, _origins, capture = serving
    app.state.db = SimpleNamespace(db_path=Path("/other-database"))
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture.capture()


def test_uncovered_remote_acp_policy_refuses_instead_of_silently_dropping_it(serving):
    app, _origins, capture = serving
    app.state.session_manager._remote_acp_targets["synthetic"] = object()
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture.capture()


@pytest.mark.skipif(os.name != "nt", reason="native Windows interpreter image")
def test_executing_interpreter_uses_native_image_not_venv_redirector():
    from agent_bridge.launch_runtime import executing_executable
    from zdd.private_windows_native import Native

    native = Native()
    image = Path(native.process_image(native.k.GetCurrentProcess())).resolve(strict=True)
    assert executing_executable() == image


def test_invalid_secret_settings_are_only_fixed_refusals(serving):
    app, _origins, capture = serving
    app.state.session_manager._idle_reap_ttl_seconds = float("nan")
    with pytest.raises(PrivateIPCError) as caught:
        capture.capture()
    assert str(caught.value) == Refusal.AUTHORITY.value
    assert "fixture-auth-value" not in repr(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("environment", [("DUP", "DUP"), ("A=B",), ("NULL\0KEY",)])
def test_invalid_required_environment_declaration(environment):
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        RuntimeOrigins(Path("/config"), Path("/install"), Path("/python"), True, 0, environment)


def test_passive_bind_does_not_erase_persistent_port_policy(serving):
    app, origins, _capture = serving
    app.state.passive = True
    app.state.config.port = 54321
    capture = ServingRuntimeCapture(app, replace(origins, port_policy=12345))
    assert capture.capture().private_payload()["runtime"]["config"]["port"] == 12345


def test_expanded_capture_uses_actual_serving_resolver(serving):
    from agent_bridge.agent_registry_common import AgentConfig
    from agent_bridge.agent_registry_resolver import AgentResolver
    from agent_bridge.resolver_runtime import restore_resolver

    app, origins, _capture = serving
    app.state.resolver = AgentResolver({"actual": AgentConfig("actual")}, {})
    app.state.session_manager.set_resolver(app.state.resolver)
    app.state.topology_ready = True
    capture = ServingRuntimeCapture(app, origins, include_resolver=True)
    first = capture.capture()
    private = first.private_payload()["runtime"]
    assert private["coverage"] == "config-manager-resolver-v1"
    restored = restore_resolver(private["resolver"])
    assert set(restored.agents) == {"actual"}
    app.state.resolver.agents["actual"].env["FIXTURE_SECRET"] = "in-memory-only"
    assert not capture.matches(first)
    assert capture.capture().identity != first.identity


def test_expanded_capture_refuses_placeholder_or_mismatched_resolver(serving):
    from agent_bridge.agent_registry_resolver import AgentResolver

    app, origins, _capture = serving
    app.state.resolver = AgentResolver({}, {})
    app.state.topology_ready = False
    capture = ServingRuntimeCapture(app, origins, include_resolver=True)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture.capture()
    app.state.topology_ready = True
    app.state.session_manager.set_resolver(AgentResolver({}, {}))
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture.capture()


def test_capture_checks_application_thread_not_resolver_construction_thread(serving):
    from agent_bridge.agent_registry_resolver import AgentResolver

    app, origins, _capture = serving
    constructed = []
    worker = threading.Thread(target=lambda: constructed.append(AgentResolver({}, {})))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    app.state.resolver = constructed[0]
    app.state.session_manager.set_resolver(app.state.resolver)
    app.state.topology_ready = True
    capture = ServingRuntimeCapture(app, origins, include_resolver=True)
    assert capture.capture().private_payload()["runtime"]["resolver"]["agents"] == {}
    outcomes = []

    def capture_on_worker():
        try:
            capture.capture()
        except PrivateIPCError as exc:
            outcomes.append(exc.reason)

    worker = threading.Thread(target=capture_on_worker)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert outcomes == [Refusal.AUTHORITY]


def test_expanded_relay_capture_requires_declared_sensitive_environment(serving):
    from credential_relay.registry import RelayBuilder
    from credential_relay.sources.injected_token import InjectedTokenSource

    app, origins, _capture = serving
    builder = RelayBuilder()
    builder.add_source(InjectedTokenSource(env_var="CAPTURE_FIXTURE_SECRET"))
    app.state.credential_relay = builder.build()
    app.state.ready = True
    capture = ServingRuntimeCapture(app, origins, include_relay=True)
    first = capture.capture()
    private = first.private_payload()["runtime"]
    assert private["coverage"] == "config-manager-relay-v1"
    assert private["relay"]["sources"][0]["env_var"] == "CAPTURE_FIXTURE_SECRET"
    app.state.credential_relay.policy.allowed_hosts.append("different.test")
    assert not capture.matches(first)
    missing_declaration = ServingRuntimeCapture(
        app, replace(origins, required_environment=()), include_relay=True,
    )
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        missing_declaration.capture()


def test_unrecorded_deferred_relay_refuses_but_explicit_disabled_policy_survives(serving):
    app, origins, _capture = serving
    app.state.ready = True
    app.state.credential_relay = None
    capture = ServingRuntimeCapture(app, origins, include_relay=True)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture.capture()
    disabled = ServingRuntimeCapture(app, replace(origins, relay_policy=False), include_relay=True)
    assert disabled.capture().private_payload()["runtime"]["relay"] is None
