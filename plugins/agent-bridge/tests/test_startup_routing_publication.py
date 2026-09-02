"""Startup publishes the normal daemon route before slow initialization."""

from __future__ import annotations

import os
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import agent_bridge.app as app_module
from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig
from zdd import routing


def _config(tmp_path) -> ServiceConfig:
    return ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )


def test_normal_start_publishes_bound_port_before_resolver_init(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=41001,
        pid=os.getpid(),
        version="old",
    )

    original_resolver = app_module.daemon_resolver
    observed = {}

    def resolver(cfg):
        table = routing.read_table(tmp_path)
        observed["table"] = table
        return original_resolver(cfg)

    monkeypatch.setattr(app_module, "daemon_resolver", resolver)

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 41002
    app.state.publish_on_ready = True
    app.state.supersession_client_factory = lambda _ep: SimpleNamespace(
        drain=lambda **_kwargs: {"drained": True},
        shutdown=lambda: {"shutting_down": True},
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    table = observed["table"]
    assert table["active"]["port"] == 41002
    assert table["active"]["pid"] is not None
    assert table["active"]["generation"] == 2
    assert table["previous"]["port"] == 41001


def test_passive_start_does_not_publish_before_resolver_init(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    old = routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=42001,
        pid=222,
        version="old",
    )

    original_resolver = app_module.daemon_resolver
    observed = {}

    def resolver(cfg):
        observed["table"] = routing.read_table(tmp_path)
        return original_resolver(cfg)

    monkeypatch.setattr(app_module, "daemon_resolver", resolver)

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 42002
    app.state.publish_on_ready = False
    app.state.supersession_client_factory = lambda _ep: pytest.fail(
        "passive startup must not retire the orchestrator-owned predecessor"
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    table = observed["table"]
    assert table["active"]["port"] == 42001
    assert table["active"]["generation"] == old.generation
    assert "previous" not in table


def test_normal_start_without_previous_does_not_start_retirement(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 42502
    app.state.publish_on_ready = True
    app.state.supersession_client_factory = lambda _ep: pytest.fail(
        "startup without a predecessor must not create a retirement client"
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    table = routing.read_table(tmp_path)
    assert table["previous"]["port"] == 42502


def test_startup_failure_restores_previous_route(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=43001,
        pid=os.getpid(),
        version="old",
    )

    def fail_resolver(cfg):
        raise RuntimeError("resolver failed")

    monkeypatch.setattr(app_module, "daemon_resolver", fail_resolver)

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 43002
    app.state.publish_on_ready = True

    with pytest.raises(RuntimeError, match="resolver failed"):
        with TestClient(app):
            pass

    table = routing.read_table(tmp_path)
    assert table["active"]["port"] == 43001
    assert table["active"]["pid"] == os.getpid()


def test_startup_failure_does_not_overwrite_newer_successor(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=43101,
        pid=os.getpid(),
        version="old",
    )

    def fail_after_successor_publish(cfg):
        routing.publish_active(
            tmp_path,
            bind="127.0.0.1",
            port=43103,
            pid=333,
            version="successor",
            demote_existing=True,
        )
        raise RuntimeError("resolver failed")

    monkeypatch.setattr(
        app_module, "daemon_resolver", fail_after_successor_publish
    )

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 43102
    app.state.publish_on_ready = True

    with pytest.raises(RuntimeError, match="resolver failed"):
        with TestClient(app):
            pass

    table = routing.read_table(tmp_path)
    assert table["active"]["port"] == 43103
    assert table["active"]["pid"] == 333


def test_promoted_passive_daemon_clears_its_route_on_shutdown(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 44002
    app.state.publish_on_ready = False

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        routing.publish_active(
            tmp_path,
            bind="127.0.0.1",
            port=44002,
            pid=os.getpid(),
            version="promoted",
        )

    table = routing.read_table(tmp_path)
    assert "active" not in table
    assert table["previous"]["port"] == 44002


def test_promoted_passive_daemon_does_not_clear_newer_successor(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 44102
    app.state.publish_on_ready = False

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        routing.publish_active(
            tmp_path,
            bind="127.0.0.1",
            port=44102,
            pid=os.getpid(),
            version="promoted",
        )
        routing.publish_active(
            tmp_path,
            bind="127.0.0.1",
            port=44103,
            pid=444,
            version="successor",
            demote_existing=True,
        )

    table = routing.read_table(tmp_path)
    assert table["active"]["port"] == 44103
    assert table["active"]["pid"] == 444
