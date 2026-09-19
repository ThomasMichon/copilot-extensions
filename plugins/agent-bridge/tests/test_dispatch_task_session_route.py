"""Integration tests for GET /api/v1/dispatch-tasks/{id}/session.

*resolve-by-any-origin-reference* (``agent-dispatch-session-worktree-history``
Phase 2): a dispatch-task reference resolves to the same session a direct
session-id/worktree-id lookup would give, through the existing live-then-
cold-store resolver plus a worktree-scoped fallback. The agent-dispatch
coordinator itself is mocked at the ``agent_bridge.routes.dispatch_tasks``
module boundary (``fetch_task``/``fetch_attachments``) -- these tests only
exercise the route's own resolution/fallback logic, not the HTTP client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge import routes
from agent_bridge.app import create_app
from agent_bridge.cold_store import ColdStoreSession
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
        agent_dispatch_url="http://127.0.0.1:9847",
    )
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        yield c


def _live_session(app, sid, worktree_id="wt-1"):
    mgr: SessionManager = app.state.session_manager
    target = SpawnTarget(type="local", cwd="/wt", worktree_id=worktree_id)
    session = Session(sid, "calm-lake", target, "test-agent")
    session.status = SessionStatus.IDLE
    mgr._sessions[sid] = session
    return session


def _mock_dispatch(monkeypatch, *, task, attachments=()):
    monkeypatch.setattr(
        routes.dispatch_tasks, "fetch_task", AsyncMock(return_value=task),
    )
    monkeypatch.setattr(
        routes.dispatch_tasks, "fetch_attachments",
        AsyncMock(return_value=list(attachments)),
    )


def test_resolves_current_owner_live_session(app, client, monkeypatch):
    _live_session(app, "session-current")
    _mock_dispatch(monkeypatch, task={"owner_session_id": "session-current"})

    resp = client.get("/api/v1/dispatch-tasks/task-1/session")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "session-current"


def test_falls_through_to_attachment_history_when_owner_has_nothing_live(
    app, client, monkeypatch,
):
    """A released task has no live current owner -- its most recent detached
    session is still individually resolvable via cold-store."""
    mgr: SessionManager = app.state.session_manager
    cold = ColdStoreSession(session_id="session-old", status="ended")
    mgr.set_resolver(_ColdStoreResolver(cold))
    _mock_dispatch(
        monkeypatch,
        task={"owner_session_id": None},
        attachments=[{"session_id": "session-old"}],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-2/session")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "session-old"
    assert body["at_rest"] is True


def test_falls_back_to_worktree_latest_session_when_no_candidate_resolves(
    app, client, monkeypatch,
):
    """No owner, no attachment history hit -- but the task's target worktree
    still has its own known (live) bridge session."""
    _live_session(app, "session-in-worktree", worktree_id="wt-42")
    _mock_dispatch(
        monkeypatch,
        task={"owner_session_id": None, "target_worktree": "wt-42"},
        attachments=[],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-3/session")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "session-in-worktree"


def test_no_resolvable_session_is_404_not_error(app, client, monkeypatch):
    _mock_dispatch(
        monkeypatch,
        task={"owner_session_id": None, "target_worktree": "wt-gone"},
        attachments=[],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-4/session")
    assert resp.status_code == 404


def test_unknown_task_is_404(app, client, monkeypatch):
    _mock_dispatch(monkeypatch, task=None)

    resp = client.get("/api/v1/dispatch-tasks/does-not-exist/session")
    assert resp.status_code == 404


def test_malformed_task_fetch_is_502_not_leaking_exception_text(
    app, client, monkeypatch,
):
    """A malformed (non-object) task payload is an upstream error, not a
    404 -- and the response body must not leak the raw exception string."""
    monkeypatch.setattr(
        routes.dispatch_tasks, "fetch_task",
        AsyncMock(side_effect=ValueError("secret-connection-detail://x")),
    )
    monkeypatch.setattr(
        routes.dispatch_tasks, "fetch_attachments", AsyncMock(return_value=[]),
    )

    resp = client.get("/api/v1/dispatch-tasks/task-6/session")
    assert resp.status_code == 502
    assert "secret-connection-detail" not in resp.text


def test_missing_agent_dispatch_url_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db"),
        agent_dispatch_url="",
    )
    app = create_app(config=cfg, token="test-token")
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        resp = c.get("/api/v1/dispatch-tasks/task-5/session")
    assert resp.status_code == 503


class _ColdStoreRegistry:
    def __init__(self, client):
        self._client = client

    def get_client(self, capability):
        return self._client


class _ColdStoreResolver:
    def __init__(self, cold_session):
        client = AsyncMock()
        client.fetch_session.return_value = cold_session
        self.cold_store = _ColdStoreRegistry(client)
