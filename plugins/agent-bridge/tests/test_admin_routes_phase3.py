"""Route-level tests for Phase 3's exit-contract release (``/api/v1/shutdown``)
and post-cutover reattach retry (``/api/v1/session-hosts/reattach``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig
from agent_bridge.session_host.host_index import HostRecord

_TEST_TOKEN = "test-token"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml")
    )
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db"))
    return create_app(config=cfg, token=_TEST_TOKEN)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {_TEST_TOKEN}"
        # A real deployment always has a uvicorn server handle by the time
        # /shutdown is reachable; simulate it so the release-then-shutdown
        # ordering (PR #4543 review) actually exercises the release path.
        app.state.uvicorn_server = SimpleNamespace(should_exit=False)
        yield c


def test_shutdown_reports_no_server_handle_without_released_claims(client, app):
    """No server handle -- shutdown must not release claims either (PR #4543
    review): a still-live daemon (shutdown could not actually be initiated)
    must never give up ownership it still holds."""
    app.state.uvicorn_server = None
    mgr = app.state.session_manager
    mgr._host_index.register(
        HostRecord(session_id="s1", port=9000, host_pid=111, child_pid=222)
    )
    mgr._host_index.claim(
        "s1", generation=mgr._generation_id, owner_pid=1, pid_alive=lambda p: True,
    )

    res = client.post("/api/v1/shutdown")
    assert res.status_code == 200
    body = res.json()
    assert body["shutting_down"] is False
    assert body["released_claims"] == []
    # The claim is still held -- nothing was released.
    assert mgr._host_index.get("s1").owner_generation == mgr._generation_id


def test_shutdown_releases_claims_held_by_this_generation(client, app):
    mgr = app.state.session_manager
    mgr._host_index.register(
        HostRecord(session_id="s1", port=9000, host_pid=111, child_pid=222)
    )
    mgr._host_index.register(
        HostRecord(session_id="s2", port=9001, host_pid=333, child_pid=444)
    )
    mgr._host_index.claim(
        "s1", generation=mgr._generation_id, owner_pid=1, pid_alive=lambda p: True,
    )
    mgr._host_index.claim(
        "s2", generation=mgr._generation_id, owner_pid=1, pid_alive=lambda p: True,
    )

    res = client.post("/api/v1/shutdown")
    assert res.status_code == 200
    body = res.json()
    assert set(body["released_claims"]) == {"s1", "s2"}
    assert mgr._host_index.get("s1").owner_generation == ""
    assert mgr._host_index.get("s2").owner_generation == ""


def test_shutdown_never_releases_a_different_generations_claim(client, app):
    mgr = app.state.session_manager
    mgr._host_index.register(
        HostRecord(session_id="s1", port=9000, host_pid=111, child_pid=222)
    )
    mgr._host_index.claim(
        "s1", generation="some-other-generation", owner_pid=1, pid_alive=lambda p: True,
    )

    res = client.post("/api/v1/shutdown")
    assert res.status_code == 200
    assert res.json()["released_claims"] == []
    assert mgr._host_index.get("s1").owner_generation == "some-other-generation"


def test_reattach_hosts_endpoint_calls_reattach_session_hosts(client, app, monkeypatch):
    mgr = app.state.session_manager
    reattach = AsyncMock(return_value=3)
    monkeypatch.setattr(mgr, "reattach_session_hosts", reattach)

    res = client.post("/api/v1/session-hosts/reattach")
    assert res.status_code == 200
    assert res.json() == {"reattached": 3}
    reattach.assert_awaited_once()
