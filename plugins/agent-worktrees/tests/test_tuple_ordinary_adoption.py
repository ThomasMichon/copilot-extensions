"""Ordinary adoption uses the receiving project and preserves retained winners."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as cfg, creation_identity, execution_spaces, git_ops, repos
from agent_worktrees import session_binding_cli, tracking, worktree_identity, worktree_ops_cli
from agent_worktrees.worktree_identity import _infer_worktree_id_from_cwd as actual_infer
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_register_session import _args
from test_tuple_adoption import adoption_config as adoption_config, _produce, _retain_history


@pytest.mark.parametrize("surface", ["infer", "register"])
def test_ordinary_external_adoption_keeps_git_identity(
    adoption_config: cfg.Config, monkeypatch: pytest.MonkeyPatch, surface: str,
) -> None:
    config = adoption_config
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id_from_cwd", actual_infer)
    cfg.project_dir().mkdir()
    (cfg.project_dir() / "config.yaml").write_text(yaml.safe_dump({
        "repo_name": config.repo_name, "machine": config.machine,
        "repos": {config.repo_name: {
            "anchor": config.default_repo.anchor,
            "worktree_root": config.default_repo.worktree_root,
        }},
    }))
    authority_path = cfg.inrepo_config_path(Path(config.default_repo.anchor))
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    authority_path.write_text("default_branch: master\nstateless: false\n")
    git_ops.git("add", str(authority_path), cwd=config.default_repo.anchor)
    git_ops.git("commit", "-m", "register fixture authority", cwd=config.default_repo.anchor)
    root = Path(config.srcroot) / "external" / "desk-wsl-wsl-retained-01234567"
    root.parent.mkdir()
    git_ops.git(
        "worktree", "add", str(root), "-b", "retained-external", "master",
        cwd=config.default_repo.anchor,
    )
    if surface == "infer":
        monkeypatch.chdir(root)
        assert worktree_identity._infer_worktree_id_from_cwd(config) == root.name
    else:
        monkeypatch.setattr(session_binding_cli, "_status_monitor_enabled", lambda: False)
        monkeypatch.setattr(session_binding_cli, "_activate_hook_session_binding", lambda *_a, **_k: None)
        monkeypatch.setattr(session_binding_cli, "_activate_project_for_path", lambda *_a, **_k: None)
        monkeypatch.setattr(
            session_binding_cli, "_register_manager_owned_monitor_session", lambda *_a, **_k: True,
        )
        monkeypatch.setattr(
            session_binding_cli.worktree_trust, "ensure_record_trusted", lambda _record: False,
        )
        assert session_binding_cli.cmd_register_session(_args(
            cwd=str(root), session_id="ordinary-session", pane="synthetic-pane",
        )) == 0
    record = tracking.load_record(cfg.tracking_dir() / f"{root.name}.yaml")
    assert record.machine_identity == WorktreeMachineIdentity(
        "desk-wsl", config.platform, "custom-space",
    )
    assert record.worktree_id == root.name and record.branch == "retained-external"
    assert Path(record.worktree_path) == root
    assert record.checkout_managed is False
    before = _retain_history(record)
    monkeypatch.chdir(root)
    assert worktree_identity._infer_worktree_id_from_cwd(config) == root.name
    assert record.yaml_path.read_bytes() == before


@pytest.fixture(params=["windows", "wsl", "linux"])
def receiving_anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    platform = request.param
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "ambient")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / (name or cfg.active_project()))
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no-global.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "observed-host")
    for project, physical in (("ambient", "ambient-host"), ("receiving", "receiving-host")):
        directory = cfg.project_dir(project)
        directory.mkdir()
        anchor = directory / "anchor"
        anchor.mkdir()
        (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
            f"{project}-space": {
                "hostname": "observed-host", "physical_host": physical,
                "execution_platform": platform,
            },
        }}))
        (directory / "config.yaml").write_text(yaml.safe_dump({
            "repo_name": project, "repos": {project: {
                "anchor": str(anchor), "worktree_root": str(directory / "trees"),
            }},
        }))
    config = cfg.load_project_config("receiving", include_control_plane_related_pr=False)
    monkeypatch.setattr(repos, "find_repo", lambda name: SimpleNamespace(
        repo_class="singleton", local_path=lambda: str(cfg.project_dir(name) / "anchor"),
    ))
    assert cfg.load_config().repo_name == "ambient"
    return config


def test_ordinary_anchor_uses_actual_receiving_configuration(receiving_anchor) -> None:
    config = receiving_anchor
    record = worktree_ops_cli._ensure_anchor_ledger(
        tracking.parse_claim_ref("receiving-space/receiving/@anchor"),
        cfg.tracking_dir("receiving"),
    )
    assert record.machine_identity == WorktreeMachineIdentity(
        "receiving-host", config.platform, "receiving-space",
    )
    assert record.repo == "receiving"
    assert record.worktree_path == config.default_repo.anchor
    assert record.yaml_path.parent == cfg.tracking_dir("receiving")
    assert cfg.active_project() == "ambient"
    assert not list(cfg.tracking_dir("ambient").glob("*.yaml"))


@pytest.mark.parametrize("mode", ["legacy", "tuple-v1"])
def test_ordinary_anchor_retains_existing_winner(receiving_anchor, mode) -> None:
    config = receiving_anchor
    original = tracking.load_or_create_anchor_record(
        config.default_repo.anchor, config.repo_name, config.machine, config.platform,
        cfg.tracking_dir("receiving"), identity_mode=mode, config=config,
    )
    before = _retain_history(original)
    retained = worktree_ops_cli._ensure_anchor_ledger(
        tracking.parse_claim_ref("receiving-space/receiving/@anchor"),
        cfg.tracking_dir("receiving"),
    )
    assert retained.yaml_path.read_bytes() == before


def test_ordinary_anchor_preserves_competing_legacy_winner(
    receiving_anchor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = tracking.create_new_record_if_absent
    winners = []

    def competing_create(*args, **kwargs):
        legacy = {key: value for key, value in kwargs.items() if key != "machine_identity_factory"}
        winner, created = create(*args, **legacy)
        assert created and winner.machine_identity is None
        winners.append(_retain_history(winner))
        return create(*args, **kwargs)

    monkeypatch.setattr(tracking, "create_new_record_if_absent", competing_create)
    monkeypatch.setattr(
        creation_identity, "new_machine_identity",
        lambda *_a: pytest.fail("losing anchor creation must not derive a new identity"),
    )
    record = worktree_ops_cli._ensure_anchor_ledger(
        tracking.parse_claim_ref("receiving-space/receiving/@anchor"),
        cfg.tracking_dir("receiving"),
    )
    assert record.machine_identity is None
    assert record.yaml_path.read_bytes() == winners[0]


@pytest.mark.parametrize("mode", ["legacy", "tuple-v1"])
def test_ordinary_inference_does_not_upgrade_retained_external_record(
    adoption_config: cfg.Config, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    record = _produce(adoption_config, "external", mode=mode)
    before = _retain_history(record)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id_from_cwd", actual_infer)
    monkeypatch.setattr(
        creation_identity, "new_machine_identity",
        lambda *_a: pytest.fail("existing inference must not derive a new identity"),
    )
    monkeypatch.chdir(record.worktree_path)
    assert worktree_identity._infer_worktree_id_from_cwd(adoption_config) == record.worktree_id
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("defect", ["project", "namespace", "platform", "machine", "registry-anchor"])
def test_ordinary_anchor_conflicts_are_visible_and_no_record_is_created(
    receiving_anchor, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    config = receiving_anchor
    parsed = tracking.parse_claim_ref("receiving-space/receiving/@anchor")
    directory = cfg.tracking_dir("receiving")
    if defect == "project":
        monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: replace(
            config, repo_name="ambient", repos={"ambient": config.default_repo},
        ))
    elif defect == "namespace":
        directory = cfg.tracking_dir("ambient")
    elif defect == "platform":
        monkeypatch.setattr(cfg, "detect_platform", lambda: (
            "linux" if config.platform != "linux" else "windows"
        ))
    elif defect == "machine":
        parsed.machine = "foreign-space"
    else:
        monkeypatch.setattr(repos, "find_repo", lambda _name: SimpleNamespace(
            repo_class="singleton", local_path=lambda: str(cfg.project_dir("ambient") / "anchor"),
        ))
    with pytest.raises((execution_spaces.ExecutionSpaceError, ValueError), match=(
        "receiving|namespace|platform|execution|anchor"
    )):
        worktree_ops_cli._ensure_anchor_ledger(parsed, directory)
    assert not list(cfg.tracking_dir("receiving").glob("*.yaml"))
    assert not list(cfg.tracking_dir("ambient").glob("*.yaml"))
