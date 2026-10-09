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
@pytest.mark.parametrize("ssh_environment", ["linux", None])
def test_registry_assembly_suppresses_equivalent_local_project(monkeypatch, host, ssh_environment):
    from types import SimpleNamespace

    from agent_bridge import agent_registry
    from agent_bridge.agent_registry_common import AgentConfig

    machines = _machines()
    if ssh_environment is None:
        machines["box"].ssh_environments = [
            env for env in machines["box"].ssh_environments if env.name != "wsl"
        ]
    agents = parse_agent_registry({"explicit-worker": {
        "host": host, "project": "example-project", "ssh_environment": ssh_environment,
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
    assert resolver._is_local_loopback_agent(resolver.agents["explicit-worker"])


@pytest.mark.parametrize("platform,target_type,covering_agent", [
    ("linux", "ssh", None), ("wsl", "local", "explicit-worker"),
])
def test_registry_coverage_uses_actual_default_environment(monkeypatch, platform, target_type, covering_agent):
    from agent_bridge import agent_registry
    from agent_bridge.agent_registry_common import AgentConfig
    from agent_bridge.agent_registry_topology import _find_covering_agent

    machines = _machines()
    monkeypatch.setattr("socket.gethostname", lambda: "os-box")
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: platform)
    agents = parse_agent_registry({"explicit-worker": {
        "host": "FRIENDLY", "project": "example-project",
    }})
    local_agent = AgentConfig(name="example-project", project="example-project")
    assert _find_covering_agent(local_agent, agents, machines) == covering_agent
    resolver = AgentResolver(agents, machines)
    assert resolver.resolve("explicit-worker").type == target_type
    assert resolver._is_local_loopback_agent(agents["explicit-worker"]) == (target_type == "local")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["box", "BOX", "friendly", "FRIENDLY", "OS-BOX"])
async def test_remote_operations_require_environment_alias_for_every_machine_identity(monkeypatch, host):
    from agent_bridge.remote_errors import RemoteBridgeError
    from agent_bridge.remote_operations import RemoteOperationService

    monkeypatch.setattr("socket.gethostname", lambda: "elsewhere")
    service = RemoteOperationService(AgentResolver({}, _machines()))
    with pytest.raises(RemoteBridgeError) as exc:
        await service._lease(host)
    assert (exc.value.status, exc.value.code) == (400, "ambiguous_host")
    assert exc.value.details["ssh_aliases"] == ["box-win", "box-linux", "box-wsl"]


@pytest.mark.asyncio
@pytest.mark.parametrize("host,platform", [
    ("BOX-WIN", "windows"), ("BOX-LINUX", "linux"), ("BOX-WSL", "linux"),
])
async def test_remote_operations_accept_case_insensitive_exact_environment_alias(monkeypatch, host, platform):
    from agent_bridge.remote_operations import RemoteOperationService

    monkeypatch.setattr("socket.gethostname", lambda: "elsewhere")
    captured = {}
    lease = object()

    async def acquire(alias, remote_platform, **kwargs):
        captured.update(alias=alias, remote_platform=remote_platform)
        return lease

    monkeypatch.setattr("agent_bridge.carrier.acquire_remote_carrier", acquire)
    service = RemoteOperationService(AgentResolver({}, _machines()))
    assert await service._lease(host) is lease
    assert captured == {"alias": f"carrier:{host.lower()}", "remote_platform": platform}


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", [None, 123])
async def test_preserved_non_string_aliases_do_not_crash_resolver_or_remote_guard(monkeypatch, caplog, alias):
    from agent_bridge.remote_errors import RemoteBridgeError
    from agent_bridge.remote_operations import RemoteOperationService

    monkeypatch.setattr("socket.gethostname", lambda: "elsewhere")
    machines = parse_machines_yaml({"machines": {"box": {
        "ssh": {"ready": True, "environments": [
            {"name": "linux", "alias": alias},
            {"name": "windows", "alias": "box-win", "shell": "pwsh"},
        ]},
    }}})
    resolver = AgentResolver({}, machines)
    assert machines["box"].get_ssh_env("linux").alias == alias
    if alias is not None:
        assert "Ignoring non-string SSH alias" in caplog.text
    assert resolver.resolve_ssh_environment("BOX-WIN")[1].name == "windows"
    with pytest.raises(RemoteBridgeError) as exc:
        await RemoteOperationService(resolver)._lease("BOX")
    assert (exc.value.status, exc.value.code) == (400, "ambiguous_host")


def test_unnamed_environment_alias_does_not_crash_local_registry_coverage(monkeypatch):
    from agent_bridge import agent_registry
    from agent_bridge.agent_registry_common import AgentConfig
    from agent_bridge.agent_registry_topology import _find_covering_agent

    monkeypatch.setattr("socket.gethostname", lambda: "os-box")
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    machines = parse_machines_yaml({"machines": {"box": {
        "hostname": "OS-BOX",
        "ssh": {"ready": True, "environments": [
            {"name": None, "alias": "box-unnamed"}, {"name": "linux", "alias": "box-linux"},
        ]},
    }}})
    agents = parse_agent_registry({"explicit-worker": {
        "host": "BOX-UNNAMED", "project": "example-project",
    }})
    assert _find_covering_agent(
        AgentConfig(name="example-project", project="example-project"), agents, machines,
    ) is None
    resolver = AgentResolver(agents, machines)
    assert resolver.resolve("explicit-worker").type == "ssh"
    assert not resolver._is_local_loopback_agent(agents["explicit-worker"])
