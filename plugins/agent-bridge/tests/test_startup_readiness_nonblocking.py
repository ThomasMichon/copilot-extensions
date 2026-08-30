"""Liveness is available while slow readiness work finishes in background."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import agent_bridge.app as app_module
from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig


class _FakeRelay:
    running = True
    port = 9857

    async def stop(self):
        self.running = False


def test_health_does_not_block_on_slow_topology_readiness(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    entered = threading.Event()
    release = threading.Event()

    def slow_resolver(cfg):
        entered.set()
        assert release.wait(timeout=10)
        return SimpleNamespace(
            agents={},
            machines={},
            topology_errors=[],
            topology_warnings=[],
            list_agents_async=lambda: [],
        )

    monkeypatch.setattr(app_module, "daemon_resolver", slow_resolver)

    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.bound_port = 45001
    app.state.publish_on_ready = False
    app.state.background_readiness = True

    start = time.monotonic()
    with TestClient(app) as client:
        startup_elapsed = time.monotonic() - start
        client.headers["Authorization"] = "Bearer test-token"
        assert entered.wait(timeout=2)

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["ready"] is False
        assert health.json()["topology_ready"] is False

        agents = client.get("/api/v1/agents")
        assert agents.status_code == 200
        assert agents.json()["agents"] == []

        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            health = client.get("/health")
            if health.json()["ready"]:
                break
            time.sleep(0.02)

        assert health.json()["ready"] is True
        assert health.json()["topology_ready"] is True

    assert startup_elapsed < 2.0


def test_health_does_not_block_on_slow_credential_relay(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    entered = threading.Event()
    release = threading.Event()

    async def slow_relay(app):
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return _FakeRelay()

    monkeypatch.setattr(app_module, "_start_credential_relay", slow_relay)

    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=True,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.bound_port = 45002
    app.state.publish_on_ready = False
    app.state.background_readiness = True

    start = time.monotonic()
    with TestClient(app) as client:
        startup_elapsed = time.monotonic() - start
        assert entered.wait(timeout=2)

        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["ready"] is False
        assert health["credential_relay_ready"] is False

        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            health = client.get("/health").json()
            if health["ready"]:
                break
            time.sleep(0.02)

        assert health["ready"] is True
        assert health["topology_ready"] is True
        assert health["credential_relay_ready"] is True

    assert startup_elapsed < 2.0


def test_background_readiness_retries_transient_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    attempts = {"count": 0}

    def flaky_resolver(cfg):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return SimpleNamespace(
            agents={},
            machines={},
            topology_errors=[],
            topology_warnings=[],
        )

    monkeypatch.setattr(app_module, "daemon_resolver", flaky_resolver)
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.publish_on_ready = False
    app.state.background_readiness = True

    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            health = client.get("/health").json()
            if health["ready"]:
                break
            time.sleep(0.02)

        assert health["ready"] is True
        assert "readiness_error" not in health
        assert attempts["count"] >= 2


def test_shutdown_does_not_wait_for_blocked_topology_thread(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    entered = threading.Event()
    release = threading.Event()

    def blocked_resolver(cfg):
        entered.set()
        release.wait(timeout=10)
        return SimpleNamespace(agents={}, machines={})

    monkeypatch.setattr(app_module, "daemon_resolver", blocked_resolver)
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.publish_on_ready = False
    app.state.background_readiness = True

    client = TestClient(app)
    client.__enter__()
    assert entered.wait(timeout=2)
    start = time.monotonic()
    client.__exit__(None, None, None)
    shutdown_elapsed = time.monotonic() - start
    release.set()

    assert shutdown_elapsed < 2.0
