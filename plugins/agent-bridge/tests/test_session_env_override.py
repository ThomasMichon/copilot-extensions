"""Tests for per-session env overrides on ``POST /api/v1/sessions``.

A caller may pass an ``env`` map that is merged onto the resolved agent's
declared env and applied to the spawned Copilot CLI process -- the primary use
being BYOK provider selection (pointing a session's brain at a local inference
front via ``COPILOT_PROVIDER_BASE_URL`` / ``COPILOT_MODEL``). Per-session values
win over the agent's own env.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.models import SessionStatus
from agent_bridge.routes import sessions as sessions_route
from agent_bridge.transport import SpawnTarget


class _CapturingMgr:
    """Session manager stub that captures the SpawnTarget it is handed."""

    is_draining = False

    def __init__(self) -> None:
        self.target: SpawnTarget | None = None

    def list_sessions(self, status=None):  # noqa: ANN001, ARG002
        return []

    async def start_session(self, target, **kwargs):  # noqa: ANN001, ARG002
        self.target = target

        class _S:
            session_id = "new-sess"
            name = "swift-forge"
            status = SessionStatus.IDLE

        return _S()


@pytest.fixture
def client():
    app = FastAPI()
    mgr = _CapturingMgr()
    app.state.session_manager = mgr
    app.include_router(sessions_route.router)
    tc = TestClient(app)
    tc._mgr = mgr
    return tc


def test_env_merged_onto_bare_local_target(client):
    byok = {
        "COPILOT_PROVIDER_BASE_URL": "http://localhost:8090/api/v1",
        "COPILOT_MODEL": "qwen3",
        "COPILOT_OFFLINE": "true",
    }
    r = client.post("/api/v1/sessions", json={"env": byok})
    assert r.status_code == 201
    assert client._mgr.target is not None
    assert client._mgr.target.env == byok


def test_no_env_leaves_target_env_untouched(client):
    r = client.post("/api/v1/sessions", json={})
    assert r.status_code == 201
    assert client._mgr.target is not None
    assert client._mgr.target.env == {}


def test_per_session_env_overrides_resolved_agent_env(client, monkeypatch):
    # A resolver returns a target carrying an agent-declared env; the
    # per-session env wins on key collision and adds new keys.
    class _Resolver:
        async def resolve_async(self, agent, sender_repo=None):  # noqa: ANN001, ARG002
            return SpawnTarget(
                type="local",
                cwd=".",
                env={"COPILOT_MODEL": "gemma", "KEEP": "1"},
            )

    client.app.state.resolver = _Resolver()
    r = client.post(
        "/api/v1/sessions",
        json={"agent": "some-agent", "env": {"COPILOT_MODEL": "qwen3", "NEW": "2"}},
    )
    assert r.status_code == 201
    env = client._mgr.target.env
    assert env["COPILOT_MODEL"] == "qwen3"  # per-session wins
    assert env["KEEP"] == "1"               # agent env preserved
    assert env["NEW"] == "2"                # per-session addition


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
