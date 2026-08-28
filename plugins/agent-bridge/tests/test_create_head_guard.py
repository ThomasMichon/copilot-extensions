"""Tests for the session-lifecycle create guard (agent-fabric
`single-current-session-per-worktree`).

Creating a session *into an existing worktree* whose ground-layer head is still
``active`` is refused with a structured 409 enumerating reuse / handoff /
sunset; ``reclaim=true`` is the break-glass that bypasses it. The head is
*derived* from agent-worktrees (via ``worktree_head.resolve_head``) -- the guard
keeps no rival pointer and fails **open** when the ground layer can't be read.

This is the create-time sibling of the ``resume_worktree`` liveness guard
(``live_cli_holds_worktree``); together they enforce one current session per
worktree.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent_bridge import worktree_head
from agent_bridge.models import SessionStatus
from agent_bridge.routes import sessions as sessions_route
from agent_bridge.worktree_head import HeadInfo, parse_head_payload


# --- pure parser: the ground-layer envelope -> HeadInfo mapping --------------

def test_parse_active_head():
    hi = parse_head_payload(
        '{"worktree_id": "wt-a", "tracked": true, "head_session": "sess-A", '
        '"active": true, "state": "active"}'
    )
    assert hi == HeadInfo(
        active=True, occupied=True, head_session="sess-A", state="active",
        tracked=True,
    )


def test_parse_concluded_head_is_inactive():
    hi = parse_head_payload(
        '{"worktree_id": "wt-a", "tracked": true, "head_session": null, '
        '"active": false, "state": null}'
    )
    assert hi.active is False
    assert hi.head_session is None
    assert hi.tracked is True


def test_parse_untracked_fails_open():
    hi = parse_head_payload(
        '{"worktree_id": "wt-x", "tracked": false, "head_session": null, '
        '"active": false, "state": null}'
    )
    assert hi.active is False
    assert hi.tracked is False


@pytest.mark.parametrize("bad", ["", "   ", "not json", "[1,2,3]", None])
def test_parse_malformed_fails_open(bad):
    # Any non-object / unparseable payload degrades to an inactive, untracked
    # head so the guard never blocks a create on a bad read.
    hi = parse_head_payload(bad)
    assert hi.active is False
    assert hi.tracked is False


# --- the guard function ------------------------------------------------------

def test_guard_raises_on_active_head(monkeypatch):
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=True, occupied=True, head_session="sess-A",
                             state="active", tracked=True),
    )
    with pytest.raises(HTTPException) as ei:
        sessions_route._enforce_worktree_head_guard("wt-a")
    exc = ei.value
    assert exc.status_code == 409
    detail = exc.detail
    assert detail["reason"] == "worktree_head_active"
    assert detail["worktree_id"] == "wt-a"
    assert detail["head_session"] == "sess-A"
    # The three deliberate resolutions, in order, with reuse preferred.
    assert [c["action"] for c in detail["choices"]] == [
        "reuse", "handoff", "sunset"]
    assert detail["choices"][0]["preferred"] is True
    assert "reclaim" in detail["override"]


def test_guard_permits_when_inactive(monkeypatch):
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=False, tracked=True),
    )
    # No raise -> create proceeds.
    assert sessions_route._enforce_worktree_head_guard("wt-a") is None


def test_guard_raises_on_pending_handoff(monkeypatch):
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=False, occupied=True, tracked=True),
    )
    with pytest.raises(HTTPException) as exc:
        sessions_route._enforce_worktree_head_guard("wt-a")
    assert exc.value.detail["reason"] == "worktree_head_pending"
    assert "pending handoff" in exc.value.detail["message"]


def test_guard_fails_open_on_untracked(monkeypatch):
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=False, tracked=False),
    )
    assert sessions_route._enforce_worktree_head_guard("wt-unknown") is None


# --- route wiring ------------------------------------------------------------

class _StubMgr:
    """Minimal session manager: satisfies the pre-guard drain check and, for the
    bypass test, a fake spawn that never touches a real subprocess.
    """

    is_draining = False

    def __init__(self):
        self.started = False

    def list_sessions(self, status=None):  # noqa: ARG002 - caller-affinity path
        return []

    async def start_session(self, target, **kwargs):  # noqa: ANN001, ARG002
        self.started = True

        class _S:
            session_id = "new-sess"
            name = "swift-forge"
            status = SessionStatus.IDLE

        return _S()


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    mgr = _StubMgr()
    app.state.session_manager = mgr
    app.include_router(sessions_route.router)
    tc = TestClient(app)
    tc._mgr = mgr  # expose for assertions
    return tc


def test_route_refuses_create_into_active_head(client, monkeypatch):
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=True, occupied=True, head_session="sess-A",
                             state="active", tracked=True),
    )
    r = client.post("/api/v1/sessions", json={"worktree_id": "wt-a"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "worktree_head_active"
    assert detail["head_session"] == "sess-A"
    # The guard fired *before* any spawn.
    assert client._mgr.started is False


def test_route_reclaim_bypasses_guard(client, monkeypatch):
    called = {"resolve": False}

    def _resolve(wid):
        called["resolve"] = True
        return HeadInfo(active=True, occupied=True, head_session="sess-A", state="active",
                        tracked=True)

    monkeypatch.setattr(worktree_head, "resolve_head", _resolve)
    # reclaim=true skips the guard entirely: resolve_head is never consulted and
    # the (stubbed) spawn proceeds to a 201.
    r = client.post(
        "/api/v1/sessions", json={"worktree_id": "wt-a", "reclaim": True})
    assert r.status_code == 201
    assert called["resolve"] is False
    assert client._mgr.started is True


def test_route_no_worktree_id_skips_guard(client, monkeypatch):
    called = {"resolve": False}

    def _resolve(wid):
        called["resolve"] = True
        return HeadInfo(active=True, tracked=True)

    monkeypatch.setattr(worktree_head, "resolve_head", _resolve)
    # A brand-new worktree (no worktree_id) never has a head to guard.
    r = client.post("/api/v1/sessions", json={})
    assert r.status_code == 201
    assert called["resolve"] is False
