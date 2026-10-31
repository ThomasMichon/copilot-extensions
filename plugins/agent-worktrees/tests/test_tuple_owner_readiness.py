"""Qualified tuple owners retain their own project's real readiness gate."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import claims_cli, config as cfg, state_root, tracking, worktree_ops_cli
from agent_worktrees.state_root import resolve_state_root as actual_resolve_state_root
from test_launch_preflight import _create_args
from test_tuple_resolve_equivalence import observed_config as observed_config


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("operation", ["create", "run"])
@pytest.mark.parametrize("load_failure", [False, True])
def test_qualified_tuple_owner_readiness_cannot_borrow_ready_ambient_project(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    tmp_path: Path, platform: str, operation: str, load_failure: bool,
) -> None:
    config = observed_config(platform, "sibling-only", "ordinary")
    assert m.cmd_create(_create_args(json_output=True)) == 0
    row = json.loads(capfd.readouterr().out)["worktree"]
    path = cfg.tracking_dir() / f"{row['id']}.yaml"
    before = path.read_bytes()
    parent = tracking.load_record(path)
    owner_config = replace(config, repos={config.repo_name: replace(
        config.default_repo, stateless=True, requires_external_state_root=True,
    )})
    ambient_anchor = tmp_path / "ambient-anchor"
    ambient_anchor.mkdir()
    (ambient_anchor / "machines.yaml").write_text(
        (Path(config.default_repo.anchor) / "machines.yaml").read_text(),
    )
    ambient = replace(config, repo_name="ambient", repos={"ambient": replace(
        config.default_repo, anchor=str(ambient_anchor),
        worktree_root=str(tmp_path / "ambient-trees"),
    )})
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: ambient)
    loaded_projects = []

    def project_config(name, **kwargs):
        loaded_projects.append(name)
        assert name == config.repo_name
        if load_failure:
            raise ValueError("owner configuration unavailable")
        return owner_config

    monkeypatch.setattr(cfg, "load_project_config", project_config)
    monkeypatch.setattr(state_root, "resolve_state_root", actual_resolve_state_root)
    monkeypatch.setattr(state_root, "_git_toplevel", lambda *_a: None)
    assert state_root.coordination_readiness(ambient).ready
    assert not state_root.coordination_readiness(owner_config).ready
    if platform != "wsl":
        assert parent.execution_machine != ambient.machine
    owner_ref = f"{parent.execution_machine}/{config.repo_name}/{parent.worktree_id}"
    effects = []
    monkeypatch.setattr(worktree_ops_cli.subprocess, "run", lambda *_a, **_k: (
        effects.append("execute") or SimpleNamespace(
            returncode=0, stdout=json.dumps({"worktree": {**row, "id": "child"}}),
        )
    ))
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("allocate"))
    if operation == "run":
        code = worktree_ops_cli.cmd_run(SimpleNamespace(
            owner_ref=owner_ref, inner_command=["synthetic-producer"],
        ))
    else:
        args = _create_args(json_output=True)
        args.no_owner = False
        args.owner_ref = owner_ref
        code = m.cmd_create(args)
        result = json.loads(capfd.readouterr().out)
        assert result["code"] == (
            "state_root_resolution_failed" if load_failure else "knowledge_binding_required"
        )
    assert code == 3
    readiness = claims_cli._coordination_readiness_for_owner_ref(owner_ref, ambient)
    assert not readiness.ready
    assert readiness.code == (
        "state_root_resolution_failed" if load_failure else "knowledge_binding_required"
    )
    assert config.repo_name in loaded_projects
    assert effects == [] and path.read_bytes() == before
    assert list(cfg.tracking_dir().glob("*.yaml")) == [path]
    if operation == "run" and not load_failure:
        owner_config = replace(owner_config, repos={config.repo_name: replace(
            owner_config.default_repo, stateless=False, requires_external_state_root=False,
        )})
        assert claims_cli._coordination_readiness_for_owner_ref(owner_ref, ambient).ready
        assert worktree_ops_cli.cmd_run(SimpleNamespace(
            owner_ref=owner_ref, inner_command=["synthetic-producer"],
        )) == 0
        assert effects == ["execute"]
        assert [claim.ref for claim in tracking.load_record(path).resources] == [
            f"{parent.execution_machine}/{config.repo_name}/child",
        ]
