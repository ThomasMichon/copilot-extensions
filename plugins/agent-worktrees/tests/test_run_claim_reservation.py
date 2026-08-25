"""Creator-ownership reservation around `agent-worktrees run`."""

from __future__ import annotations

import argparse
import json
import types

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import tracking


def _seed_owner(tmp_path, monkeypatch):
    project_dir = tmp_path / ".p"
    tracking_dir = project_dir / "worktrees"
    tracking_dir.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_dir)
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: types.SimpleNamespace(machine="m", repo_name="p"))
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
