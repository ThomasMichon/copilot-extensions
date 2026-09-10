"""Native service/auth/routing contracts without an ACP surrogate session."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_bridge import __main__ as cli
from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.native_store import NativeError
from agent_bridge.routes import native
from agent_bridge.session_manager import SessionManager


@pytest.mark.asyncio
async def test_native_http_auth_and_generation_are_not_optional():
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token="fixture-token")
    app.state.native_manager = SimpleNamespace(
        stop=AsyncMock(return_value={"state": "stopped"}),
        start=AsyncMock(return_value={"executionId": "one", "generation": "generation", "mode": "native"}),
    )
    app.include_router(native.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        assert (await client.post("/api/v1/native-executions", json={})).status_code == 401
        headers = {"Authorization": "Bearer fixture-token"}
        assert (await client.post("/api/v1/native-executions/one/stop", json={}, headers=headers)).status_code == 400
        app.state.native_manager.stop.assert_not_awaited()
        result = await client.post(
            "/api/v1/native-executions/one/stop", json={"generation": "generation"}, headers=headers,
        )
        assert result.status_code == 200
        app.state.native_manager.stop.assert_awaited_once_with("one", "generation")


def test_unauthenticated_terminal_does_not_touch_execution():
    app = FastAPI()
    app.state.auth_token = "fixture-token"
    app.state.native_manager = SimpleNamespace(endpoint=AsyncMock())
    app.include_router(native.router)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/native-executions/one/terminal?generation=gen"):
                pass
        assert exc.value.code == 1008
    app.state.native_manager.endpoint.assert_not_awaited()


def test_unrepresented_native_target_never_reaches_acp_send(monkeypatch):
    client = SimpleNamespace(
        daemon_supports=lambda _: True,
        native_resolve=lambda _: {"executionId": "one", "generation": "gen", "ready": False},
    )
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli, "_resolve_prompt", lambda *a, **k: "hello")
    monkeypatch.setattr(cli, "_resolve_target", lambda *a, **k: pytest.fail("ACP fallback"))
    args = SimpleNamespace(target="codespace:example-space", new=False)
    with pytest.raises(SystemExit, match="refusing ACP fallback"):
        cli._cmd_send(args)


@pytest.mark.asyncio
async def test_native_ownership_blocks_acp_before_session_allocation():
    manager = object.__new__(SessionManager)
    manager._draining = False
    manager._sessions = {}

    def reject(_):
        raise NativeError("native_incumbent", "native execution still owns the target")

    manager.native_guard = reject
    target = SimpleNamespace(codespace={"name": "example-space"}, spawn_command=None)
    with pytest.raises(NativeError, match="still owns"):
        await manager.start_session(target)
    assert manager._sessions == {}


def test_native_cli_generation_required_for_resume_and_stop():
    parser = cli.build_parser()
    for action in ("resume", "attach", "stop"):
        with pytest.raises(SystemExit):
            parser.parse_args(["native", action, "execution"])
    parsed = parser.parse_args([
        "native", "start", "--codespace", "example-space", "--owner", "owner",
        "--cwd", "/workspaces/example-web", "--request-id", "request",
        "--command-file", "command.txt", "--no-plugin-staging", "--require-relay", "--json",
    ])
    assert parsed.native_action == "start" and parsed.require_relay and parsed.no_plugin_staging
