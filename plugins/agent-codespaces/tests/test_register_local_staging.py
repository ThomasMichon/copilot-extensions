"""_register_codespace_plugins routes local-marketplace specs to host staging.

A ``codespacePlugins`` source backed by a local (``.ai``/``directory``)
marketplace can't be ``copilot plugin install``-ed on an egress-restricted
CodeSpace, so it must be delivered by staging the host payload into a
``--plugin-dir``. Remote-marketplace specs keep the register + pre-install lane.
This proves the split + the combined ``--plugin-dir`` result.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_codespaces import __main__ as m
from agent_codespaces.codespace_plugins import CodespacePluginSpec


@dataclass
class _Result:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


class _FakeManager:
    """Records the commands exec'd; returns success."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec_command(self, name, command, timeout=None):
        self.commands.append(command)
        return _Result(exit_code=0)


class _Config:
    source_paths: list = []
    codespace_plugins: list = []


_DIR_MKT = {"source": {"source": "directory", "path": "./.ai"}}
_GH_MKT = {"source": {"source": "github", "repo": "owner/repo"}}


@pytest.mark.asyncio
async def test_local_specs_staged_remote_specs_registered(monkeypatch):
    marketplaces = {"dotfiles-plugins": _DIR_MKT, "copilot-extensions": _GH_MKT}
    monkeypatch.setattr(
        "agent_codespaces.config.repo_copilot_settings",
        lambda paths: {
            "extraKnownMarketplaces": marketplaces,
            "enabledPlugins": {},
        },
    )
    specs = [
        CodespacePluginSpec(source="figma@dotfiles-plugins"),          # local -> stage
        CodespacePluginSpec(source="agent-bridge@copilot-extensions"),  # remote -> register
    ]
    monkeypatch.setattr(
        "agent_codespaces.codespace_plugins.resolve_codespace_plugins",
        lambda repo, **kw: list(specs),
    )

    staged_sources: list[str] = []

    async def _fake_stage(manager, name, sources, **kwargs):
        staged_sources.extend(sources)
        assert kwargs["repo_roots"] == ()
        return [f"$HOME/.acp-staged-plugins/{s.split('@')[0]}" for s in sources]

    monkeypatch.setattr(m, "_stage_plugins", _fake_stage)

    mgr = _FakeManager()
    dirs = await m._register_codespace_plugins(
        mgr, "cs-1", "example-org/example-web", _Config()
    )

    # Only the local-marketplace source went through host staging.
    assert staged_sources == ["figma@dotfiles-plugins"]
    # The register command was issued for the remote spec only (contains the
    # remote source, never the local one).
    assert len(mgr.commands) == 1
    assert "agent-bridge@copilot-extensions" in mgr.commands[0]
    assert "figma@dotfiles-plugins" not in mgr.commands[0]
    # Combined --plugin-dir result carries BOTH lanes' dirs.
    assert "$HOME/.acp-staged-plugins/figma" in dirs
    assert any("copilot-extensions/agent-bridge" in d for d in dirs)


@pytest.mark.asyncio
async def test_all_local_no_register_command(monkeypatch):
    marketplaces = {"dotfiles-plugins": _DIR_MKT}
    monkeypatch.setattr(
        "agent_codespaces.config.repo_copilot_settings",
        lambda paths: {
            "extraKnownMarketplaces": marketplaces,
            "enabledPlugins": {},
        },
    )
    monkeypatch.setattr(
        "agent_codespaces.codespace_plugins.resolve_codespace_plugins",
        lambda repo, **kw: [CodespacePluginSpec(source="figma@dotfiles-plugins")],
    )

    async def _fake_stage(manager, name, sources, **kwargs):
        return ["$HOME/.acp-staged-plugins/figma"]

    monkeypatch.setattr(m, "_stage_plugins", _fake_stage)

    mgr = _FakeManager()
    dirs = await m._register_codespace_plugins(mgr, "cs-1", "o/r", _Config())

    # No remote specs -> build_register_command returns None -> no exec_command
    # for the register lane.
    assert mgr.commands == []
    assert dirs == ["$HOME/.acp-staged-plugins/figma"]
