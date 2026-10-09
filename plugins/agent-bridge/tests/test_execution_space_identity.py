"""Explicit space selection must survive transport resolution without host folding."""

from unittest.mock import patch

import pytest

from agent_bridge import agent_registry
from agent_bridge.agent_registry import AgentConfig, AgentResolver
from agent_bridge.models import ServiceConfig
from agent_bridge.topology import (
    MachineConfig, SshEnvironment, TopologyLoadError, parse_machines_yaml,
)


def _spaces():
    return {
        key: MachineConfig(
            key=key, display_name=key, hostname="workstation",
            physical_host="workstation", execution_platform=platform,
            ssh_ready=True,
            ssh_environments=[SshEnvironment(
                name=platform, alias=f"{key}-ssh", shell=shell,
            )],
        )
        for key, platform, shell in (
            ("workstation-windows", "windows", "pwsh"),
            ("workstation-wsl", "wsl", "bash"),
        )
    }


def test_explicit_service_selector_roundtrips_without_changing_preferences():
    config = ServiceConfig(local_execution_space="workstation-windows")
    restored = ServiceConfig.model_validate(config.model_dump())
    assert restored.local_execution_space == "workstation-windows"
    assert restored.preference_source == config.preference_source


@pytest.mark.parametrize(
    "selector", ["", " workstation-wsl", "host/project", r"host\project", "host#session", "host space"],
)
def test_invalid_service_selector_is_rejected(selector):
    with pytest.raises(ValueError):
        ServiceConfig(local_execution_space=selector)


def test_hostname_is_not_a_current_space_selector():
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="workstation"):
        with pytest.raises(ValueError, match="explicit local_execution_space"):
            agent_registry._detect_local_machine(_spaces())
        current, platform = agent_registry._detect_local_machine(
            _spaces(), "workstation-windows",
        )
    assert current.key == "workstation-windows"
    assert platform == "windows"


def test_scoped_topology_rejects_an_unscoped_local_selection():
    spaces = _spaces()
    spaces["legacy-host"] = MachineConfig(key="legacy-host", display_name="Legacy")
    with patch.object(agent_registry, "_detect_platform", return_value="windows"):
        with pytest.raises(ValueError, match="registered execution space"):
            agent_registry._detect_local_machine(spaces, "legacy-host")


def test_legacy_only_topology_keeps_explicit_local_selection():
    legacy = MachineConfig(key="legacy-host", display_name="Legacy")
    with patch.object(agent_registry, "_detect_platform", return_value="windows"):
        selected, platform = agent_registry._detect_local_machine(
            {"legacy-host": legacy}, "legacy-host",
        )
    assert selected is legacy
    assert platform == "windows"


def test_targeting_and_locality_use_distinct_registered_keys():
    spaces = _spaces()
    agents = {
        key: AgentConfig(name=key, host=key, project="project", ssh_environment=platform)
        for key, platform in (
            ("workstation-windows", "windows"), ("workstation-wsl", "wsl"),
        )
    }
    with patch.object(agent_registry, "_detect_platform", return_value="windows"):
        resolver = AgentResolver(
            agents, spaces, local_execution_space="workstation-windows",
        )
    resolver._own_plugin_args = lambda *args: []
    resolver._related_plugin_args = lambda *args: []
    local = resolver.resolve("workstation-windows")
    remote = resolver.resolve("workstation-wsl")
    assert local.type == "local"
    assert local.execution_space_key == "workstation-windows"
    assert remote.type == "ssh"
    assert remote.execution_space_key == "workstation-wsl"
    assert remote.host == "workstation-wsl-ssh"
    assert spaces["workstation-windows"].physical_host == spaces["workstation-wsl"].physical_host
    with pytest.raises(ValueError):
        resolver._resolve_machine("workstation")
    with pytest.raises(ValueError, match="registered key"):
        resolver._resolve_machine("workstation-wsl-ssh")


@pytest.mark.parametrize("key", ["", " space", "space key", "space/key", r"space\key", "space#session"])
def test_topology_rejects_noncanonical_space_keys(key):
    with pytest.raises(TopologyLoadError, match="canonical names"):
        parse_machines_yaml({"machines": {key: {"execution_platform": "windows"}}})


def test_topology_rejects_casefold_space_collisions():
    with pytest.raises(TopologyLoadError, match="unique without regard to case"):
        parse_machines_yaml({"machines": {
            "workstation": {"execution_platform": "windows"},
            "WORKSTATION": {"execution_platform": "wsl"},
        }})


def test_topology_normalizes_numeric_keys_like_the_shared_registry():
    machines = parse_machines_yaml({"machines": {123: {"execution_platform": "windows"}}})
    assert machines["123"].key == "123"


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_worktree_routes_preserve_selected_space_without_hostname_loopback(platform):
    from agent_bridge.routes.worktrees import _is_local_target

    with patch.object(agent_registry, "_detect_platform", return_value=platform):
        resolver = AgentResolver(
            {}, _spaces(), local_execution_space=f"workstation-{platform}",
        )
    other = "wsl" if platform == "windows" else "windows"
    with patch.object(
        agent_registry, "_detect_local_machine",
        side_effect=AssertionError("route must reuse the selected execution space"),
    ), patch("socket.gethostname", return_value="workstation"):
        assert _is_local_target(f"workstation-{platform}-ssh", resolver)
        assert _is_local_target(f"workstation-{platform}", resolver)
        assert not _is_local_target(f"workstation-{other}-ssh", resolver)
        assert not _is_local_target(f"workstation-{other}", resolver)
        assert not _is_local_target("workstation", resolver)
