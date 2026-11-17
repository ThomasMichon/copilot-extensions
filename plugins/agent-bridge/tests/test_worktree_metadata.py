"""Workspace-only targets hydrate one dormant control record, not a process."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
import pytest

from agent_bridge import worktree_native_resume
from agent_bridge.session_host.host_index import HostIndex
from test_worktree_native_resume import boundary as boundary  # noqa: F401

pytestmark = pytest.mark.contract("agent_bridge.worktree_metadata")


@pytest.fixture
def metadata_boundary(boundary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from agent_bridge.routes import worktrees

    monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR", raising=False)
    boundary.request.app.state.ready = True
    cache = worktrees.WorktreeDiscoveryCache()
    cache._cache = {"example": [boundary.entry]}
    boundary.request.app.state.worktree_discovery_cache = cache
    boundary.state["record"].update(
        creation_nonce="original-allocation", session_count=1, sessions_indexed=True,
        execution_leg=None, execution_leg_live=False, session_backend=None,
    )
    run = worktrees._run_for_agent

    async def owner(*args: Any) -> str:
        raw = await run(*args)
        assert raw is not None
        value = json.loads(raw)
        if args[-1][0] == "record":
            value["version"] = 1
        return json.dumps(value)

    monkeypatch.setattr(worktrees, "_run_for_agent", owner)
    index = tmp_path / "hosts.json"
    index.write_text('{"version":1,"hosts":{}}', encoding="utf-8")
    boundary.manager._host_index = HostIndex(index)
    return boundary


@pytest.mark.asyncio
async def test_concurrent_hydration_retains_one_original_native_without_a_load(metadata_boundary: Any) -> None:
    bound = metadata_boundary
    results = await asyncio.gather(*(
        worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
        for _ in range(2)
    ))
    values = [json.loads(bytes(result.body)) for result in results]
    assert values[0] == values[1]
    assert values[0]["acp_session_id"] == "native-original"
    assert values[0]["status"] == "stopped"
    assert len(bound.manager.list_sessions()) == 1
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()
    assert all(call[0] in {"record", "head-session"} for call in bound.calls)
    assert not bound.db.execute_read("SELECT * FROM turns")
    assert not bound.db.execute_read(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acquisition_launch_gates'",
    )


@pytest.mark.asyncio
async def test_lost_hydration_reply_reuses_the_committed_control(
    metadata_boundary: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.routes import sessions

    bound = metadata_boundary
    render = sessions._session_info

    def lost(*_args: Any) -> Any:
        raise RuntimeError("controlled metadata reply loss")

    monkeypatch.setattr(sessions, "_session_info", lost)
    with pytest.raises(RuntimeError, match="controlled metadata reply loss"):
        await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    original = bound.manager.list_sessions()[0].session_id
    monkeypatch.setattr(sessions, "_session_info", render)
    result = await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    assert json.loads(bytes(result.body))["session_id"] == original
    assert len(bound.db.execute_read("SELECT * FROM sessions")) == 1
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_another_frontend_cannot_duplicate_persisted_metadata(metadata_boundary: Any) -> None:
    from agent_bridge.session_manager import SessionManager

    bound = metadata_boundary
    other = SessionManager(bound.db)
    other._host_index = bound.manager._host_index
    await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    bound.request.app.state.session_manager = other
    with pytest.raises(HTTPException) as held:
        await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    assert held.value.status_code == 409
    assert len(bound.db.execute_read("SELECT * FROM sessions")) == 1


@pytest.mark.asyncio
async def test_owner_head_movement_during_hydration_leaves_no_control(
    metadata_boundary: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import worktree_charter

    bound = metadata_boundary

    def change_head(target: Any, *_args: Any) -> Any:
        bound.state["head"]["head_revision"] += 1
        return target

    monkeypatch.setattr(worktree_charter, "_apply_bound_charter", change_head)
    with pytest.raises(HTTPException):
        await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    assert not bound.manager.list_sessions()
    assert not bound.db.execute_read("SELECT * FROM sessions")
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "head", "path", "nonce", "active-leg", "unknown-leg", "missing-leg", "nonboolean-leg",
    "pending", "inventory", "missing-charter", "invalid-charter",
])
async def test_unqualified_head_never_creates_metadata(metadata_boundary: Any, fault: str) -> None:
    bound = metadata_boundary
    if fault == "head":
        bound.state["head"]["head_session"] = None
    elif fault == "path":
        bound.state["record"]["path"] += "-different"
    elif fault == "nonce":
        bound.state["record"]["creation_nonce"] = None
    elif fault in {"active-leg", "unknown-leg"}:
        bound.state["record"]["execution_leg"] = {"state": fault.removesuffix("-leg")}
        bound.state["record"]["execution_leg_live"] = True
    elif fault == "missing-leg":
        bound.state["record"].pop("execution_leg_live")
    elif fault == "nonboolean-leg":
        bound.state["record"]["execution_leg_live"] = 0
    elif fault == "pending":
        bound.state["head"]["pending_handoffs"] = ["pending"]
    elif fault == "missing-charter":
        bound.state["record"].pop("bound_agent")
    elif fault == "invalid-charter":
        bound.state["record"]["bound_agent"] = {"name": "unconfirmed"}
    else:
        bound.manager._host_index = None
    with pytest.raises(HTTPException):
        await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    assert not bound.manager.list_sessions()
    assert not bound.db.execute_read("SELECT * FROM sessions")
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_member_repository_name_does_not_replace_its_owner_project(metadata_boundary: Any) -> None:
    bound = metadata_boundary
    bound.state["record"]["repo"] = "member-repository"
    result = await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    value = json.loads(bytes(result.body))
    assert value["project"] == "example-project"
    assert value["acp_session_id"] == "native-original"
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "transport"])
async def test_existing_foreign_controller_cannot_be_returned_as_hydrated_metadata(
    metadata_boundary: Any, mismatch: str,
) -> None:
    from dataclasses import replace

    bound = metadata_boundary
    result = await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    original = json.loads(bytes(result.body))["session_id"]
    session = bound.manager.get_session(original)
    if mismatch == "owner":
        session.agent_name = "foreign-owner"
    else:
        session.target = replace(session.target, type="ssh", host="different-host")
    with pytest.raises(HTTPException):
        await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    assert len(bound.manager.list_sessions()) == 1
    assert bound.manager.get_session(original) is session
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_exact_record_charter_overrides_cached_profile_before_hydration(metadata_boundary: Any) -> None:
    from types import SimpleNamespace
    from agent_bridge.models import SessionInfo

    bound = metadata_boundary
    resolver = bound.request.app.state.resolver
    bound.entry.bound_agent = "stale-profile"
    profile = SimpleNamespace(
        managed=False, copilot_path="current-copilot", copilot_args=["--agent", "current-profile"],
        mcp_servers=[{"name": "current-tool", "command": "fixture-only"}],
        env={"PROFILE_SOURCE": "current"},
    )
    resolver.agents["current-profile"] = profile
    resolver.canonical_agent_name = lambda value: value
    bound.state["record"]["bound_agent"] = "current-profile"
    result = await worktree_native_resume.hydrate_worktree(bound.entry.id, bound.request)
    session = bound.manager.get_session(json.loads(bytes(result.body))["session_id"])
    assert session.target.copilot_path == profile.copilot_path
    assert session.target.copilot_args == profile.copilot_args
    assert session.target.env == profile.env and session.mcp_servers == profile.mcp_servers
    bound.manager.resume_session.assert_not_called()
    resumed = await worktree_native_resume.resume_native_worktree(
        bound.entry.id, bound.request, "acp", strict=True,
    )
    assert isinstance(resumed, SessionInfo)
    assert resumed.session_id == session.session_id
    assert session.target.copilot_args == profile.copilot_args
    assert session.mcp_servers == profile.mcp_servers


def test_actual_http_client_negotiates_each_target_and_metadata_operation(
    metadata_boundary: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
    from agent_bridge.routes import health

    bound = metadata_boundary
    app = FastAPI()
    app.include_router(worktree_native_resume.router)
    app.include_router(health.router)
    for key, value in vars(bound.request.app.state).items():
        setattr(app.state, key, value)
    monkeypatch.setattr(health, "observe_worktree_writer", lambda: None)
    monkeypatch.setattr(health, "slot_descriptor", lambda *_a, **_kw: {})
    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}))
    with TestClient(app) as http:
        client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
        calls: list[tuple[str, str]] = []
        state = {"support": False, "lost": False}

        def send(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
            calls.append((method, path))
            if method == "POST":
                assert kwargs["allow_retry"] is False
            response = http.request(method, path, json=body, params=kwargs.get("params"))
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            result = response.json()
            if path == "/health" and not state["support"]:
                for name in ("target_inventory", "worktree_metadata_hydration"):
                    result["capabilities"].pop(name)
            if method == "POST" and not state["lost"]:
                state["lost"] = True
                raise BridgeConnectionError("controlled metadata response loss")
            return result

        monkeypatch.setattr(client, "_request", send)
        operations: tuple[Callable[[], dict[str, Any]], ...] = (
            client.list_targets, lambda: client.hydrate_worktree(bound.entry.id),
        )
        for operation in operations:
            with pytest.raises(BridgeClientError) as unsupported:
                operation()
            assert unsupported.value.status == 426
        assert calls == [("GET", "/health"), ("GET", "/health")]
        assert not bound.manager.list_sessions()
        state["support"] = True
        targets = client.list_targets()
        assert len(targets["targets"]) == 1
        assert targets["targets"][0]["kind"] == "worktree"
        assert not bound.manager.list_sessions()
        with pytest.raises(BridgeConnectionError, match="controlled metadata response loss"):
            client.hydrate_worktree(bound.entry.id)
        assert len([item for item in calls if item[0] == "POST"]) == 1
        original = bound.manager.list_sessions()[0].session_id
        hydrated = client.hydrate_worktree(bound.entry.id)
        assert hydrated["session_id"] == original
        assert hydrated["status"] == "stopped"
        for invalid in (None, {**hydrated, "metadata_hydration": {
            "version": True, "native_execution": False,
        }}):
            def malformed(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
                result = send(method, path, body, **kwargs)
                return invalid if method == "POST" else result

            monkeypatch.setattr(client, "_request", malformed)
            with pytest.raises(BridgeClientError) as unconfirmed:
                client.hydrate_worktree(bound.entry.id)
            assert unconfirmed.value.status == 502
        assert len(bound.manager.list_sessions()) == 1
        bound.manager.start_session.assert_not_called()
        bound.manager.resume_session.assert_not_called()
