"""Codespace resolver: related-repo plugin staging + target_repo (Phase 4)."""

from __future__ import annotations

import pytest

from agent_codespaces.lifecycle import CodespaceInfo
from agent_codespaces.resolver import CodespaceResolver, _build_spawn_command


def _cs(name="cs-1", repo="example-org/example-web-codespaces"):
    return CodespaceInfo(
        name=name, display_name=name, repository=repo,
        branch="main", state="Available", machine="m",
    )


def test_build_spawn_command_no_plugins():
    cmd = _build_spawn_command("cs-1", "cd /w && copilot --acp --stdio")
    assert "--stage-plugin" not in cmd
    assert cmd[-2:] == ["--remote-cmd", "cd /w && copilot --acp --stdio"]


def test_build_spawn_command_with_stage_plugins():
    cmd = _build_spawn_command(
        "cs-1", "cd /w && copilot --acp --stdio",
        stage_plugins=["a@m", "b@m"],
    )
    # --stage-plugin args precede --remote-cmd, one per source.
    assert cmd.count("--stage-plugin") == 2
    i = cmd.index("--stage-plugin")
    assert cmd[i:i + 4] == ["--stage-plugin", "a@m", "--stage-plugin", "b@m"]
    assert cmd[-2] == "--remote-cmd"


@pytest.mark.asyncio
async def test_target_repo(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.resolver.list_codespaces", lambda: [_cs()],
    )
    r = CodespaceResolver()
    assert await r.target_repo("cs-1") == "example-org/example-web-codespaces"


@pytest.mark.asyncio
async def test_target_repo_unknown_is_none(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.resolver.list_codespaces", lambda: [_cs()],
    )
    r = CodespaceResolver()
    assert await r.target_repo("does-not-exist") is None
