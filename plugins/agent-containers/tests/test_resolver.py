"""Tests for the container: resolver spawn-command construction.

Critical security property: the forwarded GH_TOKEN must NEVER appear in argv
or in the SpawnTarget agent-bridge persists. The resolver returns the
``agent-containers exec --stdio <name>`` wrapper (no token); the wrapper fetches
the token at spawn time. ``build_spawn_command`` (used inside the wrapper)
references the token by name only (``-e GH_TOKEN``).
"""

from __future__ import annotations

import asyncio
import sys
import types

from agent_containers.resolver import (
    ContainerResolver,
    build_spawn_command,
    build_wrapper_command,
)


def test_build_spawn_command_forwards_token_by_name():
    cmd = build_spawn_command("myrepo-1", "vscode", "cd /w && copilot --acp", True)
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


def test_build_wrapper_command():
    cmd = build_wrapper_command("myrepo-1")
    assert cmd[-3:] == ["exec", "--stdio", "myrepo-1"]
    # no docker / token details leak into the wrapper command
    assert "docker" not in cmd
    assert "GH_TOKEN" not in cmd


def test_build_wrapper_command_uses_module_not_binstub():
    """Spawn via ``python -m agent_containers``, never the .cmd binstub, so
    agent-bridge does not route the spawn through cmd.exe and mangle args."""
    cmd = build_wrapper_command("myrepo-1")
    assert cmd[1:3] == ["-m", "agent_containers"]
    assert not cmd[0].lower().endswith((".cmd", ".bat"))


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


def test_resolve_returns_wrapper_without_token(monkeypatch):
    from agent_containers import resolver as r
    from agent_containers.config import ContainersConfig, FleetConfig

    _stub_agent_bridge(monkeypatch)

    monkeypatch.setattr(
        r, "get_container",
        lambda config, name: types.SimpleNamespace(fleet="myrepo"),
    )
    config = ContainersConfig()
    config.fleets["myrepo"] = FleetConfig()
    monkeypatch.setattr(r, "load_config", lambda: config)
    monkeypatch.setattr(r, "get_lease", lambda name: None)
    # If resolve() ever called host_gh_token, this would put a token in the
    # target -- make it explode so the test fails loudly if that regresses.
    monkeypatch.setattr(
        r, "host_gh_token",
        lambda: (_ for _ in ()).throw(AssertionError("resolve must not fetch token")),
    )

    target = asyncio.run(ContainerResolver().resolve("myrepo-1"))
    assert target.type == "command"
    # wrapper command, NOT docker directly
    assert target.spawn_command[-3:] == ["exec", "--stdio", "myrepo-1"]
    # no token persisted anywhere on the target
    assert not getattr(target, "env", {})
    assert "container" == ContainerResolver().prefix


def test_resolve_spec_exposes_workspace_folder_and_profile(monkeypatch):
    """namespace-resolve surfaces the venue's concrete cwd + trust posture so the
    bridge can set the ACP session cwd and gate host->venue projection."""
    from agent_containers import resolver as r
    from agent_containers.config import ContainersConfig, FleetConfig

    _stub_agent_bridge(monkeypatch)
    monkeypatch.setattr(
        r, "get_container",
        lambda config, name: types.SimpleNamespace(fleet="myrepo"),
    )
    config = ContainersConfig()
    config.fleets["myrepo"] = FleetConfig(
        workspace_folder="/workspaces/myrepo", security_profile="restricted",
    )
    monkeypatch.setattr(r, "load_config", lambda: config)
    monkeypatch.setattr(r, "get_lease", lambda name: None)

    spec = asyncio.run(ContainerResolver().resolve_spec("myrepo-1"))

    assert spec["workspace_folder"] == "/workspaces/myrepo"
    assert spec["security_profile"] == "restricted"
    # unchanged contract fields still present
    assert spec["type"] == "command"
    assert spec["spawn_command"][-3:] == ["exec", "--stdio", "myrepo-1"]


def test_resolve_missing_container_raises(monkeypatch):
    from agent_containers import resolver as r

    _stub_agent_bridge(monkeypatch)
    monkeypatch.setattr(r, "get_container", lambda config, name: None)
    monkeypatch.setattr(r, "list_containers", lambda config: [])

    import pytest

    with pytest.raises(KeyError):
        asyncio.run(ContainerResolver().resolve("missing"))


def test_ensure_ready_restricted_validates_before_start(monkeypatch):
    from agent_containers import resolver as r
    from agent_containers.config import ContainersConfig, FleetConfig

    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    info = types.SimpleNamespace(
        name="sandbox-1",
        fleet="sandbox",
        security_profile="restricted",
    )
    monkeypatch.setattr(r, "load_config", lambda: config)
    monkeypatch.setattr(r, "get_container", lambda cfg, name: info)
    monkeypatch.setattr(
        r,
        "restricted_policy_errors",
        lambda *a, **k: ["root filesystem is not read-only"],
    )
    monkeypatch.setattr(
        r,
        "start_container",
        lambda name: (_ for _ in ()).throw(
            AssertionError("unsafe container must not start")
        ),
    )

    import pytest

    with pytest.raises(RuntimeError, match="does not satisfy"):
        asyncio.run(ContainerResolver().ensure_ready("sandbox-1"))
