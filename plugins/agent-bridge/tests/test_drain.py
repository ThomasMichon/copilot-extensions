"""Tests for the graceful-drain primitive (Phase 1 zero-downtime)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import DaemonDrainingError, SessionManager


def _fake_session(status=SessionStatus.IDLE, bg=False):
    return SimpleNamespace(
        status=status,
        has_active_background_tasks=bg,
    )


# -- SessionManager.busy_sessions / drain ------------------------------------


def test_busy_sessions_detects_running_and_background(session_manager: SessionManager):
    session_manager._sessions = {
        "idle": _fake_session(SessionStatus.IDLE),
        "running": _fake_session(SessionStatus.RUNNING),
        "bg": _fake_session(SessionStatus.IDLE, bg=True),
    }
    busy = set(session_manager.busy_sessions())
    assert busy == {"running", "bg"}


@pytest.mark.asyncio
async def test_drain_clean_when_no_busy(session_manager: SessionManager):
    res = await session_manager.drain(timeout=1.0, poll=0.05)
    assert res["clean"] is True
    assert res["drained"] is True
    assert res["busy_sessions"] == []
    # Gate is now open: new work is refused.
    assert session_manager.is_draining is True


@pytest.mark.asyncio
async def test_drain_times_out_then_force(session_manager: SessionManager):
    session_manager._sessions = {"running": _fake_session(SessionStatus.RUNNING)}
    res = await session_manager.drain(timeout=0.2, poll=0.05, force=False)
    assert res["clean"] is False
    assert res["drained"] is False
    assert res["busy_sessions"] == ["running"]

    res2 = await session_manager.drain(timeout=0.2, poll=0.05, force=True)
    assert res2["clean"] is False
    assert res2["drained"] is True  # forced past
    assert res2["forced"] is True


@pytest.mark.asyncio
async def test_drain_completes_when_session_settles(session_manager: SessionManager):
    sess = _fake_session(SessionStatus.RUNNING)
    session_manager._sessions = {"s": sess}

    import asyncio

    async def settle():
        await asyncio.sleep(0.15)
        sess.status = SessionStatus.IDLE

    settle_task = asyncio.create_task(settle())
    res = await session_manager.drain(timeout=5.0, poll=0.05)
    await settle_task
    assert res["clean"] is True


@pytest.mark.asyncio
async def test_draining_gate_refuses_new_work(session_manager: SessionManager):
    session_manager.set_draining(True)
    with pytest.raises(DaemonDrainingError):
        await session_manager.start_session(SimpleNamespace())
    with pytest.raises(DaemonDrainingError):
        await session_manager.submit_prompt("whatever", "hi")


def test_undrain_releases_gate(session_manager: SessionManager):
    session_manager.set_draining(True)
    assert session_manager.is_draining is True
    session_manager.set_draining(False)
    assert session_manager.is_draining is False


# -- HTTP routes -------------------------------------------------------------


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml")
    )
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        yield c


def test_health_reports_draining(client, app):
    assert client.get("/health").json()["draining"] is False
    app.state.session_manager.set_draining(True)
    assert client.get("/health").json()["draining"] is True


def test_drain_endpoint_clean(client):
    res = client.post("/api/v1/drain", json={"timeout": 1, "poll": 0.05})
    assert res.status_code == 200
    body = res.json()
    assert body["clean"] is True
    assert body["drained"] is True


def test_drain_then_start_session_returns_503(client):
    client.post("/api/v1/drain", json={"timeout": 1, "poll": 0.05})
    res = client.post("/api/v1/sessions", json={"agent": "nonexistent-agent"})
    assert res.status_code == 503
    assert "draining" in res.json()["detail"].lower()


def test_undrain_endpoint(client, app):
    client.post("/api/v1/drain", json={"timeout": 1, "poll": 0.05})
    assert app.state.session_manager.is_draining is True
    res = client.post("/api/v1/undrain")
    assert res.status_code == 200
    assert app.state.session_manager.is_draining is False


# -- Drain observability + bounded lifetime (#1757) --------------------------


def test_set_draining_records_provenance(session_manager: SessionManager):
    session_manager.set_draining(True, reason="redeploy", source="test")
    status = session_manager.drain_status()
    assert status["draining"] is True
    assert status["reason"] == "redeploy"
    assert status["source"] == "test"
    assert status["since"] is not None
    assert status["held_s"] is not None

    session_manager.set_draining(False, source="test-clear")
    status = session_manager.drain_status()
    assert status["draining"] is False
    assert status["since"] is None
    assert status["reason"] is None


def test_set_draining_is_idempotent(session_manager: SessionManager):
    session_manager.set_draining(True, reason="first", source="a")
    since1 = session_manager.drain_status()["since"]
    # A redundant open must not reset the since timestamp or provenance.
    session_manager.set_draining(True, reason="second", source="b")
    status = session_manager.drain_status()
    assert status["since"] == since1
    assert status["reason"] == "first"
    assert status["source"] == "a"


@pytest.mark.asyncio
async def test_drain_watchdog_auto_releases(tmp_db, caplog):
    # A drain that outlives its budget with no handoff must self-heal by
    # auto-releasing the gate (#1757) -- otherwise the daemon 503s forever.
    import asyncio
    import logging

    mgr = SessionManager(
        tmp_db, drain_auto_release_s=0.2, drain_warn_interval_s=0.05
    )
    with caplog.at_level(logging.WARNING, logger="agent-bridge"):
        mgr.set_draining(True, reason="stuck-cutover", source="test")
        assert mgr.is_draining is True

        # Wait past the auto-release budget; the watchdog clears the gate.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if not mgr.is_draining:
                break
    # Auto-released (not manually) -- the gate is open no longer.
    assert mgr.is_draining is False
    assert mgr.drain_status()["draining"] is False
    assert any("auto-releasing" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_manual_undrain_cancels_watchdog(tmp_db):
    import asyncio

    mgr = SessionManager(
        tmp_db, drain_auto_release_s=10.0, drain_warn_interval_s=0.05
    )
    mgr.set_draining(True, source="test")
    assert mgr._drain_watchdog is not None
    mgr.set_draining(False, source="manual")
    await asyncio.sleep(0)  # let the cancellation propagate
    assert mgr.is_draining is False
    assert mgr._drain_watchdog is None


def test_health_exposes_drain_detail(client, app):
    # Idle: no drain block.
    assert "drain" not in client.get("/health").json()
    app.state.session_manager.set_draining(True, reason="redeploy", source="cutover")
    body = client.get("/health").json()
    assert body["draining"] is True
    assert body["drain"]["reason"] == "redeploy"
    assert body["drain"]["source"] == "cutover"
    assert body["drain"]["since"] is not None


def test_drain_endpoint_records_source(client, app):
    client.post(
        "/api/v1/drain",
        json={"timeout": 1, "poll": 0.05, "source": "cutover", "reason": "redeploy"},
    )
    status = app.state.session_manager.drain_status()
    assert status["source"] == "cutover"
    assert status["reason"] == "redeploy"
