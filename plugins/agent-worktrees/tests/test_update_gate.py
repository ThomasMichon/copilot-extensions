"""Update policy must retain explicit execution-space configuration."""

from pathlib import Path
from dataclasses import replace
import os
import subprocess
import sys

import pytest
import yaml

from agent_worktrees import config as cfg, reconcile, repos, update_gate
from agent_procutil import no_window_kwargs


def _configure(tmp_path, monkeypatch, platform, *, configured=True):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text(yaml.safe_dump({
        "machines": {
            "local-space": {
                "execution_platform": platform,
                "hostname": "shared-host",
            },
        },
    }), encoding="utf-8")
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        yaml.safe_dump({"machine": "local-space"} if configured else {}),
        encoding="utf-8",
    )
    project_path = tmp_path / "project.yaml"
    project_path.write_text(yaml.safe_dump({
        "repo_name": "project",
        "repos": {"project": {
            "anchor": str(anchor),
            "worktree_root": str(tmp_path / "trees"),
        }},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(repos, "read_registry", lambda: repos.ReposRegistry())
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr("socket.gethostname", lambda: "shared-host")
    load = cfg.load_config
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: load(
        project_path, project="project", include_control_plane_related_pr=False,
    ))
    return anchor


@pytest.mark.parametrize("platform", ["windows", "linux", "wsl"])
@pytest.mark.parametrize("explicit_context", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
def test_update_gate_uses_layered_execution_identity(
    tmp_path, monkeypatch, platform, explicit_context, allowed,
):
    anchor = _configure(tmp_path, monkeypatch, platform)
    (anchor / "services.yaml").write_text(yaml.safe_dump({
        "plugins": [{
            "name": "agent-example",
            "deploy_machines": ["local-space" if allowed else "other-space"],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(reconcile, "GATE_ANCHOR", "")
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))

    assert cfg.load_config().machine == "local-space"
    assert cfg.load_config().configured_machine == "local-space"
    assert cfg.detect_machine(anchor) == "local-space"
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", anchor if explicit_context else None,
    ) is (not allowed)


@pytest.mark.parametrize("platform", ["windows", "linux", "wsl"])
@pytest.mark.parametrize("explicit_context", [False, True])
@pytest.mark.parametrize("policy", ["absent", "matching", "foreign", "omitted"])
def test_update_gate_uses_local_naming_without_bypassing_runtime_policy(
    tmp_path, monkeypatch, platform, explicit_context, policy,
):
    from agent_worktrees import front_door_cli

    anchor = _configure(tmp_path, monkeypatch, platform, configured=False)
    if policy != "absent":
        (anchor / "services.yaml").write_text(yaml.safe_dump({
            "plugins": [] if policy == "omitted" else [{
                "name": "agent-example",
                "deploy_machines": ["local-space" if policy == "matching" else "other-space"],
            }],
        }), encoding="utf-8")
    monkeypatch.setattr(reconcile, "GATE_ANCHOR", "")
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))
    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda repo: anchor)
    monkeypatch.setattr(front_door_cli, "_reverse_lookup_project", lambda repo: "project")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo: [])
    assert cfg.load_config().configured_machine is None
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", anchor if explicit_context else None,
    ) is (policy in ("foreign", "omitted"))
    plan = reconcile.build_plan(anchor, cache={}, save=False)
    assert plan["machine"] == "local-space"
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", plan["machine"],
        reconcile.load_runtime_gate(anchor), gate_present=reconcile.gate_manifest_present(anchor),
        repo_dir=anchor, config=cfg.load_config(),
    ) is (policy in ("absent", "matching"))


@pytest.mark.parametrize("case", ["missing-guest", "literal-linux-hostname", "literal-linux-matching"])
@pytest.mark.parametrize("selector", ["execution", "physical", "foreign"])
def test_local_runtime_policy_does_not_reinterpret_local_name_as_target(
    tmp_path, monkeypatch, case, selector,
):
    from agent_worktrees import front_door_cli

    actual = "wsl" if case == "missing-guest" else "linux"
    anchor = _configure(tmp_path, monkeypatch, actual, configured=False)
    hostname = "stable-host" if case == "missing-guest" else "literal-host-win"
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    machines = {"stable-host": {
        "execution_platform": "windows", "physical_host": "stable-host",
        "hostname": hostname,
    }} if case == "missing-guest" else (
        {hostname: {"hostname": hostname}} if case == "literal-linux-matching" else {}
    )
    (anchor / "machines.yaml").write_text(
        yaml.safe_dump({"machines": machines}), encoding="utf-8",
    )
    machine = "stable-host-wsl" if case == "missing-guest" else hostname
    allowed = {"execution": machine, "physical": hostname, "foreign": "other-host"}[selector]
    (anchor / "services.yaml").write_text(yaml.safe_dump({
        "plugins": [{"name": "agent-example", "deploy_machines": [allowed]}],
    }), encoding="utf-8")
    monkeypatch.setattr(reconcile, "GATE_ANCHOR", "")
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))
    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda repo: anchor)
    monkeypatch.setattr(front_door_cli, "_reverse_lookup_project", lambda repo: "project")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo: [])
    assert cfg.detect_machine(anchor) == machine
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", anchor,
    ) is (selector == "foreign")
    plan = reconcile.build_plan(anchor, cache={}, save=False)
    assert plan["machine"] == machine
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", plan["machine"], reconcile.load_runtime_gate(anchor),
        repo_dir=anchor, config=cfg.load_config(),
    ) is (selector != "foreign")


def test_runtime_policy_keeps_nonlocal_target_and_platform_refusals(tmp_path, monkeypatch):
    anchor = _configure(tmp_path, monkeypatch, "windows", configured=False)
    source = anchor / "machines.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["machines"]["local-space"]["physical_host"] = "shared-host"
    raw["machines"].update({
        "peer-space": {"execution_platform": "windows", "physical_host": "peer-host"},
        "guest-space": {"execution_platform": "wsl", "physical_host": "shared-host"},
    })
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = cfg.load_config()
    gate = {"agent-example": {"shared-host"}}
    assert not reconcile.runtime_allowed(
        "machine-gated", "agent-example", "peer-space", gate, repo_dir=anchor, config=config,
    )
    with pytest.raises(ValueError, match="conflicts"):
        reconcile.runtime_allowed(
            "machine-gated", "agent-example", "guest-space", gate, repo_dir=anchor, config=config,
        )


@pytest.mark.parametrize("registry", ["empty", "unrelated"])
@pytest.mark.parametrize("target", ["peer-wsl", "PEER-wsl"])
def test_unknown_nonlocal_runtime_target_retains_exact_name(tmp_path, monkeypatch, registry, target):
    anchor = _configure(tmp_path, monkeypatch, "wsl", configured=False)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "local-host")
    machines = {} if registry == "empty" else {
        "other-space": {"execution_platform": "linux", "physical_host": "other-host"},
    }
    (anchor / "machines.yaml").write_text(
        yaml.safe_dump({"machines": machines}), encoding="utf-8",
    )
    config = cfg.load_config()
    assert not reconcile.runtime_allowed(
        "machine-gated", "agent-example", target,
        {"agent-example": {"peer", "PEER"}}, repo_dir=anchor, config=config,
    )
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", target,
        {"agent-example": {target}}, repo_dir=anchor, config=config,
    )


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("field", ["key", "alias", "compatibility_alias", "profile"])
@pytest.mark.parametrize("conflicting_platform", [False, True])
def test_registered_target_identity_precedes_observed_hostname(
    tmp_path, monkeypatch, actual, field, conflicting_platform,
):
    anchor = _configure(tmp_path, monkeypatch, actual)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    source = anchor / "machines.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["machines"]["local-space"]["physical_host"] = "local-host"
    peer_platform = ("wsl" if actual == "windows" else "windows") if conflicting_platform else actual
    peer = {"execution_platform": peer_platform, "physical_host": "peer-host"}
    if field == "alias":
        peer["alias"] = "raw-host"
    elif field == "compatibility_alias":
        peer["compatibility_aliases"] = ["raw-host"]
    elif field == "profile":
        peer["ssh"] = {"environments": [{"name": peer_platform, "alias": "raw-host"}]}
    raw["machines"]["raw-host" if field == "key" else "peer-space"] = peer
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = cfg.load_config()

    def allowed():
        return reconcile.runtime_allowed(
            "machine-gated", "agent-example", "raw-host",
            {"agent-example": {"local-host"}}, repo_dir=anchor, config=config,
        )

    if conflicting_platform:
        with pytest.raises(ValueError, match="conflicts"):
            allowed()
    else:
        assert not allowed()


@pytest.mark.parametrize("actual", ["windows", "linux"])
@pytest.mark.parametrize("hostname", ["literal-host-win", "literal-host-wsl"])
@pytest.mark.parametrize("selector", ["alias", "physical", "foreign"])
def test_literal_native_customization_alias_keeps_update_and_launch_context(
    tmp_path, monkeypatch, actual, hostname, selector,
):
    from agent_worktrees import front_door_cli

    anchor = _configure(tmp_path, monkeypatch, actual, configured=False)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        hostname: {"hostname": hostname, "alias": "local-alias"},
    }}), encoding="utf-8")
    target = {"alias": "local-alias", "physical": hostname, "foreign": "other-host"}[selector]
    (anchor / "services.yaml").write_text(yaml.safe_dump({
        "plugins": [{"name": "agent-example", "deploy_machines": [target]}],
    }), encoding="utf-8")
    monkeypatch.setattr(reconcile, "GATE_ANCHOR", "")
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))
    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda repo: anchor)
    monkeypatch.setattr(front_door_cli, "_reverse_lookup_project", lambda repo: "project")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo: [])
    assert cfg.detect_machine(anchor) == "local-alias"
    assert cfg.load_config().machine == hostname
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", anchor,
    ) is (selector == "foreign")
    plan = reconcile.build_plan(anchor, cache={}, save=False)
    assert plan["machine"] == "local-alias"
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", plan["machine"], reconcile.load_runtime_gate(anchor),
        repo_dir=anchor, config=cfg.load_config(),
    ) is (selector != "foreign")


@pytest.mark.parametrize("actual", ["windows", "linux"])
def test_literal_native_customization_retains_conflicting_profile_refusal(tmp_path, monkeypatch, actual):
    anchor = _configure(tmp_path, monkeypatch, actual, configured=False)
    hostname = "literal-host-win"
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        hostname: {
            "hostname": hostname, "alias": "local-alias",
            "ssh": {"environments": [{
                "name": "wsl" if actual == "windows" else "windows", "alias": "wrong-profile",
            }]},
        },
    }}), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        reconcile.runtime_allowed(
            "machine-gated", "agent-example", "wrong-profile",
            {"agent-example": {hostname}}, repo_dir=anchor, config=cfg.load_config(),
        )


@pytest.mark.parametrize("override_source", ["project", "dropin"])
def test_update_gate_honors_project_machine_precedence(tmp_path, monkeypatch, override_source):
    anchor = _configure(tmp_path, monkeypatch, "windows")
    registry = anchor / "machines.yaml"
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    data["machines"]["project-space"] = {"execution_platform": "windows"}
    registry.write_text(yaml.safe_dump(data), encoding="utf-8")
    project = tmp_path / "project.yaml"
    if override_source == "project":
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        data["machine"] = "project-space"
        project.write_text(yaml.safe_dump(data), encoding="utf-8")
    else:
        directory = tmp_path / "config.d"
        directory.mkdir()
        (directory / "identity.yaml").write_text("machine: project-space\n", encoding="utf-8")
    monkeypatch.setattr(reconcile, "load_runtime_gate",
                        lambda repo: {"agent-example": {"project-space"}})
    monkeypatch.setattr(reconcile, "gate_manifest_present", lambda repo: True)

    assert cfg.load_config().configured_machine == "project-space"
    assert not update_gate._machine_gate_blocks("agent-example", "machine-gated", anchor)


def test_update_gate_reports_invalid_registry(tmp_path, monkeypatch):
    anchor = _configure(tmp_path, monkeypatch, "windows")
    (anchor / "machines.yaml").write_text("machines: [\n", encoding="utf-8")
    with pytest.raises(update_gate.MachineDetectionError, match="agent-example"):
        update_gate._machine_gate_blocks("agent-example", "machine-gated", anchor)


@pytest.mark.parametrize("platform", ["windows", "wsl"])
@pytest.mark.parametrize("physical", ["physical-host", "physical-host-wsl"])
@pytest.mark.parametrize("allowed", [False, True])
def test_update_gate_selects_declared_physical_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, physical: str, allowed: bool,
) -> None:
    anchor = _configure(tmp_path, monkeypatch, platform)
    registry = anchor / "machines.yaml"
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    data["machines"]["local-space"]["physical_host"] = physical
    registry.write_text(yaml.safe_dump(data), encoding="utf-8")
    (anchor / "services.yaml").write_text(yaml.safe_dump({
        "plugins": [{
            "name": "agent-example",
            "deploy_machines": [physical if allowed else "foreign-host"],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(reconcile, "GATE_ANCHOR", "")
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))

    assert cfg.load_config().machine == "local-space"
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", anchor,
    ) is (not allowed)
    from agent_worktrees import front_door_cli

    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda repo: anchor)
    monkeypatch.setattr(front_door_cli, "_reverse_lookup_project", lambda repo: "project")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo: [])
    plan = reconcile.build_plan(anchor, cache={}, save=False)
    assert plan["machine"] == "local-space"
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", plan["machine"],
        reconcile.load_runtime_gate(anchor), repo_dir=anchor,
    ) is allowed
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", plan["machine"],
        {"agent-example": {"local-space"}}, repo_dir=anchor,
    )
    assert cfg.load_config().machine == "local-space"


@pytest.mark.parametrize("ambient_available", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
def test_update_and_launch_use_explicit_target_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ambient_available: bool, allowed: bool,
) -> None:
    from agent_worktrees import front_door_cli

    ambient_anchor = _configure(tmp_path, monkeypatch, "wsl")
    ambient_config = cfg.load_config()
    target = tmp_path / "target"
    target.mkdir()
    (target / "machines.yaml").write_text(yaml.safe_dump({
        "machines": {
            "local-space": {"execution_platform": "wsl", "physical_host": "ambient-host"},
            "target-space": {"execution_platform": "wsl", "physical_host": "physical-host"},
        },
    }), encoding="utf-8")
    (target / "services.yaml").write_text(yaml.safe_dump({
        "plugins": [{
            "name": "agent-example",
            "deploy_machines": ["physical-host" if allowed else "foreign-host"],
        }],
    }), encoding="utf-8")
    target_config = replace(
        ambient_config, machine="target-space", configured_machine="target-space",
        repo_name="target-project", repos={"target-project": cfg.RepoConfig(
            anchor=str(target), worktree_root=str(tmp_path / "target-trees"),
        )},
    )

    def load(
        path: Path | None = None, *, project: str | None = None, **kwargs: object,
    ) -> cfg.Config:
        if path == cfg.project_dir("target-project") / "config.yaml" and project == "target-project":
            return target_config
        if not ambient_available:
            raise RuntimeError("No active project could be resolved")
        return ambient_config

    monkeypatch.setattr(cfg, "load_config", load)
    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda path: target)
    monkeypatch.setattr(
        front_door_cli, "_reverse_lookup_project",
        lambda anchor: "target-project" if anchor == target else None,
    )
    monkeypatch.setattr(reconcile, "GATE_ANCHOR", "")
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo: [])
    plan = reconcile.build_plan(target, cache={}, save=False)
    assert plan["machine"] == "target-space"
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", target,
    ) is (not allowed)
    assert reconcile.runtime_allowed(
        "machine-gated", "agent-example", plan["machine"],
        reconcile.load_runtime_gate(target), repo_dir=target, config=target_config,
    ) is allowed
    assert ambient_config.default_repo.anchor == str(ambient_anchor)


def test_empty_machine_setting_retains_existing_global_fallback(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, "windows")
    project = tmp_path / "project.yaml"
    data = yaml.safe_load(project.read_text(encoding="utf-8"))
    data["machine"] = ""
    project.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert cfg.load_config().configured_machine == "local-space"


def test_universal_runtime_does_not_require_machine_config(monkeypatch):
    def unavailable():
        raise AssertionError("universal runtime must not load machine configuration")

    monkeypatch.setattr(cfg, "load_config", unavailable)
    assert not update_gate._machine_gate_blocks("agent-example", "universal", Path("/unused"))


def test_config_keyword_only_identity_preserves_positional_arguments():
    repositories = {"project": cfg.RepoConfig(anchor="/example", worktree_root="/trees")}
    config = cfg.Config("/source", "local-space", "windows", "project", repositories,
                        configured_machine="local-space")
    assert config.repo_name == "project"
    assert config.repos is repositories
    assert config.configured_machine == "local-space"


def test_native_update_gate_reads_explicit_machine_from_isolated_home(tmp_path):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text(yaml.safe_dump({
        "machines": {"local-space": {"execution_platform": cfg.detect_platform()}},
    }), encoding="utf-8")
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    (runtime / "config.yaml").write_text("machine: local-space\n", encoding="utf-8")
    project = tmp_path / ".project"
    project.mkdir()
    (project / "config.yaml").write_text(yaml.safe_dump({
        "repo_name": "project",
        "repos": {"project": {"anchor": str(anchor), "worktree_root": str(tmp_path / "trees")}},
    }), encoding="utf-8")
    environment = dict(os.environ, AGENT_HOME=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "from agent_worktrees import config, update_gate; "
         "config.set_active_project('project'); "
         "assert config.load_config().configured_machine == 'local-space'; "
         "assert not update_gate._machine_gate_blocks('agent-example', 'machine-gated', None); "
         "print('native-update-gate-ok')"],
        env=environment, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "native-update-gate-ok"
