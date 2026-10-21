"""Controller authority must use the execution owner of explicit Worktrees tuples."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from agent_bridge.transport import SpawnTarget
from agent_bridge.venue_plugins import prepare_venue_plugins, resolve_controller_cwd


@pytest.fixture
def tuple_controller(monkeypatch, tmp_path):
    row = {
        "id": "controller", "repo": "control", "path": str(tmp_path), "status": "active",
        "machine_identity_version": 1, "machine": "physical-host", "environment": "wsl",
        "execution_machine": "custom-guest", "platform": "wsl",
    }
    command = AsyncMock(return_value={"worktrees": [row]})
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", command)
    return row, command, tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,owner", [
    ("windows", "custom-native"), ("wsl", "custom-guest"), ("linux", "custom-linux"),
])
async def test_tuple_controller_checks_execution_owner_and_preserves_metadata(
    tuple_controller, environment, owner,
):
    row, command, path = tuple_controller
    row.update(environment=environment, platform=environment, execution_machine=owner)
    before = deepcopy(row)
    caller = SpawnTarget(caller_worktree=str(path), caller_owner_ref=f"{owner}/control/controller")
    assert await resolve_controller_cwd(caller) == str(path.resolve())
    command.assert_awaited_once_with(
        ["list", "--json", "--cache-only", "--worktree-id", "controller"], "control",
    )
    assert row == before


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,owner", [
    ("windows", "custom-native"), ("wsl", "custom-guest"),
])
async def test_physical_host_cannot_substitute_for_execution_owner(
    tuple_controller, monkeypatch, environment, owner,
):
    row, _command, path = tuple_controller
    row.update(environment=environment, platform=environment, execution_machine=owner)
    caller = SpawnTarget(
        caller_worktree=str(path), caller_owner_ref="physical-host/control/controller",
    )
    provider = AsyncMock(return_value=(0, '{"plugins":[]}', ""))
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    with pytest.raises(RuntimeError, match="controller context could not be verified"):
        await prepare_venue_plugins(
            {"name": "example", "provider_command": ["provider"], "plugin_plan": True},
            transport, "/workspaces/project", caller=caller,
        )
    provider.assert_not_awaited()
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("machine_identity_version", True), ("machine_identity_version", 2),
    ("environment", "win"), ("environment", "future"),
    ("execution_machine", None), ("execution_machine", " guest"),
    ("platform", "windows"),
])
async def test_malformed_explicit_controller_refuses_before_provider_or_transport(
    tuple_controller, monkeypatch, field, value,
):
    row, _command, path = tuple_controller
    row[field] = value
    before = deepcopy(row)
    caller = SpawnTarget(
        caller_worktree=str(path), caller_owner_ref="physical-host/control/controller",
    )
    provider = AsyncMock(return_value=(0, '{"plugins":[]}', ""))
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    with pytest.raises(RuntimeError, match="controller context could not be verified"):
        await prepare_venue_plugins(
            {"name": "example", "provider_command": ["provider"], "plugin_plan": True},
            transport, "/workspaces/project", caller=caller,
        )
    provider.assert_not_awaited()
    transport.run.assert_not_awaited()
    assert row == before


@pytest.mark.asyncio
async def test_unversioned_controller_retains_legacy_owner_without_inference(tuple_controller):
    row, _command, path = tuple_controller
    del row["machine_identity_version"]
    row.update(environment="ignored", execution_machine=None)
    before = deepcopy(row)
    caller = SpawnTarget(
        caller_worktree=str(path), caller_owner_ref="physical-host/control/controller",
    )
    assert await resolve_controller_cwd(caller) == str(path.resolve())
    assert row == before
