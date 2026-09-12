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


class _KnownSessionsMgr:
    """A stub session manager reporting a fixed set of known session ids."""

    def __init__(self, session_ids):
        self._ids = list(session_ids)

    def list_sessions(self, status=None):  # noqa: ARG002
        class _S:
            def __init__(self, session_id):
                self.session_id = session_id

        return [_S(sid) for sid in self._ids]


def test_guard_permits_when_head_session_absent_from_bridge_store(monkeypatch):
    # The ground layer asserts an active head session, but agent-bridge's own
    # session store has never heard of it (e.g. a stale/orphaned head pointer
    # left behind when a worktree's cleanup was skipped). This must not block
    # a create forever -- the ground-layer assertion is unverifiable, not a
    # live conflict.
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=True, occupied=True, head_session="ghost-sess",
                             state="active", tracked=True),
    )
    mgr = _KnownSessionsMgr(["some-other-sess"])
    assert sessions_route._enforce_worktree_head_guard("wt-a", mgr) is None


def test_guard_still_blocks_when_head_session_known_to_bridge(monkeypatch):
    # A session id agent-bridge does know about (in any status) still fully
    # blocks -- only "never heard of it at all" downgrades the guard.
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=True, occupied=True, head_session="sess-A",
                             state="active", tracked=True),
    )
    mgr = _KnownSessionsMgr(["sess-A"])
    with pytest.raises(HTTPException) as ei:
        sessions_route._enforce_worktree_head_guard("wt-a", mgr)
    assert ei.value.detail["reason"] == "worktree_head_active"


def test_guard_blocks_when_no_mgr_supplied(monkeypatch):
    # Without a manager to cross-check against, behavior is unchanged from
    # before this fix: the ground-layer assertion is trusted as-is.
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=True, occupied=True, head_session="sess-A",
                             state="active", tracked=True),
    )
    with pytest.raises(HTTPException):
        sessions_route._enforce_worktree_head_guard("wt-a")


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

    ``known_session_ids`` seeds ``list_sessions()`` so the head-guard's
    cross-check treats those ids as sessions agent-bridge knows about (see
    ``test_guard_permits_when_head_session_absent_from_bridge_store``); any id
    not listed is treated as an unverifiable/stale ground-layer assertion.
    """

    is_draining = False

    def __init__(self, known_session_ids=()):
        self.started = False
        self._known_session_ids = list(known_session_ids)

    def list_sessions(self, status=None):  # noqa: ARG002 - caller-affinity path
        class _S:
            def __init__(self, session_id):
                self.session_id = session_id

        return [_S(sid) for sid in self._known_session_ids]

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
    mgr = _StubMgr(known_session_ids=("sess-A",))
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


def test_route_permits_create_when_head_session_is_a_bridge_ghost(monkeypatch):
    # Same asserted-active ground layer as test_route_refuses_create_into_active_head,
    # but agent-bridge's own store has no record of that session id at all --
    # a stale head pointer must not block the create end-to-end.
    app = FastAPI()
    mgr = _StubMgr(known_session_ids=())
    app.state.session_manager = mgr
    app.include_router(sessions_route.router)
    tc = TestClient(app)
    monkeypatch.setattr(
        worktree_head, "resolve_head",
        lambda wid: HeadInfo(active=True, occupied=True, head_session="ghost-sess",
                             state="active", tracked=True),
    )
    r = tc.post("/api/v1/sessions", json={"worktree_id": "wt-a"})
    assert r.status_code == 201
    assert mgr.started is True


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
