"""Source context selects existing authority, never a second standing key."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_bridge import agent_registry, config, execution_identity, session_lifecycle_cli
from agent_bridge.agent_registry import AgentConfig, AgentResolver
from agent_bridge.models import ServiceConfig, TopologyProfile
from agent_bridge.topology import parse_machines_yaml


def _spaces(platform="windows"):
    return parse_machines_yaml({"machines": {
        key: {
            "execution_platform": kind, "physical_host": "shared-host",
            "ssh": {"ready": True, "environments": [
                {"name": kind, "alias": key + "-ssh", "shell": "bash" if kind != "windows" else "pwsh"},
            ]},
        }
        for key, kind in (("native-a", platform), ("native-b", platform), ("guest", "wsl"))
    }}, normalize=True)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setattr(config, "_canonical_repo_root", lambda root: root)
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "windows")
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda: ["owner"])
    return root


def _identity_packet(key="native-a", **updates):
    return {
        "schema": "agent-worktrees.execution-identity", "version": 1,
        "status": "ready", "configured": True, "source": "project-machine-config",
        "machine": key, "project": "project", "platform": agent_registry._detect_platform(),
        **updates,
    }


def _authority(monkeypatch, key="native-a", *, error=""):
    calls = []

    def query(prefix, args, cwd):
        calls.append((args, cwd))
        value = _identity_packet(key, **({"status": "refused", "reason": error} if error else {}))
        return subprocess.CompletedProcess(prefix + args, 1 if error else 0, json.dumps(value), error)

    monkeypatch.setattr(execution_identity, "_query", query)
    return calls


def test_source_capture_is_not_serialized_or_accepted_as_a_key(source, monkeypatch):
    monkeypatch.chdir(source)
    cfg = ServiceConfig()
    assert cfg._source_worktree == source
    assert "_source_worktree" not in cfg.model_dump()
    assert cfg.local_execution_space is None
    _authority(monkeypatch)
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-a"
    assert cfg.local_execution_space is None


@pytest.mark.parametrize("platform,key", [("windows", "native-a"), ("wsl", "guest"), ("linux", "native-b")])
def test_exact_native_guest_and_same_platform_keys(source, monkeypatch, platform, key):
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: platform)
    _authority(monkeypatch, key)
    cfg = ServiceConfig()
    cfg._source_worktree = source
    spaces = _spaces("linux" if platform == "linux" else "windows")
    assert execution_identity.resolve_execution_space(cfg, spaces) == key
    resolver = AgentResolver({}, spaces, local_execution_space=key)
    for other in spaces:
        assert resolver.is_local_transport(other, execution_space_key=other) == (other == key)


@pytest.mark.parametrize("key", ["shared-host", "native-a-ssh", "unknown", "guest", ""])
def test_derived_selection_must_be_canonical_and_match_platform(source, monkeypatch, key):
    _authority(monkeypatch, key)
    cfg = ServiceConfig()
    cfg._source_worktree = source
    with pytest.raises(execution_identity.ExecutionIdentityError, match="Source-project machine binding"):
        execution_identity.resolve_execution_space(cfg, _spaces())


def test_legacy_selector_cannot_override_authority(source, monkeypatch):
    _authority(monkeypatch)
    cfg = ServiceConfig(local_execution_space="native-b")
    cfg._source_worktree = source
    with pytest.raises(execution_identity.ExecutionIdentityError, match="contradicts"):
        execution_identity.resolve_execution_space(cfg, _spaces())
    cfg.local_execution_space = "native-a"
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-a"


@pytest.mark.parametrize("raw", [
    "native-a",
    json.dumps({"machine": "native-a"}),
    json.dumps({"schema": "agent-worktrees.execution-identity", "version": 2, "machine": "native-a"}),
])
def test_old_or_unknown_reader_cannot_grant_detected_identity(source, monkeypatch, raw):
    monkeypatch.setattr(
        execution_identity, "_query",
        lambda *args: subprocess.CompletedProcess([], 0, raw, ""),
    )
    cfg = ServiceConfig()
    cfg._source_worktree = source
    with pytest.raises(execution_identity.ExecutionIdentityError, match="Missing source-project execution binding"):
        execution_identity.resolve_execution_space(cfg, _spaces())
    cfg.local_execution_space = "native-b"
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-b"


@pytest.mark.parametrize("updates", [
    {"configured": False}, {"source": "detected"}, {"source": []},
    {"platform": "wsl"}, {"project": ""}, {"machine": []},
])
def test_compatible_ready_response_requires_configured_binding(source, monkeypatch, updates):
    monkeypatch.setattr(
        execution_identity, "_query",
        lambda *args: subprocess.CompletedProcess([], 0, json.dumps(_identity_packet(**updates)), ""),
    )
    cfg = ServiceConfig(local_execution_space="native-b")
    cfg._source_worktree = source
    with pytest.raises(execution_identity.ExecutionIdentityError, match="invalid authority"):
        execution_identity.resolve_execution_space(cfg, _spaces())


def test_missing_sibling_preserves_valid_standalone_and_legacy(source, monkeypatch):
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda: None)
    cfg = ServiceConfig()
    cfg._source_worktree = source
    assert execution_identity.resolve_execution_space(cfg, {}) is None
    with pytest.raises(execution_identity.ExecutionIdentityError, match="Missing source-project execution binding"):
        execution_identity.resolve_execution_space(cfg, _spaces())
    cfg.local_execution_space = "native-b"
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-b"


def test_local_only_legacy_config_needs_no_machine_inventory_or_peer(tmp_path, monkeypatch):
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


def test_authority_refusal_does_not_become_legacy_success(source, monkeypatch):
    def query(prefix, args, cwd):
        return subprocess.CompletedProcess([], 1, json.dumps(_identity_packet(
            status="refused", reason="configured execution-space platform mismatch",
        )), "")

    monkeypatch.setattr(execution_identity, "_query", query)
    cfg = ServiceConfig(local_execution_space="native-a")
    cfg._source_worktree = source
    with pytest.raises(execution_identity.ExecutionIdentityError, match="platform mismatch"):
        execution_identity.resolve_execution_space(cfg, _spaces())


@pytest.mark.parametrize("duplicate", [False, True])
def test_adopted_source_survives_neutral_cwd_and_recomputes_config(
    source, tmp_path, monkeypatch, duplicate,
):
    cfg = ServiceConfig(topologies={"source": TopologyProfile(source_repo=str(source))})
    if duplicate:
        cfg.topologies["duplicate"] = TopologyProfile(source_repo=str(source))
    cfg._source_worktree = tmp_path
    calls = _authority(monkeypatch)
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-a"
    assert calls == [(["get", "execution-identity"], source)]
    _authority(monkeypatch, "native-b")
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-b"


@pytest.mark.parametrize("relative", [False, True])
def test_invalid_source_locator_identifies_profile_and_path(source, tmp_path, monkeypatch, relative):
    locator = "relative-source" if relative else str(tmp_path / "missing-source")
    cfg = ServiceConfig(topologies={"sample-profile": TopologyProfile(source_repo=locator)})
    cfg._source_worktree = tmp_path
    with pytest.raises(execution_identity.ExecutionIdentityError) as error:
        execution_identity.resolve_execution_space(cfg, _spaces())
    assert "sample-profile" in str(error.value)
    assert repr(locator) in str(error.value)


def test_declared_control_project_precedes_topology_storage_repository(source, tmp_path, monkeypatch):
    machines = source / "machines.yaml"
    machines.write_text("control_plane:\n  project: control-project\nmachines: {}\n", encoding="utf-8")
    cfg = ServiceConfig(topologies={"shared": TopologyProfile(machines_yaml=str(machines))})
    cfg._source_worktree = tmp_path
    calls = _authority(monkeypatch)
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-a"
    assert calls == [
        (["--project", "control-project", "get", "execution-identity"], None),
    ]


def test_adopted_source_precedes_legacy_control_project(source, tmp_path, monkeypatch):
    machines = source / "machines.yaml"
    machines.write_text("control_plane:\n  project: legacy-control\nmachines: {}\n", encoding="utf-8")
    cfg = ServiceConfig(topologies={"adopted": TopologyProfile(
        source_repo=str(source), machines_yaml=str(machines),
    )})
    cfg._source_worktree = tmp_path
    calls = _authority(monkeypatch)
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-a"
    assert calls == [(["get", "execution-identity"], source)]


def test_unregistered_optional_project_preserves_valid_legacy_selector(source, tmp_path, monkeypatch):
    cfg = ServiceConfig(
        local_execution_space="native-b",
        topologies={"source": TopologyProfile(source_repo=str(source))},
    )
    cfg._source_worktree = tmp_path
    calls = []

    def query(prefix, args, cwd):
        calls.append(args)
        assert args[-1] == "execution-identity"
        return subprocess.CompletedProcess([], 0, json.dumps(_identity_packet(
            status="unavailable", reason="project-not-registered",
        )), "")

    monkeypatch.setattr(execution_identity, "_query", query)
    assert execution_identity.resolve_execution_space(cfg, _spaces()) == "native-b"
    cfg.local_execution_space = None
    with pytest.raises(execution_identity.ExecutionIdentityError, match="Unresolved sources"):
        execution_identity.resolve_execution_space(cfg, _spaces())
    assert calls == [["get", "execution-identity"], ["get", "execution-identity"]]


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


def test_conflicting_adopted_sources_fail_closed(source, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    cfg = ServiceConfig(topologies={
        "one": TopologyProfile(source_repo=str(source)),
        "two": TopologyProfile(source_repo=str(other)),
    })
    cfg._source_worktree = tmp_path

    def query(prefix, args, cwd):
        return subprocess.CompletedProcess([], 0, json.dumps(_identity_packet(
            "native-a" if cwd == source else "native-b",
        )), "")

    monkeypatch.setattr(execution_identity, "_query", query)
    with pytest.raises(execution_identity.ExecutionIdentityError, match="different execution spaces") as error:
        execution_identity.resolve_execution_space(cfg, _spaces())
    assert "native-a" in str(error.value) and "native-b" in str(error.value)
    assert repr(str(source)) in str(error.value) and repr(str(other)) in str(error.value)


def test_discovery_coverage_propagates_derived_selector_to_nested_resolver(source, tmp_path, monkeypatch):
    machines_path = tmp_path / "machines.yaml"
    machines_path.write_text("machines: {}\n", encoding="utf-8")
    cfg = ServiceConfig(topologies={"source": TopologyProfile(machines_yaml=str(machines_path))})
    cfg._source_worktree = source
    _authority(monkeypatch)
    from agent_bridge import topology

    spaces = _spaces()
    monkeypatch.setattr(topology, "load_machines_yaml", lambda *a, **k: spaces)
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: [])
    monkeypatch.setattr(agent_registry, "infer_control_plane_project", lambda *a: None)
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    monkeypatch.setattr(agent_registry, "derive_topology_agents", lambda *a, **k: {})
    local = AgentConfig(name="project", project="project", auto_discovered=True)
    registered = AgentConfig(
        name="registered", project="project", host="native-a", ssh_environment="windows",
    )
    agents_path = tmp_path / "agents.json"
    agents_path.write_text(json.dumps({"registered": {
        "project": "project", "host": "native-a", "ssh_environment": "windows",
    }}), encoding="utf-8")
    cfg.topologies["source"].agents_config = str(agents_path)
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {"project": local})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    assert agent_registry._find_covering_agent(local, {"registered": registered}, spaces, "native-a") == "registered"
    resolver = agent_registry.build_resolver(cfg)
    assert resolver._local_machine.key == "native-a"
    assert "registered" in resolver.agents
    assert "project" not in resolver.agents
    assert cfg.local_execution_space is None


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


def test_adopt_records_only_stable_source_locator(source, monkeypatch):
    path = source / "machines.yaml"
    path.write_text("machines: {}\n", encoding="utf-8")
    monkeypatch.setattr(config, "load_config", ServiceConfig)
    saved = []
    monkeypatch.setattr(config, "save_config", saved.append)
    cfg = config.adopt_topology("source", str(source))
    assert cfg.topologies["source"].source_repo == str(source)
    assert cfg.local_execution_space is None
    assert saved == [cfg]


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
    _authority(monkeypatch)
    assert execution_identity.resolve_execution_space(migrated[-1], _spaces()) == "native-a"


def test_native_query_preserves_cwd_argv_and_reads_changed_binding(source, monkeypatch):
    binding = source / "machine.yaml"
    binding.write_text(yaml.safe_dump({"machine": "native-a"}), encoding="utf-8")
    peer = source / "synthetic_owner.py"
    peer.write_text(
        "import pathlib,sys,yaml\n"
        "assert sys.argv[1:] == ['get','machine']\n"
        "print(yaml.safe_load(pathlib.Path('machine.yaml').read_text())['machine'])\n",
        encoding="utf-8",
    )
    prefix = [sys.executable, str(peer)]
    assert execution_identity._query(prefix, ["get", "machine"], source).stdout.strip() == "native-a"
    binding.write_text(yaml.safe_dump({"machine": "native-b"}), encoding="utf-8")
    assert execution_identity._query(prefix, ["get", "machine"], source).stdout.strip() == "native-b"


@pytest.mark.parametrize("error", [OSError("missing peer"), subprocess.TimeoutExpired(["peer"], 30)])
def test_query_failure_is_explicit(source, monkeypatch, error):
    with patch.object(subprocess, "run", side_effect=error):
        with pytest.raises(execution_identity.ExecutionIdentityError, match="Cannot read"):
            execution_identity._query(["owner"], ["get", "machine"], source)


def test_malformed_legacy_selector_never_becomes_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text("local_execution_space: 'bad/key'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing identity fallback"):
        config.load_config()


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


def test_query_strips_git_routing_but_preserves_unrelated_environment(source, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "foreign")
    monkeypatch.setenv("GIT_WORK_TREE", "foreign")
    monkeypatch.setenv("SYNTHETIC_IDENTITY_TEST", "retained")
    with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
        execution_identity._query(["owner"], ["get", "machine"], source)
    environment = run.call_args.kwargs["env"]
    assert not any(key.upper().startswith("GIT_") for key in environment)
    assert environment["SYNTHETIC_IDENTITY_TEST"] == "retained"
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL


def test_real_identity_reader_receives_eof_instead_of_caller_stdin(source):
    result = execution_identity._query(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], [], source,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "''"


def test_source_repository_recognizes_registered_bare_anchor(tmp_path):
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "objects").mkdir()
    (tmp_path / "config").write_text("[core]\nbare = true\n", encoding="utf-8")
    assert execution_identity.source_repository(tmp_path) == tmp_path
