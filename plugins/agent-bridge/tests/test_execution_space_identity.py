"""Explicit space selection must survive transport resolution without host folding."""

from unittest.mock import patch

import pytest

from agent_bridge import agent_registry
from agent_bridge.agent_registry import AgentConfig, AgentResolver
from agent_bridge.models import ServiceConfig
from agent_bridge.topology import (
    MachineConfig, SshEnvironment, TopologyLoadError, load_machines_yaml, parse_machines_yaml,
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


@pytest.mark.parametrize("platform,key", [("windows", "workstation"), ("wsl", "workstation-wsl")])
def test_legacy_windows_wsl_projection_selects_exact_logical_machine(platform, key):
    machines = parse_machines_yaml({"machines": {"workstation": {
        "hostname": "real-host", "auth": {"hooks": [{"name": "relay", "local_port": 1234}]},
        "ssh": {"ready": True, "environments": [
            {"name": "windows", "alias": "workstation-win", "shell": "pwsh"},
            {"name": "wsl", "alias": "workstation-ubuntu", "shell": "bash"},
        ]},
    }}}, normalize=True)
    assert set(machines) == {"workstation", "workstation-wsl"}
    assert machines[key].auth_hooks[0].name == "relay"
    assert machines[key].physical_host == "workstation"
    with patch.object(agent_registry, "_detect_platform", return_value=platform), \
            patch("socket.gethostname", return_value="real-host"):
        current, detected = agent_registry._detect_local_machine(machines)
        resolver = AgentResolver({}, machines)
    assert current.key == key
    assert detected == platform
    windows, win_env = resolver.resolve_ssh_environment("workstation")
    wsl, wsl_env = resolver.resolve_ssh_environment("workstation-wsl")
    assert (windows.key, win_env.alias) == ("workstation", "workstation-win")
    assert (wsl.key, wsl_env.alias) == ("workstation-wsl", "workstation-ubuntu")
    alias_machine, alias_env = resolver.resolve_ssh_environment("workstation-ubuntu")
    assert (alias_machine.key, alias_env.name) == ("workstation-wsl", "wsl")
    legacy_machine, legacy_env = resolver.resolve_ssh_environment("workstation", "wsl")
    assert (legacy_machine.key, legacy_env.alias) == ("workstation-wsl", "workstation-ubuntu")
    with pytest.raises(ValueError):
        resolver.resolve_ssh_environment("workstation-ubuntu", "windows")


@pytest.mark.parametrize("platform", ["windows", "wsl"])
@pytest.mark.parametrize("missing", [("wsl",), ("windows",), ("windows", "wsl")])
def test_projected_missing_ssh_aliases_default_to_logical_keys_without_rewriting(
    tmp_path, platform, missing,
):
    import yaml

    profiles = [{"name": name, **({} if name in missing else {"alias": f"desk-{name}-ssh"})}
                for name in ("windows", "wsl")]
    path = tmp_path / "machines.yaml"
    path.write_text(yaml.safe_dump({"machines": {"desk": {
        "ssh": {"environments": profiles},
    }}}), encoding="utf-8")
    before = path.read_bytes()
    machines = load_machines_yaml(path, strict=True)
    assert set(machines) == {"desk", "desk-wsl"}
    with patch.object(agent_registry, "_detect_platform", return_value=platform), \
            patch("socket.gethostname", return_value="desk"):
        current, detected = agent_registry._detect_local_machine(machines)
        resolver = AgentResolver({}, machines)
    assert current.key == ("desk-wsl" if platform == "wsl" else "desk")
    assert detected == platform
    for name, key in (("windows", "desk"), ("wsl", "desk-wsl")):
        entry, profile = resolver.resolve_ssh_environment(key)
        assert entry.key == key
        assert profile.alias == (key if name in missing else f"desk-{name}-ssh")
    assert path.read_bytes() == before


def test_projected_explicit_empty_alias_is_not_replaced_by_a_default():
    machines = parse_machines_yaml({"machines": {"desk": {
        "ssh": {"environments": [{"name": "windows"}, {"name": "wsl", "alias": ""}]},
    }}}, normalize=True)
    assert machines["desk"].ssh_environments[0].alias == "desk"
    assert machines["desk-wsl"].ssh_environments[0].alias == ""


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("environments,keys", [
    (None, {"desk"}),
    (["windows"], {"desk"}),
    ([None, 7, [], {"name": "wsl"}], {"desk", "desk-wsl"}),
])
def test_loader_keeps_tolerated_environment_shapes(tmp_path, strict, environments, keys):
    import yaml

    path = tmp_path / "machines.yaml"
    path.write_text(yaml.safe_dump({"machines": {"desk": {
        "ssh": {"environments": environments},
    }}}), encoding="utf-8")
    before = path.read_bytes()
    machines = load_machines_yaml(path, strict=strict)
    assert set(machines) == keys
    if "desk-wsl" in machines:
        assert machines["desk-wsl"].ssh_environments[0].alias == "desk-wsl"
    else:
        assert machines["desk"].ssh_environments == []
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_remote_carrier_acquisition_uses_each_logical_machine_profile(monkeypatch):
    from agent_bridge import carrier
    from agent_bridge.remote_operations import RemoteOperationService
    from ssh_manager import SSHProfileSource

    machines = parse_machines_yaml({"machines": {"desk": {
        "ssh": {"environments": [
            {"name": "windows", "alias": "desk-win", "shell": "pwsh"},
            {"name": "wsl", "alias": "desk-ubuntu", "shell": "bash"},
        ]},
    }}}, normalize=True)
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="desk"):
        resolver = AgentResolver({}, machines)
    acquired = []
    monkeypatch.setattr(SSHProfileSource, "_resolve_effective_config", lambda self: ())

    async def acquire(key, platform, *, config_source, manager):
        acquired.append((key, platform, config_source.get_ssh_config().host_alias))
        return object()

    monkeypatch.setattr(carrier, "acquire_remote_carrier", acquire)
    service = RemoteOperationService(resolver)
    await service._lease("desk")
    await service._lease("desk-wsl")
    assert acquired == [
        ("carrier:desk-win", "windows", "desk-win"),
        ("carrier:desk-ubuntu", "linux", "desk-ubuntu"),
    ]


@pytest.mark.parametrize("hostname,local_key", [("local-host", "laptop"), ("unregistered-host", None)])
def test_remote_projection_preserves_native_legacy_locality_and_unregistered_read_clients(
    hostname, local_key,
):
    machines = parse_machines_yaml({"machines": {
        "laptop": {"hostname": "local-host", "ssh": {"environments": [
            {"name": "windows", "alias": "laptop-ssh", "shell": "pwsh"},
        ]}},
        "desk": {"ssh": {"environments": [
            {"name": "windows", "alias": "desk-win", "shell": "pwsh"},
            {"name": "wsl", "alias": "desk-ubuntu", "shell": "bash"},
        ]}},
    }}, normalize=True)
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value=hostname):
        resolver = AgentResolver({}, machines)
        assert (resolver._local_machine.key if resolver._local_machine else None) == local_key
        assert not resolver.is_local_transport("desk-ubuntu", execution_space_key="desk-wsl")
        if local_key:
            assert resolver.is_local_transport("laptop-ssh", execution_space_key="laptop")


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


def test_hostname_and_platform_select_the_matching_space():
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="workstation"):
        current, platform = agent_registry._detect_local_machine(_spaces())
        assert current.key == "workstation-windows"
        assert platform == "windows"
        current, platform = agent_registry._detect_local_machine(
            _spaces(), "workstation-windows",
        )
    assert current.key == "workstation-windows"
    assert platform == "windows"


def test_unmatched_hostname_does_not_invent_local_ownership():
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="unregistered-host"):
        current, platform = agent_registry._detect_local_machine(_spaces())
    assert current is None
    assert platform == "windows"


@pytest.mark.parametrize("platform,foreign", [
    ("windows", "wsl"), ("windows", "linux"), ("wsl", "windows"), ("linux", "windows"),
])
def test_other_platform_hostname_remains_remote_only(platform, foreign):
    machines = {"foreign": MachineConfig(
        key="foreign", display_name="Foreign", hostname="workstation",
        physical_host="workstation", execution_platform=foreign,
    )}
    with patch.object(agent_registry, "_detect_platform", return_value=platform), \
            patch("socket.gethostname", return_value="workstation"):
        current, detected = agent_registry._detect_local_machine(machines)
        assert current is None
        assert detected == platform
        with pytest.raises(ValueError, match="execution platform"):
            agent_registry._detect_local_machine(machines, "foreign")


@pytest.mark.parametrize("alias_kind", ["ssh", "machine", "compatibility", "key", "physical"])
@pytest.mark.parametrize("legacy", [False, True])
def test_target_names_do_not_override_an_explicit_remote_hostname(alias_kind, legacy):
    machine = MachineConfig(
        key="remote", display_name="Remote", hostname="remote-host",
        physical_host="remote-host", execution_platform="linux",
        ssh_environments=[SshEnvironment("linux", "remote-ssh", shell="bash")],
    )
    if alias_kind == "ssh":
        machine.ssh_environments[0].alias = "workstation"
    elif alias_kind == "machine":
        machine.alias = "workstation"
    elif alias_kind == "compatibility":
        machine.compatibility_aliases = ("workstation",)
    elif alias_kind == "key":
        machine.key = "workstation"
    else:
        machine.physical_host = "workstation"
    machine.legacy_key = machine.key if legacy else ""
    with patch.object(agent_registry, "_detect_platform", return_value="linux"), \
            patch("socket.gethostname", return_value="workstation"):
        current, _ = agent_registry._detect_local_machine({machine.key: machine})
        resolver = AgentResolver({}, {machine.key: machine})
    assert current is None
    assert not resolver.is_local_transport("workstation", execution_space_key=machine.key)
    assert not resolver.is_local_transport("workstation")


def test_other_platform_sibling_cannot_override_a_remote_hostname():
    machines = {
        key: MachineConfig(
            key=key, display_name=key, hostname=hostname,
            physical_host="shared-host", execution_platform=platform,
        )
        for key, hostname, platform in (
            ("native", "workstation", "windows"), ("remote", "another-host", "linux"),
        )
    }
    with patch.object(agent_registry, "_detect_platform", return_value="linux"), \
            patch("socket.gethostname", return_value="workstation"):
        current, _ = agent_registry._detect_local_machine(machines)
    assert current is None


def test_implicit_physical_hostname_selects_the_current_platform():
    machines = _spaces()
    for machine in machines.values():
        machine.hostname = ""
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="workstation"):
        current, _ = agent_registry._detect_local_machine(machines)
    assert current.key == "workstation-windows"


@pytest.mark.parametrize("exact_key", ["workstation", "WORKSTATION"])
def test_exact_key_does_not_hide_same_platform_hostname_collision(exact_key):
    machines = {
        key: MachineConfig(
            key=key, display_name=key, hostname="workstation",
            physical_host="workstation", execution_platform="windows",
        )
        for key in (exact_key, "other-windows")
    }
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="workstation"):
        with pytest.raises(ValueError, match="[Aa]mbiguous"):
            agent_registry._detect_local_machine(machines)
        selected, _ = agent_registry._detect_local_machine(machines, exact_key)
        assert selected.key == exact_key


def test_same_platform_hostname_ambiguity_requires_explicit_selection():
    spaces = _spaces()
    spaces["another-windows"] = MachineConfig(
        key="another-windows", display_name="Other", hostname="workstation",
        physical_host="workstation", execution_platform="windows",
    )
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="workstation"):
        with pytest.raises(ValueError, match="[Aa]mbiguous"):
            agent_registry._detect_local_machine(spaces)
        current, _ = agent_registry._detect_local_machine(spaces, "workstation-windows")
    assert current.key == "workstation-windows"


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


@pytest.mark.parametrize("collision", ["none", "ssh-alias", "key", "shared-alias"])
def test_selected_unscoped_local_host_keeps_local_routing_in_mixed_inventory(collision):
    from agent_bridge.resolver_runtime import capture_resolver, restore_resolver
    from agent_bridge.routes.worktrees import _is_local_target

    local = MachineConfig(
        key="legacy", display_name="Local", hostname="workstation",
        ssh_environments=[SshEnvironment("windows", "local-ssh", shell="pwsh")],
    )
    remote = MachineConfig(
        key="remote", display_name="Remote", hostname="another-host",
        physical_host="another-host", execution_platform="linux", ssh_ready=True,
        ssh_environments=[SshEnvironment("linux", "remote-ssh", shell="bash")],
    )
    if collision == "ssh-alias":
        remote.ssh_environments[0].alias = "workstation"
    elif collision == "key":
        remote.key = "workstation"
    elif collision == "shared-alias":
        remote.ssh_environments[0].alias = "local-ssh"
    agents = {"local": AgentConfig(
        name="local", host=local.key, project="control", ssh_environment="windows",
    )}
    with patch.object(agent_registry, "_detect_platform", return_value="windows"), \
            patch("socket.gethostname", return_value="workstation"):
        original = AgentResolver(agents, {local.key: local, remote.key: remote})
        restored = restore_resolver(capture_resolver(original))
        for resolver in (original, restored):
            resolver._own_plugin_args = lambda *args: []
            resolver._related_plugin_args = lambda *args: []
            target = resolver.resolve("local")
            assert target.type == "local"
            assert target.execution_space_key is None
            host = target.host or agents["local"].host
            assert resolver.is_local_transport(host)
            assert _is_local_target(host, resolver)
            assert not resolver.is_local_transport(
                remote.ssh_environments[0].alias, execution_space_key=remote.key,
            )
            if collision != "none":
                assert not resolver.is_local_transport(remote.ssh_environments[0].alias)


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
