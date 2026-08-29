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
import time

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import SessionManager


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )


def test_startup_does_not_block_on_slow_reattach(tmp_path, monkeypatch):
    invoked = {"called": False}

    async def slow_reattach(self, *args, **kwargs):
        # Records invocation, then blocks far longer than the daemon's other
        # startup work. If startup awaited this inline, entering the TestClient
        # context (which runs the lifespan startup) would take ~60s+; with the
        # reattach backgrounded it returns in the daemon's normal startup time.
        invoked["called"] = True
        await asyncio.sleep(60)
        return 0

    monkeypatch.setattr(SessionManager, "reattach_session_hosts", slow_reattach)

    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db")
    )
    app = create_app(config=cfg, token="test-token")

    start = time.monotonic()
    with TestClient(app) as client:
        startup_elapsed = time.monotonic() - start
        client.headers["Authorization"] = "******"
        # Serving works while the (backgrounded) reattach is still sleeping.
        resp = client.get("/health")

    assert resp.status_code == 200
    # Startup returned well before the 60s reattach could finish -> it was
    # backgrounded, not awaited. The generous margin above the daemon's normal
    # startup keeps the assertion from being timing-flaky.
    assert startup_elapsed < 40.0, (
        f"startup blocked on reattach ({startup_elapsed:.1f}s); "
        "it must run as a background task"
    )
    # The reattach was still scheduled/invoked (a /health round-trip gave the
    # lifespan loop a chance to run the task's first line).
    assert invoked["called"] is True
