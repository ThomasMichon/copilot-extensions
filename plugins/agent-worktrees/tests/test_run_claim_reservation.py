"""Creator-ownership reservation around `agent-worktrees run`."""

from __future__ import annotations

import argparse
import json
import types

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import state_root
from agent_worktrees import tracking


def _seed_owner(tmp_path, monkeypatch):
    project_dir = tmp_path / ".p"
    tracking_dir = project_dir / "worktrees"
    tracking_dir.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_dir)
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: types.SimpleNamespace(machine="m", repo_name="p"))
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda name: types.SimpleNamespace(machine="m", repo_name=name),
    )
    ready_root = state_root.StateRoot(
        str(tmp_path), "launch_repo", "p", False, False, True
    )
    monkeypatch.setattr(
        m.state_root_mod,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            True, "ready", ready_root
        ),
    )
    tracking.create_new_record(
        "owner", "worktree/owner", str(tmp_path / "owner"), "p",
        "m", "windows", tracking_dir)
    return tracking_dir / "owner.yaml"


def _args():
    return argparse.Namespace(
        inner_command=["resource-create"], owner_ref="m/p/owner")


def test_run_failure_retains_pending_ownership(tmp_path, monkeypatch):
    path = _seed_owner(tmp_path, monkeypatch)
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=5, stdout=""))
    assert m.cmd_run(_args()) == 5
    rec = tracking.load_record(path)
    assert len(rec.resources) == 1
    assert rec.resources[0].ref.startswith("pending-run:")


def test_run_success_replaces_pending_with_real_claim(
        tmp_path, monkeypatch):
    path = _seed_owner(tmp_path, monkeypatch)
    payload = json.dumps({
        "worktree": {"id": "child", "machine": "m", "repo": "p"}
    })
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload))
    assert m.cmd_run(_args()) == 0
    rec = tracking.load_record(path)
    assert [claim.ref for claim in rec.resources] == ["m/p/child"]


def test_run_refuses_cross_machine_owner_before_creation(
        tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    called = {"run": False}

    def _run(*args, **kwargs):
        called["run"] = True
        return types.SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr(m.subprocess, "run", _run)
    args = argparse.Namespace(
        inner_command=["resource-create"], owner_ref="other/p/owner")
    assert m.cmd_run(args) == 1
    assert called["run"] is False


def test_run_cross_machine_error_precedes_unready_ambient_config(
    tmp_path,
    monkeypatch,
):
    _seed_owner(tmp_path, monkeypatch)
    root = state_root.StateRoot(
        None,
        "knowledge_repo",
        "",
        True,
        True,
        False,
        error="no knowledge_repo is bound",
    )
    monkeypatch.setattr(
        m.state_root_mod,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            False,
            "knowledge_binding_required",
            root,
            error="bind the knowledge repository",
        ),
    )
    called = {"run": False}
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *a, **k: called.__setitem__("run", True),
    )

    args = argparse.Namespace(
        inner_command=["resource-create"], owner_ref="other/p/owner"
    )
    assert m.cmd_run(args) == 1
    assert called["run"] is False


def test_run_rejects_unready_owner_without_mutation_or_subprocess(
    tmp_path,
    monkeypatch,
):
    path = _seed_owner(tmp_path, monkeypatch)
    before = path.read_bytes()
    root = state_root.StateRoot(
        None,
        "knowledge_repo",
        "",
        True,
        True,
        False,
        error="no knowledge_repo is bound",
    )
    monkeypatch.setattr(
        m.state_root_mod,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            False,
            "knowledge_binding_required",
            root,
            error="bind the knowledge repository",
        ),
    )
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unready run launched a subprocess")
        ),
    )

    assert m.cmd_run(_args()) == 3
    assert path.read_bytes() == before


def test_owner_config_resolution_failure_is_structured_readiness(
    tmp_path,
    monkeypatch,
):
    _seed_owner(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cfg,
        "load_project_config",
        lambda name: (_ for _ in ()).throw(
            ValueError("No repo could be resolved")
        ),
    )
    config = types.SimpleNamespace(machine="m", repo_name="p")

    result = m._coordination_readiness_for_owner_ref(
        "m/p/owner", config
    )

    assert result.ready is False
    assert result.code == "state_root_resolution_failed"
    assert "No repo could be resolved" in result.error
