"""Live Session Host census in drain / drain_status / health (dotfiles#1656).

A drain/cutover must never report ``clean=True`` while a live Session Host it is
responsible for preserving goes *unaccounted for*. Under the detach-only redeploy
(dotfiles#1661) a host-backed turn is PRESERVED across the restart (detached, the
turn keeps running on the host, the successor reattaches) -- which is fine, but it
must be surfaced explicitly, never folded into a silent clean. These tests pin
that the census is visible in the drain result, the drain-status snapshot, and
/health.
"""

from __future__ import annotations

import os

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _mgr(tmp_path) -> SessionManager:
    return SessionManager(
        Database(tmp_path / "c.db"),
        session_host_state_dir=str(tmp_path / "hosts"),
    )


def _host_session(
    mgr: SessionManager, sid: str, *, boundary: str,
    status: SessionStatus = SessionStatus.RUNNING,
) -> Session:
    s = Session(sid, sid, SpawnTarget(type="command", cwd="/tmp/x"))
    s.status = status
    s.acp_session_id = "acp-" + sid
    mgr._sessions[sid] = s
    # A remote host's pid is a far-side pid (presumed alive); a local host's must
    # be a live pid so `_rec_host_alive` -> pid_alive() is True.
    host_pid = os.getpid() if boundary == "local" else 999_001
    mgr._host_index.register(HostRecord(
        session_id=sid, port=1, host_pid=host_pid, child_pid=host_pid,
        boundary=boundary,
    ))
    return s


def test_live_host_count_property(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    assert mgr.live_host_count == 0
    _host_session(mgr, "h1", boundary="codespace")
    _host_session(mgr, "h2", boundary="local")
    assert mgr.live_host_count == 2


@pytest.mark.asyncio
async def test_drain_result_surfaces_preserved_live_hosts(tmp_path) -> None:
    mgr = _mgr(tmp_path)  # detach-only default
    _host_session(mgr, "hosted", boundary="codespace")
    res = await mgr.drain(timeout=0.3, poll=0.05)
    # The host-backed turn is preserved (reattach) -- a clean drain, but the live
    # host is ACCOUNTED FOR, never hidden.
    assert res["clean"] is True
    assert res["preserved"] == ["hosted"]
    assert res["live_host_count"] == 1
    assert "hosted" not in res["busy_sessions"]


@pytest.mark.asyncio
async def test_drain_result_no_hosts_is_plainly_clean(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    res = await mgr.drain(timeout=0.3, poll=0.05)
    assert res["clean"] is True
    assert res["preserved"] == []
    assert res["live_host_count"] == 0


def test_drain_status_includes_live_host_count(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    _host_session(mgr, "hosted", boundary="codespace")
    assert mgr.drain_status()["live_host_count"] == 1


# -- /health --------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    from agent_bridge.app import create_app
    from agent_bridge.models import ServiceConfig
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml"))
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        yield c


def test_health_surfaces_live_host_count(client, app) -> None:
    body = client.get("/health").json()
    assert body["live_host_count"] == 0
    mgr = app.state.session_manager
    if mgr._host_index is not None:
        mgr._host_index.register(HostRecord(
            session_id="h", port=1, host_pid=999_001, child_pid=999_001,
            boundary="codespace",
        ))
        assert client.get("/health").json()["live_host_count"] == 1
