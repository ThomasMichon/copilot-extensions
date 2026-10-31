"""Topology discovery does not require source-project execution authority."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import agent_registry, config, execution_identity, session_lifecycle_cli
from agent_bridge.agent_registry import AgentConfig, AgentResolver
from agent_bridge.models import ServiceConfig, TopologyProfile
from agent_bridge.topology import parse_machines_yaml


def _spaces():
    return parse_machines_yaml({"machines": {
        key: {
            "execution_platform": platform, "physical_host": hostname,
            "hostname": hostname,
            "ssh": {"ready": True, "environments": [
                {"name": platform, "alias": key + "-ssh",
                 "shell": "pwsh" if platform == "windows" else "bash"},
            ]},
        }
        for key, platform, hostname in (
            ("native-a", "windows", "local-host"),
            ("native-b", "windows", "other-host"),
            ("guest", "wsl", "local-host"),
            ("linux", "linux", "local-host"),
        )
    }}, normalize=True)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setattr(config, "_canonical_repo_root", lambda root: root)
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "windows")
    monkeypatch.setattr("socket.gethostname", lambda: "local-host")
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_launch_prefix",
        lambda: pytest.fail("topology discovery must not query a source-project binding"),
    )
    return root


def test_source_capture_is_not_serialized_or_required_as_a_key(source, monkeypatch):
    monkeypatch.chdir(source)
    cfg = ServiceConfig()
    assert cfg._source_worktree == source
    assert "_source_worktree" not in cfg.model_dump()
    assert execution_identity.resolve_execution_space(cfg, _spaces()) is None
    assert AgentResolver({}, _spaces())._local_machine.key == "native-a"
    assert cfg.local_execution_space is None


@pytest.mark.parametrize("platform,key", [
    ("windows", "native-a"), ("wsl", "guest"), ("linux", "linux"),
])
def test_native_and_guest_detection_needs_no_project_or_sibling(source, monkeypatch, platform, key):
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: platform)
    resolver = AgentResolver({}, _spaces())
    assert resolver._local_machine.key == key
    for other in _spaces():
        assert resolver.is_local_transport(other, execution_space_key=other) == (other == key)


@pytest.mark.parametrize("selector", ["native-a", "native-b"])
def test_explicit_selector_remains_available_without_source_binding(source, selector):
    cfg = ServiceConfig(local_execution_space=selector)
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == selector
    resolver = AgentResolver({}, _spaces(), local_execution_space=selector)
    assert resolver._local_machine.key == selector


@pytest.mark.parametrize("key", ["shared-host", "native-a-ssh", "unknown", "guest"])
def test_explicit_selection_must_be_canonical_and_match_platform(source, key):
    cfg = ServiceConfig(local_execution_space=key)
    with pytest.raises(execution_identity.ExecutionIdentityError, match="Bridge selector is invalid"):
        execution_identity.resolve_execution_space(cfg, _spaces())


def test_local_only_config_needs_no_machine_inventory_or_peer(tmp_path, monkeypatch):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"local": {"cwd": str(tmp_path)}}), encoding="utf-8")
    cfg = ServiceConfig(
        local_execution_space="standalone-legacy-key",
        topologies={"local": TopologyProfile(agents_config=str(path))},
    )
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_launch_prefix",
        lambda: pytest.fail("local-only startup must not require an identity peer"),
    )
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    resolver = agent_registry.build_resolver(cfg)
    assert resolver._local_machine is None
    assert "local" in resolver.agents
    assert cfg.local_execution_space == "standalone-legacy-key"


@pytest.mark.parametrize("source_exists", [False, True])
def test_roster_build_ignores_source_binding_and_control_project(
    source, tmp_path, monkeypatch, source_exists,
):
    from agent_bridge import topology

    machines_path = tmp_path / "machines.yaml"
    machines_path.write_text(
        "control_plane:\n  project: unregistered-project\nmachines: {}\n", encoding="utf-8",
    )
    locator = source if source_exists else tmp_path / "missing-source"
    cfg = ServiceConfig(topologies={"source": TopologyProfile(
        machines_yaml=str(machines_path), source_repo=str(locator),
    )})
    cfg._source_worktree = tmp_path
    spaces = _spaces()
    monkeypatch.setattr(topology, "load_machines_yaml", lambda *a, **k: spaces)
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: [])
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    monkeypatch.setattr(agent_registry, "derive_topology_agents", lambda *a, **k: {})
    local = AgentConfig(name="project", project="project", auto_discovered=True)
    agents_path = tmp_path / "agents.json"
    agents_path.write_text(json.dumps({"registered": {
        "project": "project", "host": "native-a", "ssh_environment": "windows",
    }}), encoding="utf-8")
    cfg.topologies["source"].agents_config = str(agents_path)
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {"project": local})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    resolver = agent_registry.build_resolver(cfg)
    assert resolver._local_machine.key == "native-a"
    assert "registered" in resolver.agents
    assert "project" not in resolver.agents
    assert cfg.local_execution_space is None


@pytest.mark.parametrize("split_profiles", [False, True])
def test_unbound_topology_reaches_service_readiness(source, tmp_path, monkeypatch, split_profiles):
    import time

    import yaml
    from fastapi.testclient import TestClient

    import agent_bridge.app as app_module

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "no-projects.yaml"))
    entries = {
        key: {"hostname": "local-host", "execution_platform": platform}
        for key, platform in (("native", "windows"), ("guest", "wsl"))
    }
    groups = ({key: {key: entry} for key, entry in entries.items()}
              if split_profiles else {"source": entries})
    topologies = {}
    for name, machines in groups.items():
        machines_path = tmp_path / f"{name}.yaml"
        machines_path.write_text(yaml.safe_dump({
            "control_plane": {"project": "unregistered-project"}, "machines": machines,
        }), encoding="utf-8")
        topologies[name] = TopologyProfile(machines_yaml=str(machines_path))
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: [])
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {})
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    monkeypatch.setattr(agent_registry, "derive_topology_agents", lambda *a, **k: {})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)

    async def no_relay(*args, **kwargs):
        return None

    monkeypatch.setattr(app_module, "_start_credential_relay", no_relay)
    cfg = ServiceConfig(
        port=0, db_path=str(tmp_path / "test.db"), enable_credential_relay=False,
        topologies=topologies,
    )
    app = app_module.create_app(config=cfg, token="synthetic")
    app.state.publish_on_ready = False
    app.state.background_readiness = True
    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get("/health")
            if response.json()["ready"]:
                break
            time.sleep(0.02)
        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["topology_ready"] is True
        assert "readiness_error" not in response.json()
        assert app.state.resolver._local_machine.key == "native"


def test_combined_observation_drives_real_split_profile_derivation_once(source, tmp_path, monkeypatch):
    import yaml

    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "wsl")
    monkeypatch.setattr("socket.gethostname", lambda: "observed-host")
    topologies = {}
    for key, platform, hostname in (
        ("native", "windows", "observed-host"), ("guest", "wsl", ""),
    ):
        path = tmp_path / f"{key}.yaml"
        path.write_text(yaml.safe_dump({
            "control_plane": {"project": "control"},
            "machines": {key: {
                "hostname": hostname, "physical_host": "shared-family",
                "execution_platform": platform,
                "ssh": {"ready": False, "environments": [{
                    "name": platform, "alias": key + "-ssh",
                    "shell": "pwsh" if platform == "windows" else "bash",
                }]},
            }},
        }), encoding="utf-8")
        topologies[key] = TopologyProfile(machines_yaml=str(path))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    repos = [
        {"name": name, "agent": True, "paths": {"wsl": str(checkout)}}
        for name in ("control", "library")
    ]
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: repos)
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    local_agent = AgentConfig(name="library", project="library", auto_discovered=True)
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {"library": local_agent})
    detected = []
    original = agent_registry._detect_local_machine

    def observe(*args, **kwargs):
        detected.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_registry, "_detect_local_machine", observe)
    cfg = ServiceConfig(topologies=topologies)
    resolver = agent_registry.build_resolver(cfg)
    assert detected == [1]
    assert resolver._local_machine.key == "guest"
    assert resolver._local_platform == "wsl"
    assert "guest" in resolver.agents
    assert "library@guest" in resolver.agents
    assert local_agent.display_name == "guest (WSL)"
    assert cfg.local_execution_space is None
    resolver._own_plugin_args = lambda *args: []
    resolver._related_plugin_args = lambda *args: []
    assert resolver.resolve("guest").type == "local"
    assert resolver.resolve("library@guest").type == "local"


@pytest.mark.parametrize("alias,duplicate", [
    ("legacy", False), ("LEGACY", False), ("legacy", True), ("local-ssh", False),
])
def test_split_profile_remote_alias_cannot_become_local(source, tmp_path, monkeypatch, alias, duplicate):
    import yaml

    from agent_bridge.resolver_runtime import capture_resolver, restore_resolver
    from agent_bridge.routes.worktrees import _is_local_target

    topologies = {}
    entries = [
        ("legacy", "local-host", "windows", "local-ssh"),
        ("remote", "remote-host", "linux", alias),
    ]
    if duplicate:
        entries.append(("other-remote", "other-host", "linux", alias))
    for key, hostname, platform, ssh_alias in entries:
        machines_path = tmp_path / f"{key}.yaml"
        machines_path.write_text(yaml.safe_dump({"machines": {key: {
            "hostname": hostname,
            "ssh": {"ready": True, "environments": [{
                "name": platform, "alias": ssh_alias,
                "shell": "pwsh" if platform == "windows" else "bash",
            }]},
        }}}), encoding="utf-8")
        agents_path = tmp_path / f"{key}.json"
        agents_path.write_text(json.dumps({key: {
            "host": key, "project": "control", "ssh_environment": platform,
        }}), encoding="utf-8")
        topologies[key] = TopologyProfile(
            machines_yaml=str(machines_path), agents_config=str(agents_path),
        )
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: [])
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    original = agent_registry.build_resolver(ServiceConfig(topologies=topologies))
    for resolver in (original, restore_resolver(capture_resolver(original))):
        assert resolver._local_machine.key == "legacy"
        assert all(not machine.execution_platform for machine in resolver.machines.values())
        resolver._own_plugin_args = lambda *args: []
        resolver._related_plugin_args = lambda *args: []
        local = resolver.resolve("legacy")
        assert local.type == "local"
        assert _is_local_target(local.host, resolver, local.execution_space_key)
        assert not resolver.is_local_transport(alias)
        for key, _, _, _ in entries[1:]:
            remote = resolver.resolve(key)
            assert remote.type == "ssh"
            assert remote.host == alias
            assert remote.execution_space_key is None
            assert not _is_local_target(remote.host, resolver, remote.execution_space_key)


@pytest.mark.parametrize("file", [False, True])
def test_invalid_source_under_existing_repository_is_not_promoted(source, monkeypatch, file):
    from agent_bridge import service_start_cli

    locator = source / "not-a-directory"
    if file:
        locator.write_text("not a source worktree", encoding="utf-8")
    monkeypatch.setattr(config, "load_config", ServiceConfig)
    monkeypatch.setattr(config, "write_default_config", lambda cfg: pytest.fail("invalid origin was persisted"))
    with pytest.raises(ValueError, match="unavailable"):
        service_start_cli._cmd_start(argparse.Namespace(source_worktree=str(locator)))
    assert execution_identity.source_repository(locator) is None


def test_adopt_records_stable_source_locator_without_identity(source, monkeypatch):
    path = source / "machines.yaml"
    path.write_text("machines: {}\n", encoding="utf-8")
    monkeypatch.setattr(config, "load_config", ServiceConfig)
    saved = []
    monkeypatch.setattr(config, "save_config", saved.append)
    cfg = config.adopt_topology("source", str(source))
    assert cfg.topologies["source"].source_repo == str(source)
    assert cfg.local_execution_space is None
    assert saved == [cfg]


def test_malformed_explicit_selector_never_becomes_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text("local_execution_space: 'bad/key'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing identity fallback"):
        config.load_config()


@pytest.mark.parametrize("relative", [False, True])
def test_invalid_captured_source_identifies_path_before_writes(tmp_path, monkeypatch, relative):
    from agent_bridge import service_start_cli

    locator = "relative-source" if relative else str(tmp_path / "missing-source")
    monkeypatch.setattr(config, "load_config", ServiceConfig)
    monkeypatch.setattr(execution_identity, "source_repository", lambda path: None)
    monkeypatch.setattr(config, "write_default_config", lambda cfg: pytest.fail("invalid origin was persisted"))
    with pytest.raises(ValueError) as error:
        service_start_cli._cmd_start(argparse.Namespace(source_worktree=locator))
    assert repr(locator) in str(error.value)


@pytest.mark.parametrize("resolved", [False, True])
def test_unverified_legacy_launcher_receives_no_new_hidden_flag(source, tmp_path, monkeypatch, resolved):
    from types import SimpleNamespace

    from agent_bridge import service_process_cli

    monkeypatch.chdir(source)
    monkeypatch.setattr(service_process_cli.sys, "executable", "")
    monkeypatch.setattr(
        service_process_cli, "_core", lambda: SimpleNamespace(_INSTALL_DIR=str(tmp_path)),
    )
    executable = str(tmp_path / "legacy-bridge") if resolved else None
    monkeypatch.setattr(service_process_cli.shutil, "which", lambda name: executable)
    assert service_process_cli._daemon_launch_argv() == [executable or "agent-bridge", "start"]


@pytest.mark.parametrize("copy_config", [False, True], ids=["unchanged-config", "migrated-copy"])
def test_detached_launch_captures_source_and_child_retains_it_before_chdir(
    source, tmp_path, monkeypatch, copy_config,
):
    from agent_bridge import __main__ as main, service_start_cli, winjob
    from agent_bridge.singleton import AlreadyRunningError, SingleInstance

    monkeypatch.chdir(source)
    argv = main._daemon_launch_argv()
    assert argv[-2:] == ["--source-worktree", str(source)]
    monkeypatch.chdir(tmp_path)
    cfg = ServiceConfig()
    migrated = []

    def migrate(cfg):
        result = cfg.model_copy(update={"idle_reap_ttl_seconds": 600}) if copy_config else cfg
        migrated.append(result)
        return result

    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(config, "migrate_config", migrate)
    monkeypatch.setattr(config, "write_default_config", lambda cfg: None)
    monkeypatch.setattr(config, "load_or_create_auth_token", lambda: "synthetic")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(winjob, "setup_kill_on_close_job", lambda: None)
    monkeypatch.setattr(main, "_active_endpoint_is_forward", lambda: False)

    def held(self):
        raise AlreadyRunningError(lock_path=self.lock_path, holder_pid=1234)

    monkeypatch.setattr(SingleInstance, "acquire", held)
    service_start_cli._cmd_start(argparse.Namespace(
        source_worktree=str(source), port=None, bind=None, passive=False, idle_shutdown=None,
    ))
    assert migrated[-1]._source_worktree == source
    assert execution_identity.resolve_execution_space(migrated[-1], _spaces()) is None


def test_malformed_source_profile_never_becomes_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text(
        "topologies:\n  sample:\n    source_repo: 123\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="refusing identity fallback"):
        config.load_config()


@pytest.mark.parametrize(
    "data,message",
    [
        ({"topologies": {}, "remote_acp": "invalid"}, "Invalid remote ACP configuration"),
        ({"topologies": {}, "port": "invalid"}, "Invalid bridge configuration"),
        (
            {"local_execution_space": "bad/key", "remote_acp": {}},
            "Invalid execution-space or topology configuration",
        ),
    ],
)
def test_mixed_config_reports_actual_validation_source(tmp_path, monkeypatch, data, message):
    import yaml

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        config.load_config()


def test_real_linked_source_canonicalization_ignores_git_environment(tmp_path, monkeypatch):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is unavailable")
    anchor = tmp_path / "anchor"
    worktree = tmp_path / "worktree"
    foreign = tmp_path / "foreign"
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}

    def run(*args):
        subprocess.run(
            [git, *args], check=True, capture_output=True, env=environment,
            **no_window_kwargs(),
        )

    run("init", str(anchor))
    run("-C", str(anchor), "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "commit", "--allow-empty", "-m", "Synthetic source")
    run("-C", str(anchor), "worktree", "add", "--detach", str(worktree), "HEAD")
    run("init", str(foreign))
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(worktree))
    assert execution_identity.source_repository(worktree) == anchor


def test_source_repository_recognizes_bare_anchor(tmp_path):
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "objects").mkdir()
    (tmp_path / "config").write_text("[core]\nbare = true\n", encoding="utf-8")
    assert execution_identity.source_repository(tmp_path) == tmp_path
