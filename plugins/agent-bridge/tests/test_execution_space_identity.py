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
        key = f"workstation-{platform}"
        assert _is_local_target(f"workstation-{platform}-ssh", resolver, key)
        assert _is_local_target(f"workstation-{platform}", resolver, key)
        assert not _is_local_target(f"workstation-{other}-ssh", resolver, f"workstation-{other}")
        assert not _is_local_target(f"workstation-{other}", resolver, f"workstation-{other}")
        assert not _is_local_target("workstation", resolver)


def test_duplicate_ssh_alias_cannot_make_a_remote_key_local():
    from agent_bridge.routes.worktrees import _is_local_target

    spaces = _spaces()
    for machine in spaces.values():
        machine.ssh_environments[0].alias = "shared-transport"
    agent = AgentConfig(
        name="remote", host="workstation-wsl", project="project", ssh_environment="wsl",
    )
    with patch.object(agent_registry, "_detect_platform", return_value="windows"):
        resolver = AgentResolver(
            {"remote": agent}, spaces, local_execution_space="workstation-windows",
        )
    resolver._own_plugin_args = lambda *args: []
    resolver._related_plugin_args = lambda *args: []
    target = resolver.resolve("remote")
    assert target.host == "shared-transport"
    assert target.execution_space_key == "workstation-wsl"
    assert not _is_local_target(target.host, resolver, target.execution_space_key)
    assert not _is_local_target(target.host, resolver)
    assert _is_local_target(target.host, resolver, "workstation-windows")


@pytest.mark.parametrize("other_key,other_platform", [("SPACE", "wsl"), ("space", "wsl")])
def test_merged_profile_identity_collisions_reject_before_agent_derivation(
    other_key, other_platform, tmp_path, monkeypatch,
):
    from types import SimpleNamespace
    from agent_bridge import topology

    files = []
    for name, key, platform in (
        ("first", "space", "windows"), ("second", other_key, other_platform),
    ):
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            f"machines:\n  {key}:\n    execution_platform: {platform}\n",
            encoding="utf-8",
        )
        files.append(SimpleNamespace(machines_yaml=str(path), agents_config=None))
    config = SimpleNamespace(
        topologies=dict(zip(["first", "second"], files)), local_execution_space="space",
    )
    monkeypatch.setattr(
        agent_registry, "derive_topology_agents",
        lambda *args, **kwargs: pytest.fail("ambiguous merged identity derived an agent"),
    )
    with pytest.raises(topology.TopologyLoadError, match="across topology profiles|conflicting platforms"):
        agent_registry.build_resolver(config)
