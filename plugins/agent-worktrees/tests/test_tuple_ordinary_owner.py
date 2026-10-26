"""Ordinary nested creation retains execution owners rather than detected scalars."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg, launch_environment, resolve_cli, tracking
from agent_worktrees import claims_cli, worktree_ops_cli
from test_launch_preflight import _create_args
from test_resolve_seed_delivery import _args
from test_tuple_resolve_equivalence import observed_config as observed_config


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_invalid_tuple_component_still_returns_json(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str,
) -> None:
    config = observed_config(platform, "matching", "ordinary")
    topology = Path(config.default_repo.anchor) / "machines.yaml"
    topology.write_text(topology.read_text().replace("friendly-host", "rack..desk"))
    effects = []
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    code = resolve_cli.cmd_resolve(_args(new_worktree=True, worktree_id=None))
    result = json.loads(capfd.readouterr().out)
    assert code != 0 and "portable" in result["error"]
    assert effects == [] and not list(cfg.tracking_dir().glob("*.yaml"))


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind", ["absent", "matching", "unscoped", "sibling-only"])
@pytest.mark.parametrize("owner_source", ["cwd", "environment", "run"])
def test_ordinary_nested_creation_uses_retained_parent_execution_owner(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, map_kind: str, owner_source: str,
) -> None:
    config = observed_config(platform, map_kind, "ordinary")
    assert m.cmd_create(_create_args(json_output=True)) == 0
    parent_row = json.loads(capfd.readouterr().out)["worktree"]
    parent = tracking.load_record(cfg.tracking_dir() / f"{parent_row['id']}.yaml")
    original_identity, original_nonce = parent.machine_fields, parent.creation_nonce
    monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda *_a: parent.worktree_id)
    monkeypatch.setattr(m, "_worktree_id_from_git", lambda *_a: parent.worktree_id)
    monkeypatch.setattr(
        m, "_coordination_readiness_for_owner_ref", lambda *_a: SimpleNamespace(ready=True),
    )
    monkeypatch.setattr(
        claims_cli, "_coordination_readiness_for_owner_ref", lambda *_a: SimpleNamespace(ready=True),
    )
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    owner_ref = f"{parent.execution_machine}/{config.repo_name}/{parent.worktree_id}"
    if owner_source == "environment":
        env = launch_environment._build_env(None, None, work_dir=parent.worktree_path, config=config)
        assert env["AGENT_WORKTREES_OWNER_REF"] == owner_ref
        monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", env["AGENT_WORKTREES_OWNER_REF"])
    if owner_source == "run":
        produced = {"worktree": {
            **parent_row, "id": "child", "path": str(Path(parent.worktree_path).parent / "child"),
        }}
        monkeypatch.setattr(worktree_ops_cli.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
            stdout=json.dumps(produced), returncode=0,
        ))
        assert worktree_ops_cli.cmd_run(SimpleNamespace(
            inner_command=["synthetic-producer"], owner_ref=None,
        )) == 0
        child_id = "child"
    else:
        args = _create_args(json_output=True)
        args.no_owner = False
        code = m.cmd_create(args)
        result = json.loads(capfd.readouterr().out)
        assert code == 0, result
        child = tracking.load_record(cfg.tracking_dir() / f"{result['worktree']['id']}.yaml")
        assert child.owner_ref == owner_ref
        assert child.execution_machine == parent.execution_machine
        child_id = child.worktree_id
    saved = tracking.load_record(parent.yaml_path)
    assert saved.machine_fields == original_identity and saved.creation_nonce == original_nonce
    assert [claim.ref for claim in saved.resources] == [
        f"{parent.execution_machine}/{config.repo_name}/{child_id}",
    ]


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("owner_source", ["explicit", "environment", "run"])
def test_explicit_foreign_owner_is_never_replaced_by_local_inference(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, owner_source: str,
) -> None:
    config = observed_config(platform, "sibling-only", "ordinary")
    assert m.cmd_create(_create_args(json_output=True)) == 0
    row = json.loads(capfd.readouterr().out)["worktree"]
    path = cfg.tracking_dir() / f"{row['id']}.yaml"
    before = path.read_bytes()
    foreign = f"sibling-space/{config.repo_name}/{row['id']}"
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("foreign owner must not allocate"),
    )
    monkeypatch.setattr(
        worktree_ops_cli.subprocess, "run",
        lambda *_a, **_k: pytest.fail("foreign owner must not execute"),
    )
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    if owner_source == "run":
        code = worktree_ops_cli.cmd_run(SimpleNamespace(
            inner_command=["synthetic-producer"], owner_ref=foreign,
        ))
    else:
        args = _create_args(json_output=True)
        args.no_owner = False
        if owner_source == "explicit":
            args.owner_ref = foreign
        else:
            monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", foreign)
        code = m.cmd_create(args)
        assert json.loads(capfd.readouterr().out)["error"]
    assert code != 0
    assert path.read_bytes() == before
    assert list(cfg.tracking_dir().glob("*.yaml")) == [path]


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_implicit_legacy_reference_is_not_relabelled_to_admit_a_tuple_child(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str,
) -> None:
    config = observed_config(platform, "sibling-only", "ordinary")
    row = m._create_worktree_core(config, build_launch_plan=False)["worktree"]
    path = cfg.tracking_dir() / f"{row['id']}.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda *_a: row["id"])
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    expected = f"{config.machine}/{config.repo_name}/{row['id']}"
    assert worktree_ops_cli._resolve_owner_ref() == expected
    args = _create_args(json_output=True)
    args.no_owner = False
    capfd.readouterr()
    assert m.cmd_create(args) != 0
    assert "physical-host metadata" in json.loads(capfd.readouterr().out)["error"]
    assert path.read_bytes() == before
