"""Admission precedes allocation gates, CLI reservations and seed delivery."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from repository_policy import AgentHostingError

from agent_bridge import inventory_cli, local_launch_preparation, workspace_creation
from agent_bridge.client import BridgeClientError
from agent_bridge.models import SessionStatus
from agent_bridge.remote_operations import CarrierRequestRouter
from agent_bridge.routes import ui_tasks
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_repository_hosting_policy import deny, local_scope as _local_scope, repo as _repo
from test_workspace_creation import creation_rig as _creation_rig, rig as _rig

local_scope = _local_scope
repo = _repo
creation_rig = _creation_rig
rig = _rig


@pytest.mark.asyncio
async def test_keyed_denial_precedes_allocation_and_journal(creation_rig) -> None:
    rig = creation_rig
    rig.target.cwd = rig.row["path"]
    deny(Path(rig.row["path"]))
    with pytest.raises(AgentHostingError):
        await workspace_creation.start(rig.manager, rig.target, key="reserved", prompt="first seed")
    assert rig.calls == []
    assert rig.db.execute_read("SELECT * FROM workspace_embodiments") == []
    assert rig.db.execute_read("SELECT * FROM acquisition_launch_gates") == []
    assert rig.db.execute_read("SELECT * FROM sessions") == []
    assert rig.db.execute_read("SELECT * FROM turns") == []


@pytest.mark.asyncio
async def test_keyed_policy_added_after_allocation_retains_original_reservation(
    creation_rig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = creation_rig
    rig.target.cwd = rig.row["path"]

    async def resolve(*_a, **_k):
        deny(Path(rig.row["path"]))
        return ["copilot"], rig.row["path"], {}

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    with pytest.raises(AgentHostingError):
        await workspace_creation.start(rig.manager, rig.target, key="reserved", prompt="first seed")
    saved = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert saved["state"] == "allocated" and saved["session_id"]
    assert json.loads(saved["worktree_json"])["creation_nonce"] == "original-allocation"
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert rig.db.execute_read("SELECT * FROM acquisition_launch_gates") == []
    assert rig.db.execute_read("SELECT * FROM sessions") == []
    assert rig.db.execute_read("SELECT * FROM turns") == []
    with pytest.raises(AgentHostingError):
        await workspace_creation.start(rig.manager, rig.target, key="reserved", prompt="first seed")
    assert dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == saved
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1


@pytest.mark.asyncio
async def test_ui_embody_checks_fresh_actual_record_before_seed(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def owner(args, **_kwargs):
        calls.append(args)
        assert "embody" not in args and "--seed" not in args
        deny(repo)
        return {"worktree": {"id": "workspace", "path": str(repo)}}, ""

    monkeypatch.setattr(ui_tasks, "_aw", owner)
    result, error = await ui_tasks._embody("source", "workspace", "first seed")
    assert result is None and "expose_agent: false" in error
    assert len(calls) == 1 and "record" in calls[0]


@pytest.mark.asyncio
async def test_ui_task_refuses_before_source_allocation(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deny(repo)
    state = {"lock": None}
    monkeypatch.setattr(ui_tasks, "_guard", AsyncMock(return_value=(
        state, {"project": "source", "prompt": "first seed"}, None,
    )))
    monkeypatch.setattr(ui_tasks, "_repo_rows", AsyncMock(return_value=(
        [{"name": "source", "paths": {"linux": str(repo)}}], "",
    )))
    owner = AsyncMock()
    monkeypatch.setattr(ui_tasks, "_aw", owner)
    response = await ui_tasks.start_task(SimpleNamespace())
    assert response.status_code == 409
    assert "expose_agent" in response.body.decode()
    owner.assert_not_awaited()


def test_cli_denial_precedes_reservation_and_mux(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    client = MagicMock()

    def owner(args, **_kwargs):
        calls.append(args)
        if args[1] == "record":
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "worktree": {"id": "workspace", "path": "/workspace"},
            }))
        assert args[1:3] == ["agent-policy", "check"]
        return SimpleNamespace(returncode=1, stdout="", stderr="expose_agent: false")

    with pytest.raises(AgentHostingError, match="expose_agent"):
        inventory_cli._launch_cli_mode_session(client, "workspace", seed="first seed", run=owner)
    client.create_cli_mode_reservation.assert_not_called()
    assert len(calls) == 2
    assert all("embody" not in args and "--seed" not in args for args in calls)


@pytest.mark.asyncio
async def test_live_message_delivery_remains_usable_after_repository_denial(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session("live", "live", SpawnTarget(cwd=str(repo)), "source")
    session.status = SessionStatus.IDLE
    session.client = MagicMock(is_running=True)
    session_manager._sessions["live"] = session
    session_manager.db.create_session("live", "live", "source", str(repo), "local", "idle", time.time())
    delivered = AsyncMock()
    monkeypatch.setattr(session_manager, "_run_prompt", delivered)
    monkeypatch.setattr(session_manager, "_notify_host_reapable", AsyncMock())
    deny(repo)
    turn = await session_manager.submit_prompt("live", "message to existing agent")
    await session._prompt_task
    assert turn == 0
    delivered.assert_awaited_once_with(session, 0, "message to existing agent")
    assert session_manager.db.execute_read("SELECT prompt FROM turns")[0]["prompt"] == "message to existing agent"
    session.client.shutdown.assert_not_called()


@pytest.mark.asyncio
async def test_carrier_receiver_refusal_never_submits_seed_or_destroys_session() -> None:
    client = MagicMock()
    client.start_session.side_effect = BridgeClientError(409, {
        "code": "repository-agent-exposure-denied", "message": "expose_agent: false",
    })
    router = CarrierRequestRouter(lambda: client)
    with pytest.raises(BridgeClientError, match="expose_agent"):
        await router._create_session(client, {
            "agent": "explicit", "prompt": "first seed", "caller_id": "caller",
        })
    client.submit_prompt.assert_not_called()
    client.end_session.assert_not_called()
