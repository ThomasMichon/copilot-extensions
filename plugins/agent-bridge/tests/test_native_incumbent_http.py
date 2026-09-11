"""Native ownership is an explicit HTTP refusal, never a fallback ACP launch."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.models import SessionStatus
from agent_bridge.native_manager import NativeManager
from agent_bridge.native_store import NativeError
from agent_bridge.routes import sessions, worktrees
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget


def refusal():
    return NativeError("native_incumbent", "Native execution ownership blocks ACP fallback")


def expected_refusal():
    return {"detail": {"code": "native_incumbent",
                       "detail": "Native execution ownership blocks ACP fallback"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "resume"])
async def test_real_native_guard_returns_409_before_acp_allocation(tmp_path, action):
    native = NativeManager(tmp_path / "native", lambda: ["provider"])
    store = native.store(create=True)
    store.reserve("native", "generation", "request", "example-space", "owner", "hash", {})
    before = store.get("native", "generation")
    manager = object.__new__(SessionManager)
    manager._draining = False
    manager._sessions = {}
    manager.native_guard = native.assert_acp_allowed
    manager._resolve_ref = lambda ref: ref
    target = SpawnTarget(type="command", cwd="/workspaces/example",
                         codespace={"name": "example-space"}, caller_worktree="owner")
    app = FastAPI()
    app.include_router(sessions.router)
    app.state.session_manager = manager
    app.state.resolver = SimpleNamespace(resolve_async=AsyncMock(return_value=target))
    if action == "resume":
        manager._sessions["old-acp"] = SimpleNamespace(target=target)
        url, body = "/api/v1/sessions/old-acp/resume", {}
    else:
        url, body = "/api/v1/sessions", {"agent": "codespace:example-space"}
    previous_sessions = dict(manager._sessions)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.post(url, json=body)
    assert response.status_code == 409
    assert response.json() == expected_refusal()
    fixture = Path(__file__).parents[1] / "contract" / "fixtures" / "http" / "current" / "native-incumbent-error.json"
    captured = json.loads(fixture.read_text())["response"]
    assert captured == {"status_code": response.status_code, "json": response.json()}
    assert manager._sessions == previous_sessions
    assert store.get("native", "generation") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body,method", [
    ("/api/v1/sessions/old-acp/turns", {"prompt": "hello"}, "submit_prompt"),
    ("/api/v1/sessions/old-acp/turns", {"prompt": "hello", "queue": True}, "submit_or_queue_prompt"),
    ("/api/v1/sessions/old-acp/handoff", {}, "handoff_session"),
    ("/api/v1/worktrees/worktree/resume", {}, "resume_session"),
    ("/api/v1/worktrees/worktree/handoff", {}, "handoff_session"),
    ("/api/v1/worktrees/worktree/handoff-request",
     {"session_id": "old-acp", "seed_text": "Continue", "handoff_token": "token"}, "handoff_session"),
])
async def test_adapters_preserve_native_refusal_without_recreation(path, body, method):
    old = SimpleNamespace(session_id="old-acp", status=SessionStatus.STOPPED,
                          target=SimpleNamespace(worktree_id="worktree"))
    denied = AsyncMock(side_effect=refusal())
    manager = SimpleNamespace(list_sessions=lambda: [old], get_session=lambda _: old,
                              start_session=AsyncMock(side_effect=AssertionError("ACP fallback")))
    setattr(manager, method, denied)
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(sessions.router)
    app.include_router(worktrees.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.post(path, json=body)
    assert response.status_code == 409
    assert response.json() == expected_refusal()
    denied.assert_awaited_once()
    manager.start_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_state_unavailability_keeps_its_status():
    app = FastAPI()
    app.state.session_manager = SimpleNamespace(
        resume_session=AsyncMock(side_effect=NativeError("state_unavailable", "Authority unreadable", 503)),
    )
    app.include_router(sessions.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.post("/api/v1/sessions/old-acp/resume")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "state_unavailable", "detail": "Authority unreadable"}}
