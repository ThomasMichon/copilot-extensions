"""Codespace resolver: related-repo plugin staging + target_repo (Phase 4)."""

from __future__ import annotations

import os
from pathlib import Path

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
    # The payload is routed through a durable file, not an inline string.
    assert cmd[-2] == "--remote-cmd-file"
    assert Path(cmd[0]).name in {"agent-codespaces", "agent-codespaces.cmd"}
    assert Path(cmd[-1]).read_text(encoding="utf-8") == "cd /w && copilot --acp --stdio"


def test_build_spawn_command_with_stage_plugins():
    cmd = _build_spawn_command(
        "cs-1", "cd /w && copilot --acp --stdio",
        stage_plugins=["a@m", "b@m"],
    )
    # --stage-plugin args precede --remote-cmd-file, one per source.
    assert cmd.count("--stage-plugin") == 2
    i = cmd.index("--stage-plugin")
    assert cmd[i:i + 4] == ["--stage-plugin", "a@m", "--stage-plugin", "b@m"]
    assert cmd[-2] == "--remote-cmd-file"


def test_build_spawn_command_is_payload_local_not_global_binstub(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent_codespaces._invoke._payload_root",
        lambda: tmp_path / "marketplace" / "agent-codespaces",
    )
    payload_bin = tmp_path / "marketplace" / "agent-codespaces" / "bin"
    payload_bin.mkdir(parents=True)
    name = "agent-codespaces.cmd" if os.name == "nt" else "agent-codespaces"
    (payload_bin / name).write_text("", encoding="utf-8")

    cmd = _build_spawn_command("cs-1", "cd /w && copilot --acp --stdio")

    assert Path(cmd[0]) == payload_bin / name


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
