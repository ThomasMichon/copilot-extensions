"""Integration tests for GET /api/v1/dispatch-tasks/{id}/session.

*resolve-by-any-origin-reference* (``agent-dispatch-session-worktree-history``
Phase 2, rewired for Phase 2.5a/#3389): a dispatch-task reference resolves to
the same session a direct session-id/worktree-id lookup would give, through
the existing live-then-cold-store resolver plus a worktree-scoped fallback.
The agent-dispatch coordinator is no longer reached over HTTP by this route
at all -- it's mocked here as a registered ``dispatch:`` namespace resolver
(the same process-boundary seam agent-codespaces/agent-containers already
use), so these tests exercise only the route's own resolution/fallback
logic, never an HTTP client.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agent_bridge.agent_registry_namespace import NamespaceResolver
from agent_bridge.agent_registry_common import NamespaceAgentInfo
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
    # Isolate the providers.d scan too -- an empty dir means
    # refresh_provider_resolvers() never touches the manually-registered
    # fake "dispatch" resolver these tests install.
    monkeypatch.setenv(
        "AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path / "providers.d"),
    )


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {app.state.auth_token}"
        yield c


def _live_session(app, sid, worktree_id="wt-1"):
    mgr: SessionManager = app.state.session_manager
    target = SpawnTarget(type="local", cwd="/wt", worktree_id=worktree_id)
    session = Session(sid, "calm-lake", target, "test-agent")
    session.status = SessionStatus.IDLE
    mgr._sessions[sid] = session
    return session


class _FakeDispatchResolver(NamespaceResolver):
    """Stands in for agent-dispatch's own `namespace-resolve` CLI seam."""

    def __init__(self, *, task=None, attachments=(), raise_not_found=False,
                 raise_bad_state=False, worktree_id=None):
        self._task = task
        self._attachments = list(attachments)
        self._raise_not_found = raise_not_found
        self._raise_bad_state = raise_bad_state
        self._worktree_id = worktree_id

    @property
    def prefix(self) -> str:
        return "dispatch"

    async def list(self) -> list[NamespaceAgentInfo]:
        return []

    async def resolve(self, name, *, extra_plugins=(), repo=None, repo_remote=None):
        if self._raise_not_found:
            raise KeyError(name)
        if self._raise_bad_state:
            raise ValueError(f"{name} is not bound to a worktree")
        return SpawnTarget(
            type="local",
            worktree_id=self._worktree_id,
            venue={
                "provider": "agent-dispatch", "target_id": name,
                "task": self._task, "attachments": self._attachments,
            },
        )


def _register_dispatch(app, **kwargs):
    resolver = _FakeDispatchResolver(**kwargs)
    app.state.resolver.register_namespace_resolver(resolver)
    return resolver


def test_resolves_current_owner_live_session(app, client):
    _live_session(app, "session-current")
    _register_dispatch(app, task={"owner_session_id": "session-current"})

    resp = client.get("/api/v1/dispatch-tasks/task-1/session")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "session-current"


def test_resolves_completed_headless_task_via_session_type_spawn_target(
    app, client,
):
    """A completed headless body (board-sweep/review worker) never binds a
    worktree -- the resolver returns `type: "session"` (no worktree_id,
    nothing spawnable), and the route resolves purely off `.venue`,
    unaffected by `SpawnTarget.type` (#3389 extension, Phase 2.5a)."""
    _live_session(app, "session-headless")
    resolver = _FakeDispatchResolver(
        task={"status": "completed", "owner_session_id": "session-headless"},
    )

    async def _resolve_as_session(name, *, extra_plugins=(), repo=None, repo_remote=None):
        return SpawnTarget(
            type="session",
            venue={
                "provider": "agent-dispatch", "target_id": name,
                "task": resolver._task, "attachments": [],
            },
        )

    resolver.resolve = _resolve_as_session
    app.state.resolver.register_namespace_resolver(resolver)

    resp = client.get("/api/v1/dispatch-tasks/task-headless/session")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "session-headless"


def test_falls_through_to_attachment_history_when_owner_has_nothing_live(
    app, client,
):
    """A released task has no live current owner -- its most recent detached
    session is still individually resolvable via cold-store."""
    mgr: SessionManager = app.state.session_manager
    cold = ColdStoreSession(session_id="session-old", status="ended")
    mgr.set_resolver(_ColdStoreResolver(cold))
    _register_dispatch(
        app,
        task={"owner_session_id": None},
        attachments=[{"session_id": "session-old"}],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-2/session")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "session-old"
    assert body["at_rest"] is True


def test_falls_back_to_worktree_latest_session_when_no_candidate_resolves(
    app, client,
):
    """No owner, no attachment history hit -- but the task's target worktree
    still has its own known (live) bridge session."""
    _live_session(app, "session-in-worktree", worktree_id="wt-42")
    _register_dispatch(
        app,
        task={"owner_session_id": None, "target_worktree": "wt-42"},
        attachments=[],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-3/session")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "session-in-worktree"


def test_resolves_owner_session_from_live_registration_registry(app, client):
    """A CLI-embodied task's owner_session_id is a real ACP id registered in
    the *live-sessions* registry (an interactive CLI session the bridge
    represents but does not own), not bridge-owned SessionManager or the
    cold-store provider -- this must still resolve, not 404."""
    db = app.state.db
    db.register_live_session(
        "11111111-1111-1111-1111-111111111111",
        machine="lambda-core", cwd="/wt", worktree_id="wt-embody",
        repo="aperture-labs", branch="main", pid=123, role=None,
        now=1000.0,
    )
    _register_dispatch(
        app,
        task={"owner_session_id": "11111111-1111-1111-1111-111111111111"},
    )

    resp = client.get("/api/v1/dispatch-tasks/task-embody/session")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["durable_session_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["acp_session_id"] == "11111111-1111-1111-1111-111111111111"


def test_falls_back_to_worktree_latest_live_registration(app, client):
    """No owner, no attachment history hit, no bridge-owned worktree
    session -- but the task's target worktree has a current *interactive*
    CLI session registered."""
    db = app.state.db
    db.register_live_session(
        "22222222-2222-2222-2222-222222222222",
        machine="lambda-core", cwd="/wt", worktree_id="wt-embody-2",
        repo="aperture-labs", branch="main", pid=456, role=None,
        now=time.time(),
    )
    _register_dispatch(
        app,
        task={"owner_session_id": None, "target_worktree": "wt-embody-2"},
        attachments=[],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-embody-2/session")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "22222222-2222-2222-2222-222222222222"


def test_no_resolvable_session_is_404_not_error(app, client):
    _register_dispatch(
        app,
        task={"owner_session_id": None, "target_worktree": "wt-gone"},
        attachments=[],
    )

    resp = client.get("/api/v1/dispatch-tasks/task-4/session")
    assert resp.status_code == 404


def test_unknown_task_is_404(app, client):
    _register_dispatch(app, raise_not_found=True)

    resp = client.get("/api/v1/dispatch-tasks/does-not-exist/session")
    assert resp.status_code == 404


def test_bad_state_task_degrades_to_404_not_error(app, client):
    """A task not yet bound to a worktree (or cross-machine) is a legitimate,
    expected degrade -- never a hard error."""
    _register_dispatch(app, raise_bad_state=True)

    resp = client.get("/api/v1/dispatch-tasks/task-unbound/session")
    assert resp.status_code == 404


def test_malformed_task_payload_is_502_not_leaking_exception_text(app, client):
    """A resolver that returns no usable task record is an upstream error,
    not a 404 -- and the response body must not leak internals."""
    _register_dispatch(app, task=None)

    resp = client.get("/api/v1/dispatch-tasks/task-6/session")
    assert resp.status_code == 502


def test_missing_dispatch_provider_is_503(app, client):
    """No `dispatch:` namespace resolver registered at all (agent-dispatch
    not installed/running) degrades this one namespace, per
    a-la-carte-independence -- never a bridge-wide failure."""
    resp = client.get("/api/v1/dispatch-tasks/task-5/session")
    assert resp.status_code == 503


class _ColdStoreRegistry:
    def __init__(self, client):
        self._client = client

    def get_client(self, capability):
        return self._client


class _ColdStoreResolver:
    def __init__(self, cold_session):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.fetch_session.return_value = cold_session
        self.cold_store = _ColdStoreRegistry(client)
