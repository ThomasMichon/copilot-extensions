"""Update policy must retain explicit execution-space configuration."""

from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, reconcile, repos, update_gate


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
    monkeypatch.setattr(cfg, "load_config", lambda: load(
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
    with pytest.raises(RuntimeError, match="explicit machine configuration"):
        cfg.detect_machine(anchor)
    assert update_gate._machine_gate_blocks(
        "agent-example", "machine-gated", anchor if explicit_context else None,
    ) is (not allowed)


@pytest.mark.parametrize("platform", ["windows", "linux", "wsl"])
@pytest.mark.parametrize("explicit_context", [False, True])
def test_update_gate_does_not_promote_unconfigured_hostname(
    tmp_path, monkeypatch, platform, explicit_context,
):
    anchor = _configure(tmp_path, monkeypatch, platform, configured=False)
    with pytest.raises(update_gate.MachineDetectionError, match="explicit machine configuration"):
        update_gate._machine_gate_blocks(
            "agent-example", "machine-gated", anchor if explicit_context else None,
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
    with pytest.raises(update_gate.MachineDetectionError):
        update_gate._machine_gate_blocks("agent-example", "machine-gated", anchor)


def test_universal_runtime_does_not_require_machine_config(monkeypatch):
    def unavailable():
        raise AssertionError("universal runtime must not load machine configuration")

    monkeypatch.setattr(cfg, "load_config", unavailable)
    assert not update_gate._machine_gate_blocks("agent-example", "universal", Path("/unused"))
