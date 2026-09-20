"""Tests for the "slot" descriptor agent-bridge's ``/health`` renders.

process-slot-ownership Phase 5 (aperture-labs) parity slice: agent-dispatch's
coordinator `/health` already renders a `"slot"` descriptor (process -> slot ->
owner -> alive?); this proves agent-bridge's own `/health` does the same,
including that the published `self_retire` status is genuinely live-updated by
the running loop, not just a static snapshot of prebuilt state.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

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


def test_health_includes_slot_descriptor_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 45001
    app.state.publish_on_ready = True

    with TestClient(app) as client:
        slot = client.get("/health").json()["slot"]

    # Deliberately does NOT include "abandoned_passive_reap" -- see
    # slot_descriptor's docstring for why (that reap runs once, in the
    # separate `deploy` CLI process, not this daemon's own event loop).
    assert set(slot) == {"pid", "role", "active", "previous", "self_retire"}
    assert isinstance(slot["pid"], int)
    assert slot["role"] in ("active", "passive", "unknown")
    assert set(slot["self_retire"]) == {
        "enabled", "armed", "generation", "superseded", "confirms",
    }


def test_self_retire_status_lifecycle_reflects_the_live_loop(tmp_path, monkeypatch):
    """Prove the published status tracks the running self-retire loop
    end-to-end (armed -> generation -> superseded/confirms), not just a
    prebuilt snapshot. `is_superseded` is monkeypatched (rather than standing
    up a real listening successor process) to keep this fast and
    deterministic -- mirrors agent-dispatch's coordinator coverage
    (test_coordinator.py::test_self_retire_status_lifecycle_reflects_the_live_loop)."""
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "1")
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE_POLL_S", "0.05")
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE_CONFIRMATIONS", "20")

    # Patched BEFORE the app/lifespan starts: the loop's `from .self_retire
    # import is_superseded` binds this lambda once, for the coroutine's whole
    # lifetime, so flipping the mutable flag later is what changes its answer.
    import agent_bridge.app as app_module

    supersede_flag = {"value": False}
    monkeypatch.setattr(
        app_module, "_count_active_sessions", lambda *a, **k: 0,
    )
    import agent_bridge.self_retire as self_retire_mod

    monkeypatch.setattr(
        self_retire_mod, "is_superseded", lambda *a, **k: supersede_flag["value"],
    )

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 45101
    app.state.publish_on_ready = True

    with TestClient(app) as client:

        def _wait(predicate, *, timeout=5.0):
            deadline = time.monotonic() + timeout
            last = None
            while time.monotonic() < deadline:
                last = client.get("/health").json()["slot"]["self_retire"]
                if predicate(last):
                    return last
                time.sleep(0.02)
            pytest.fail(f"condition not met within {timeout}s; last status={last}")

        # 1. Arms once it observes itself as the routing table's active pid,
        #    capturing its own generation.
        armed = _wait(lambda s: s["armed"])
        assert armed["generation"] is not None

        # 2. Not superseded while nothing supersedes it: the loop keeps
        #    polling and keeps writing superseded: False / confirms: 0 --
        #    proving the status is live-updated on every cycle, not written
        #    once and forgotten.
        for _ in range(3):
            status = client.get("/health").json()["slot"]["self_retire"]
            assert status["superseded"] is False
            assert status["confirms"] == 0
            time.sleep(0.05)

        # 3. Once superseded (simulated by flipping the patched predicate),
        #    confirms climb toward the confirmation threshold and
        #    superseded flips true.
        supersede_flag["value"] = True
        status = _wait(lambda s: s["superseded"] and s["confirms"] >= 1)
        assert status["superseded"] is True
        assert status["confirms"] >= 1


def test_slot_role_reflects_own_pid_as_active(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 45201
    app.state.publish_on_ready = True

    with TestClient(app) as client:
        slot = client.get("/health").json()["slot"]

    assert slot["role"] == "active"
    assert slot["active"]["pid"] == os.getpid()


def test_slot_role_reflects_passive_when_not_promoted(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=45301, pid=999999, version="1.0",
    )

    app = create_app(config=_config(tmp_path), token="test-token")
    app.state.bound_port = 45302
    app.state.publish_on_ready = False

    with TestClient(app) as client:
        slot = client.get("/health").json()["slot"]

    assert slot["role"] == "passive"
    assert slot["active"]["pid"] == 999999
