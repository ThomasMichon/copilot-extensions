"""The serving resume owner cannot substitute a newer conversation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from agent_bridge import worktree_exact_resume as exact
from agent_bridge.acquisition_store import AcquisitionStore
from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.models import SessionInfo
from test_worktree_native_resume import boundary, singleton_boundary  # noqa: F401
from test_worktree_native_resume import _singleton_session

pytestmark = pytest.mark.contract("agent_bridge.worktree_exact_resume")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "cli"])
async def test_exact_resume_uses_original_native_in_either_mode(
    boundary: SimpleNamespace, mode: Any,  # noqa: F811
) -> None:
    result = await exact.resume_exact_worktree(
        "wt-example", boundary.request, mode, exact.ExactResumeBody(native_session_id="native-original"),
    )
    assert isinstance(result, SessionInfo)
    assert result.acp_session_id == result.durable_session_id == "native-original"
    assert result.worktree_id == "wt-example"
    assert result.mode == mode
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "cli"])
@pytest.mark.parametrize("change", ["different-head", "handoff-pending"])
async def test_exact_resume_refuses_changed_head_before_metadata_or_launch(
    boundary: SimpleNamespace, mode: Any, change: str,  # noqa: F811
) -> None:
    AcquisitionStore(boundary.db)
    if change == "different-head":
        boundary.state["head"]["head_session"] = "native-newer"
    else:
        boundary.state["head"]["pending_handoffs"] = [{"ordinal": 1}]
    with pytest.raises(HTTPException) as refused:
        await exact.resume_exact_worktree(
            "wt-example", boundary.request, mode, exact.ExactResumeBody(native_session_id="native-original"),
        )
    assert refused.value.status_code == 409
    assert not boundary.manager.list_sessions()
    assert not boundary.state["launched"]
    assert not boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
async def test_live_cli_rechecks_selected_head_after_owner_liveness(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge.routes import worktrees as wt

    original = wt._run_for_agent
    boundary.state["launched"] = True

    async def raced(*args: Any) -> str:
        value = await original(*args)
        if args[-1][0] == "list-sessions":
            boundary.state["head"]["head_session"] = "native-newer"
        assert value is not None
        return value

    monkeypatch.setattr(wt, "_run_for_agent", raced)
    with pytest.raises(HTTPException) as refused:
        await exact.resume_exact_worktree(
            "wt-example", boundary.request, "cli", exact.ExactResumeBody(native_session_id="native-original"),
        )
    detail = refused.value.detail
    assert isinstance(detail, dict)
    assert detail["reason"] == "native_identity_changed"
    assert not any("--ensure-mux" in args for args in boundary.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_history", [False, True])
async def test_exact_singleton_never_initializes_or_loads_a_different_native(
    singleton_boundary: SimpleNamespace, has_history: bool,  # noqa: F811
) -> None:
    bound = singleton_boundary
    if has_history:
        _singleton_session(bound)
    with pytest.raises(HTTPException) as refused:
        await exact.resume_exact_worktree(
            "example@anchor", bound.request, "acp",
            exact.ExactResumeBody(native_session_id="selected-other"),
        )
    assert refused.value.status_code == 409
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()
    assert not bound.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
async def test_exact_singleton_preserves_the_known_native(
    singleton_boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    bound = singleton_boundary
    session = _singleton_session(bound)
    assert session.acp_session_id is not None

    async def load(session_id: str, **kwargs: Any) -> Any:
        assert session_id == session.session_id
        assert kwargs["allow_recreate"] is False
        from agent_bridge.session_status import SessionStatus

        session.status = SessionStatus.IDLE
        return session

    monkeypatch.setattr(bound.manager, "resume_session", AsyncMock(side_effect=load))
    result = await exact.resume_exact_worktree(
        "example@anchor", bound.request, "acp",
        exact.ExactResumeBody(native_session_id=session.acp_session_id),
    )
    assert isinstance(result, SessionInfo)
    assert result.durable_session_id == session.acp_session_id
    bound.manager.start_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"native_session_id": ""}, {"native_session_id": True},
    {"native_session_id": "native\n"}, {"native_session_id": "../other"},
    {"native_session_id": "native-original", "reclaim": True},
])
async def test_http_refuses_invalid_identity_before_lifecycle(
    body: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    resume = AsyncMock(side_effect=AssertionError("invalid input reached lifecycle"))
    monkeypatch.setattr(exact, "_resume_native_worktree", resume)
    app = FastAPI()
    app.include_router(exact.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post("/api/v1/worktrees/wt-example/resume/acp/exact", json=body)
    assert response.status_code == 422
    resume.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "cli"])
async def test_actual_http_route_preserves_selected_native(
    boundary: SimpleNamespace, mode: str,  # noqa: F811
) -> None:
    app = FastAPI()
    app.include_router(exact.router)
    app.state = boundary.request.app.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post(
            f"/api/v1/worktrees/wt-example/resume/{mode}/exact",
            json={"native_session_id": "native-original"},
        )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["acp_session_id"] == value["durable_session_id"] == "native-original"
    assert value["mode"] == mode and value["worktree_id"] == "wt-example"


@pytest.mark.parametrize("cli_mode", [False, True], ids=["acp", "cli"])
@pytest.mark.parametrize("changed_head", [False, True], ids=["original", "head-race"])
def test_root_cli_selector_reaches_the_existing_resume_owner_without_identity_substitution(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: Any,  # noqa: F811
    cli_mode: bool, changed_head: bool,
) -> None:
    from fastapi.testclient import TestClient

    from agent_bridge import __main__ as cli
    from agent_bridge.protocol import HTTP_PROTOCOL_VERSION

    app = FastAPI()
    app.include_router(exact.router)
    app.state = boundary.request.app.state
    client = BridgeClient("http://fixture", token="fixture")
    calls: list[tuple[str, str, Any]] = []
    with TestClient(app) as api:
        def request(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
            calls.append((method, path, body))
            if path == "/health":
                return {
                    "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                    "capabilities": {"bridge_inventory": 2, "worktree_exact_resume": 1},
                }
            if path == "/api/v1/bridges":
                assert kwargs["params"] == {"project": "example", "local_only": "true"}
                return {
                    "version": 1, "complete": True, "sources": {},
                    "bridges": [{
                        "handle": "wt-example", "worktree_id": "wt-example",
                        "session_id": "native-original", "status": "dormant",
                        "control": "unavailable",
                    }],
                }
            if changed_head:
                boundary.state["head"]["head_session"] = "native-newer"
            response = api.request(method, path, json=body)
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            return response.json()

        def get_client(**kwargs: Any) -> BridgeClient:
            assert kwargs == {"ensure": False, "propagate_errors": True}
            return client

        monkeypatch.setattr(cli, "_get_client", get_client)
        monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", None)
        monkeypatch.setattr(cli, "_PROJECT_ROUTED", False)
        monkeypatch.setattr(client, "_request", request)
        monkeypatch.setattr(cli, "_ensure_daemon", lambda: pytest.fail("must not ensure"))
        monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
        argv = ["--json", "--project", "example", "resume", "native-original"]
        argv += ["--cli"] if cli_mode else []
        if changed_head:
            with pytest.raises(SystemExit) as refused:
                cli.main(argv)
            assert refused.value.code == 1
        else:
            cli.main(argv)
    result = json.loads(capsys.readouterr().out)
    if changed_head:
        assert result["reason"] == "native_identity_changed"
        assert result["native_session_id"] == "native-original"
        assert not boundary.manager.list_sessions()
        assert not boundary.state["launched"]
    else:
        assert result["acp_session_id"] == result["durable_session_id"] == "native-original"
        assert result["mode"] == ("cli" if cli_mode else "acp")
    assert [call for call in calls if call[0] == "POST"] == [(
        "POST", f"/api/v1/worktrees/wt-example/resume/{'cli' if cli_mode else 'acp'}/exact",
        {"native_session_id": "native-original"},
    )]
    boundary.manager.start_session.assert_not_called()


@pytest.mark.parametrize("mode", ["acp", "cli"])
def test_client_negotiates_exact_capability_and_sends_one_pinned_request(
    mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object.__new__(BridgeClient)
    client._base = "http://fixture"
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/health":
            return {"protocol_version": 52, "capabilities": {"worktree_exact_resume": 1}}
        assert args == ({"native_session_id": "native-original"},)
        return {
            "session_id": "managed", "acp_session_id": "native-original",
            "durable_session_id": "native-original", "worktree_id": "wt-example", "mode": mode,
        }

    monkeypatch.setattr(client, "_request", request)
    result = client.resume_worktree_exact("wt-example", "native-original", mode=mode)
    assert result["durable_session_id"] == "native-original"
    assert [row[:2] for row in calls] == [
        ("GET", "/health"), ("POST", f"/api/v1/worktrees/wt-example/resume/{mode}/exact"),
    ]
    assert all(row[2]["allow_retry"] is False for row in calls)
    assert all(row[2]["_endpoint"] == "http://fixture" for row in calls)


@pytest.mark.parametrize("capability", [None, False, 0, "1"])
def test_prior_provider_refuses_before_post(
    capability: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object.__new__(BridgeClient)
    client._base = "http://fixture"
    request = Mock(return_value={
        "protocol_version": 52, "capabilities": {"worktree_exact_resume": capability},
    })
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeClientError) as refused:
        client.resume_worktree_exact("wt-example", "native-original")
    assert refused.value.status == 426
    assert request.call_count == 1
    assert request.call_args.args == ("GET", "/health")


@pytest.mark.parametrize("failure", [
    BridgeConnectionError("lost response"), {"durable_session_id": "native-newer"},
    BridgeClientError(404, "unsupported route"),
])
def test_cutover_or_lost_reply_never_retries_or_uses_legacy(
    failure: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object.__new__(BridgeClient)
    client._base = "http://fixture"
    request = Mock(side_effect=[
        {"protocol_version": 52, "capabilities": {"worktree_exact_resume": 1}}, failure,
    ])
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeClientError):
        client.resume_worktree_exact("wt-example", "native-original")
    assert request.call_count == 2
    assert request.call_args.args[1] == "/api/v1/worktrees/wt-example/resume/acp/exact"
