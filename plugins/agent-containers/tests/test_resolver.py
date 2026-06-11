"""Tests for the container: resolver spawn-command construction.

Critical security property: the forwarded GH_TOKEN must NEVER appear in argv
(it is passed by name via -e GH_TOKEN and supplied through the process env).
"""

from __future__ import annotations

import asyncio
import sys
import types

from agent_containers.resolver import ContainerResolver, build_spawn_command


def test_build_spawn_command_forwards_token_by_name():
    cmd = build_spawn_command("odsp-web-1", "vscode", "cd /w && copilot --acp", True)
    assert cmd[:3] == ["docker", "exec", "-i"]
    assert "-e" in cmd and "GH_TOKEN" in cmd
    # token value must NOT be embedded
    assert not any("GH_TOKEN=" in part for part in cmd)
    assert cmd[-3:] == ["bash", "-lc", "cd /w && copilot --acp"]
    assert "vscode" in cmd


def test_build_spawn_command_no_token():
    cmd = build_spawn_command("c1", "vscode", "copilot --acp", False)
    assert "GH_TOKEN" not in cmd
    assert "-e" not in cmd


def _stub_agent_bridge(monkeypatch):
    """Provide a minimal fake agent_bridge.transport.SpawnTarget."""
    mod = types.ModuleType("agent_bridge")
    transport = types.ModuleType("agent_bridge.transport")

    class SpawnTarget:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    transport.SpawnTarget = SpawnTarget
    mod.transport = transport
    monkeypatch.setitem(sys.modules, "agent_bridge", mod)
    monkeypatch.setitem(sys.modules, "agent_bridge.transport", transport)
    return SpawnTarget


def test_resolve_builds_command_target(monkeypatch):
    from agent_containers import resolver as r

    _stub_agent_bridge(monkeypatch)

    monkeypatch.setattr(
        r, "get_container",
        lambda config, name: types.SimpleNamespace(fleet=None),
    )
    monkeypatch.setattr(r, "get_lease", lambda name: None)
    monkeypatch.setattr(r, "host_gh_token", lambda: "SECRET-TOKEN")

    target = asyncio.run(ContainerResolver().resolve("odsp-web-1"))
    assert target.type == "command"
    assert target.env.get("GH_TOKEN") == "SECRET-TOKEN"
    # token only in env, never in the command args
    assert not any("SECRET-TOKEN" in part for part in target.spawn_command)
    assert "container" == ContainerResolver().prefix


def test_resolve_missing_container_raises(monkeypatch):
    from agent_containers import resolver as r

    _stub_agent_bridge(monkeypatch)
    monkeypatch.setattr(r, "get_container", lambda config, name: None)
    monkeypatch.setattr(r, "list_containers", lambda config: [])

    import pytest

    with pytest.raises(KeyError):
        asyncio.run(ContainerResolver().resolve("missing"))
