"""Exact managed sends do not mistake listing visibility for target validity."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as core
from agent_bridge.bridge_inventory import project_bridges
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.routes.sessions import router
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_status import SessionStatus
from agent_bridge.transport import SpawnTarget
from test_session_manager import _patch_acp as _patch_acp, _patch_spawn as _patch_spawn


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,accepted", [
    (["--all-projects"], True),
    (["--project", "unrelated"], False),
    (["--all-projects", "--machine", "other-host"], False),
    (["--all-projects", "--namespace", "other-owner"], False),
])
async def test_exact_stopped_anchor_uses_real_projection_and_preserves_scope(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    scope: list[str], accepted: bool,
) -> None:
    session = await session_manager.start_session(spawn_target)
    session.status = SessionStatus.STOPPED
    session_manager.db.update_session_status(session.session_id, "stopped", session.updated_at)
    mock_acp_client.is_running = False
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router)
    client = BridgeClient("http://fixture.invalid", "fixture")
    posts = []
    with TestClient(app) as api:
        def request(method: str, path: str, body: Any = None, **options: Any) -> Any:
            if path == "/health":
                return {"protocol_version": HTTP_PROTOCOL_VERSION, "capabilities": {
                    "bridge_inventory": 2, "durable_managed_messages": 1,
                }}
            if path == "/api/v1/bridges":
                info = api.get(f"/api/v1/sessions/{session.session_id}").json()
                report = project_bridges([{
                    "kind": "managed", "handle": session.session_id,
                    "sources": ["managed"], "session": info,
                }], {"managed": {"availability": "available"}})
                assert report["bridges"] == []
                return {**report, "host": "host-a"}
            if method == "POST":
                posts.append((path, body))
            response = api.request(method, path, json=body)
            if response.is_error:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            return response.json()

        monkeypatch.setattr(client, "_request", request)
        monkeypatch.setattr(core, "_get_client", lambda **_kwargs: client)
        monkeypatch.setattr(core, "_sender_repo", lambda: "demo")
        monkeypatch.setattr(core, "_caller_id_for", lambda _args: None)
        root_scope = scope if scope[0] == "--project" else []
        send_scope = [] if root_scope else scope
        argv = [
            "--json", *root_scope, "send", session.session_id, "original input",
            "--idempotency-key", "original", *send_scope,
        ]
        if accepted:
            core.main(argv)
            result = json.loads(capsys.readouterr().out)
            assert result["outcome"] == "queued" and result["session_id"] == session.session_id
            pending, = session_manager.db.list_pending_prompts(session.session_id)
            assert pending["id"] == result["queue_id"] and pending["prompt"] == "original input"
            assert len(posts) == 1
        else:
            with pytest.raises(SystemExit) as error:
                core.main(argv)
            assert error.value.code == 69
            assert json.loads(capsys.readouterr().out)["outcome"] == "refused_unavailable"
            assert posts == [] and session_manager.db.list_pending_prompts(session.session_id) == []
        assert session.status == SessionStatus.STOPPED and session.turn_count == 0
        mock_acp_client.send_prompt.assert_not_awaited()
