"""Both plugin kinds use one mandatory preparation barrier."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import __main__ as cli


@pytest.mark.asyncio
async def test_selection_is_prepared_once_for_the_execution_checkout(monkeypatch):
    prepare = AsyncMock(return_value=["/prepared/local", "/prepared/remote"])
    monkeypatch.setattr("agent_codespaces.plugin_preparation.prepare_plugins", prepare)
    config = SimpleNamespace(resolved_workspace_folder_for=lambda repo: "/workspaces/project")
    manager = object()
    directories = await cli._register_codespace_plugins(
        manager, "synthetic", "example/project", config,
        sources=["related@synthetic"], worktree_id="selected-worktree",
    )
    assert directories == ["/prepared/local", "/prepared/remote"]
    prepare.assert_awaited_once_with(
        manager, "synthetic", "example/project", config, "/workspaces/project",
        ["related@synthetic"], worktree_id="selected-worktree", repo_roots=(),
        prelude="", relay_port=None,
    )


@pytest.mark.asyncio
async def test_required_preparation_failure_is_not_best_effort(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.prepare_plugins",
        AsyncMock(side_effect=RuntimeError("required payload unavailable")),
    )
    config = SimpleNamespace(resolved_workspace_folder_for=lambda repo: "/workspaces/project")
    with pytest.raises(RuntimeError, match="required payload"):
        await cli._register_codespace_plugins(object(), "synthetic", "example/project", config)
