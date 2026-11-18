"""Startup must not block on Session-Host reattach (dotfiles #1932).

A slow far-side authority recovery (an unreachable remote venue) can stall
``reattach_session_hosts`` for its whole remote-recovery budget. When that ran
inline on the lifespan startup path it delayed serving and could push startup
past the self-watchdog grace (#166), causing a reap/restart flap and a stale
``active.json``. The reattach is now scheduled as a background task, so the
daemon serves immediately while reattach completes concurrently.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from threading import Event
import time
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from zdd import process_exit, routing

from agent_bridge import passive_maintenance
from agent_bridge.app import create_app
from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import SessionManager


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )


def test_startup_does_not_block_on_slow_reattach(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invoked = {"called": False}

    async def slow_reattach(self: SessionManager, *args: object, **kwargs: object) -> int:
        # Records invocation, then blocks well beyond this (relay-disabled)
        # daemon's startup. If startup awaited it inline, entering the
        # TestClient context would take >= 10s; backgrounded, it returns in the
        # daemon's normal (sub-second) startup time.
        invoked["called"] = True
        await asyncio.sleep(10)
        return 0

    monkeypatch.setattr(SessionManager, "reattach_session_hosts", slow_reattach)

    # Disable the credential relay so the lifespan's own startup is fast and the
    # threshold isolates the reattach contribution (not relay/socket setup).
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")

    start = time.monotonic()
    with TestClient(app) as client:
        startup_elapsed = time.monotonic() - start
        client.headers["Authorization"] = "******"
        # Serving works while the (backgrounded) reattach is still sleeping.
        resp = client.get("/health")

    assert resp.status_code == 200
    # Startup returned in the daemon's normal (fast) time, far below the 10s
    # reattach -> it was backgrounded, not awaited. A broken (inline-await)
    # version fails in ~10s rather than hanging.
    assert startup_elapsed < 5.0, (
        f"startup blocked on reattach ({startup_elapsed:.1f}s); "
        "it must run as a background task"
    )
    # The reattach was still scheduled/invoked (a /health round-trip gave the
    # lifespan loop a chance to run the task's first line).
    assert invoked["called"] is True


@pytest.mark.parametrize("passive", [True, False], ids=["passive-candidate", "cold-start"])
def test_only_cold_start_concludes_an_open_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, passive: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(root / "sessions.db"),
                        enable_credential_relay=False)
    owner = Database(cfg.db_path)
    try:
        now = time.time()
        owner.create_session("active", "active", None, str(tmp_path), "local", "running", now,
                             acp_session_id="90abe1bc-b6d2-48b2-965d-b8fa6451b099")
        owner.create_turn("active", 0, "unfinished work", now)
        original_turn = owner.get_turn("active", 0)
        original_events = owner.get_events("active")
        app = create_app(config=cfg, token="test-token")
        app.state.passive = passive
        denied, promoted, beat = Event(), Event(), Event()

        def ownership(root: Path, port: int | None) -> bool:
            if promoted.is_set():
                return True
            denied.set()
            return False

        def note_heartbeat(self: SessionManager) -> int:
            beat.set()
            return 0

        resync = AsyncMock(return_value=0)
        monkeypatch.setattr("agent_bridge.app._HEARTBEAT_INTERVAL_S", .01)
        monkeypatch.setattr("agent_bridge.passive_maintenance.owns_session_maintenance", ownership)
        monkeypatch.setattr(SessionManager, "note_heartbeats", note_heartbeat)
        monkeypatch.setattr(SessionManager, "recover_disconnected_hosts", AsyncMock(return_value=0))
        monkeypatch.setattr(SessionManager, "resync_session", resync)
        with TestClient(app):
            if passive:
                assert denied.wait(3) and not beat.is_set()
                resync.assert_not_awaited()
            turn = owner.get_turn("active", 0)
            session = owner.get_session("active")
            assert session and turn
            if passive:
                assert session["status"] == "running"
                assert turn == original_turn and turn["completed_at"] is None
                assert owner.get_events("active") == original_events
                promoted.set()
                assert beat.wait(3), "maintenance must resume after ownership transfers"
            else:
                assert session["status"] == "stopped"
                assert turn["stop_reason"] == "interrupted" and turn["completed_at"] is not None
    finally:
        owner.close()


def test_passive_maintenance_requires_route_and_predecessor_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = 12002
    active = routing.Endpoint("127.0.0.1", port, os.getpid(), process_start_time="200")
    previous = routing.Endpoint("127.0.0.1", 12001, 42, process_start_time="100")
    path = routing.routing_table_path(tmp_path)
    path.write_text(json.dumps({"active": active.to_dict(), "previous": previous.to_dict()}))
    monkeypatch.setattr(passive_maintenance, "process_start_time", Mock(return_value="200"))
    exited = Mock(return_value=False)
    monkeypatch.setattr(process_exit, "observe_original_exit", exited)

    assert not passive_maintenance.owns_session_maintenance(tmp_path, port)
    exited.assert_called_once_with(42, "100")
    exited.return_value = True
    assert passive_maintenance.owns_session_maintenance(tmp_path, port)

    exited.reset_mock()
    assert not passive_maintenance.owns_session_maintenance(tmp_path, port + 1)
    path.write_text(json.dumps({"active": active.to_dict(), "previous": {"port": 12001}}))
    assert not passive_maintenance.owns_session_maintenance(tmp_path, port)
    exited.assert_not_called()
