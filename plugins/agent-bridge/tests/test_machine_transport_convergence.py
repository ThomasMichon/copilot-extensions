"""Bridge-specific compatibility around shared named-machine resolution."""

from __future__ import annotations

import pytest

from agent_bridge.agent_registry import AgentResolver, parse_agent_registry
from agent_bridge.topology import parse_machines_yaml
from agent_bridge.transport import _build_remote_cmd


def _machines():
    return parse_machines_yaml({"machines": {"box": {
        "alias": "friendly", "hostname": "OS-BOX", "display_name": "Build Box",
        "description": " development ", "capabilities": [" build ", "build"],
        "field_terminal": True,
        "ssh": {"ready": True, "ip": "192.0.2.10", "environments": [
            {"name": "windows", "alias": "box-win", "shell": "pwsh"},
            {"name": "linux", "alias": "box-linux", "port": 2200, "user": "dev"},
            {"name": "wsl", "alias": "box-wsl", "shell": "zsh"},
        ]},
        "auth": {"hooks": [{"name": "relay", "local_port": 8000,
                           "remote_port": 8001, "env": {"PORT": 8001}}]},
    }}})


def test_shared_identities_keep_bridge_defaults_and_metadata(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "elsewhere")
    machines = _machines()
    resolver = AgentResolver({}, machines)
    machine = machines["box"]
    for name in ("box", "BOX", "FRIENDLY", "os-box", "BUILD BOX"):
        found, env = resolver.resolve_ssh_environment(name)
        assert found is machine
        assert env.name == "wsl"
    assert machine.description == "development"
    assert machine.capabilities == ["build"]
    assert machine.field_terminal and machine.ssh_ip == "192.0.2.10"
    assert machine.auth_hooks[0].env == {"PORT": "8001"}
    assert machine.get_ssh_env("linux").port == 2200
    assert machine.get_ssh_env("linux").user == "dev"
    assert machine.get_ssh_env("linux").shell == "bash"
    legacy = parse_machines_yaml({"machines": {"old": {
        "ssh": {"environments": [{}, {"name": "linux", "alias": "", "shell": ""}]},
    }}})["old"]
    assert [(env.name, env.alias, env.shell) for env in legacy.ssh_environments] == [
        ("", "old", "bash"), ("linux", "", ""),
    ]
    assert parse_machines_yaml({}) == {}


def test_ssh_alias_binding_conflicts_and_acp_command_are_preserved(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "elsewhere")
    machines = _machines()
    agents = parse_agent_registry({"worker": {
        "host": "BOX-LINUX", "cwd": "/home/dev/work", "env": {"FLAG": "yes"},
    }})
    resolver = AgentResolver(agents, machines)
    target = resolver.resolve("worker")
    assert (target.type, target.host, target.ssh_shell, target.user) == (
        "ssh", "box-linux", "bash", "dev",
    )
    assert target.auth_hooks == [{
        "name": "relay", "local_port": 8000, "remote_port": 8001,
        "env": {"PORT": "8001"},
    }]
    canonical = AgentResolver(parse_agent_registry({"worker": {
        "host": "box", "ssh_environment": "linux",
        "cwd": "/home/dev/work", "env": {"FLAG": "yes"},
    }}), machines).resolve("worker")
    assert _build_remote_cmd(target, "session-test") == _build_remote_cmd(canonical, "session-test")
    with pytest.raises(ValueError, match="conflict"):
        resolver.resolve_ssh_environment("BOX-LINUX", "wsl")
    assert resolver.resolve_ssh_environment("BOX-LINUX", "linux")[1].name == "linux"
    with pytest.raises(ValueError, match="POSIX"):
        AgentResolver(parse_agent_registry({"worker": {"host": "BOX-WIN"}}), machines).resolve("worker")


def test_local_self_uses_shared_hostname_identity_but_not_cross_environment(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "os-box")
    monkeypatch.setattr("agent_bridge.agent_registry._detect_platform", lambda: "linux")
    machines = _machines()
    machines["box"].ssh_ready = False
    for host in ("BOX", "FRIENDLY", "OS-BOX", "Build Box", "BOX-LINUX"):
        agents = parse_agent_registry({"worker": {
            "host": host, "ssh_environment": "linux",
        }})
        resolver = AgentResolver(agents, machines)
        assert resolver.resolve("worker").type == "local"
        assert resolver._is_local_loopback_agent(agents["worker"])
        assert resolver.machine_key_for_agent(agents["worker"]) == "box"
    machines["box"].ssh_ready = True
    resolver = AgentResolver(parse_agent_registry({"worker": {"host": "BOX-WSL"}}), machines)
    assert resolver.resolve("worker").type == "ssh"
    assert not resolver._is_local_loopback_agent(resolver.agents["worker"])


def test_ambiguous_identities_and_environment_aliases_fail_closed(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "elsewhere")
    machines = _machines()
    other = parse_machines_yaml({"machines": {"other": {
        "alias": "FRIENDLY",
        "ssh": {"environments": [{"name": "linux", "alias": "BOX-LINUX"}]},
    }}})
    resolver = AgentResolver({}, {**machines, **other})
    for name in ("friendly", "box-linux"):
        with pytest.raises(ValueError, match="ambiguous"):
            resolver.resolve_ssh_environment(name)
    assert resolver.resolve_ssh_environment("box")[0] is machines["box"]
    with pytest.raises(ValueError, match="not found"):
        resolver.resolve_ssh_environment("missing")


def test_duplicate_local_hostnames_do_not_select_a_machine(monkeypatch, caplog):
    from agent_bridge import agent_registry

    monkeypatch.setattr("socket.gethostname", lambda: "os-box")
    machines = _machines()
    machines.update(parse_machines_yaml({"machines": {"other": {"hostname": "OS-BOX"}}}))
    machine, _platform = agent_registry._detect_local_machine(machines)
    assert machine is None
    assert "Cannot determine local machine" in caplog.text
    assert "ambiguous" in caplog.text


@pytest.mark.parametrize("host", ["FRIENDLY", "OS-BOX", "Build Box", "BOX-LINUX"])
def test_registry_assembly_suppresses_equivalent_local_project(monkeypatch, host):
    from types import SimpleNamespace

    from agent_bridge import agent_registry
    from agent_bridge.agent_registry_common import AgentConfig

    machines = _machines()
    agents = parse_agent_registry({"explicit-worker": {
        "host": host, "project": "example-project", "ssh_environment": "linux",
    }})
    monkeypatch.setattr("socket.gethostname", lambda: "os-box")
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    monkeypatch.setattr("agent_bridge.topology.load_machines_yaml", lambda *a, **k: machines)
    monkeypatch.setattr("agent_bridge.topology.load_control_plane_project", lambda *a: None)
    monkeypatch.setattr(agent_registry, "load_agent_registry", lambda *a, **k: agents)
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: {})
    monkeypatch.setattr(agent_registry, "infer_control_plane_project", lambda *a: None)
    monkeypatch.setattr(agent_registry, "_load_related_entries", lambda *a: [])
    monkeypatch.setattr(agent_registry, "derive_topology_agents", lambda *a, **k: {})
    monkeypatch.setattr(agent_registry, "_effective_spawn_defaults", lambda *a: ([], {}))
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {
        "example-project": AgentConfig(
            name="example-project", project="example-project", auto_discovered=True,
        ),
    })
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda *a: None)
    monkeypatch.setattr("agent_bridge.config.load_repo_bridge_config", lambda *a: {})
    profile = SimpleNamespace(machines_yaml="machines.yaml", agents_config="agents.json")
    resolver = agent_registry.build_resolver(SimpleNamespace(topologies={"test": profile}))
    assert resolver is not None
    assert list(resolver.agents) == ["explicit-worker"]
    assert resolver.resolve("explicit-worker").type == "local"
