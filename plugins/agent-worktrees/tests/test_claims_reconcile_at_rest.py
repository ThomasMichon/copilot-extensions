"""Tests for `agent-worktrees claims reconcile-at-rest` (worktree-finality-
and-obligations Phase 4 / design.md's dedicated legacy/GC close-out
reconciliation command)."""

from __future__ import annotations

import argparse
import json

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import tracking


def _seed_project(tmp_path, monkeypatch, machine="m", project="p"):
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr(cfg, "tracking_dir",
                        lambda: tmp_path / f".{project}" / "worktrees")
    monkeypatch.setattr(cfg, "load_config",
                        lambda *a, **k: __import__("types").SimpleNamespace(machine=machine))
    d = tmp_path / f".{project}" / "worktrees"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _owner(tdir, owner_id, resources):
    rec = tracking.create_new_record(
        owner_id, f"worktree/{owner_id}", str(tdir.parent / owner_id), "p", "m",
        "windows", tdir)
    for claim in resources:
        tracking.add_resource_claim(rec, claim, save=False)
    tracking.save_record(rec, tdir / f"{owner_id}.yaml")


def _args(*, selectors=(), apply=False, json_=True):
    return argparse.Namespace(
        target=["reconcile-at-rest", *selectors], apply=apply, json=json_)


def test_dry_run_reports_but_does_not_write(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _owner(tdir, "wt-owner", [
        tracking.ResourceClaim(kind="codespace", ref="cs-1", state="at-rest"),
        tracking.ResourceClaim(kind="worktree", ref="m/p/wt-c", state="active"),
    ])
    rc = m.cmd_claims(_args(apply=False))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["applied"] is False
    assert out["released"] == [{"owner": "wt-owner", "kind": "codespace", "ref": "cs-1"}]
    reloaded = tracking.load_record(tdir / "wt-owner.yaml")
    by_ref = {c.ref: c.state for c in reloaded.resources}
    assert by_ref["cs-1"] == "at-rest"      # dry-run: unchanged on disk
    assert by_ref["m/p/wt-c"] == "active"


def test_apply_releases_at_rest_never_active(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _owner(tdir, "wt-owner", [
        tracking.ResourceClaim(kind="codespace", ref="cs-1", state="at-rest"),
        tracking.ResourceClaim(kind="worktree", ref="m/p/wt-c", state="active"),
    ])
    rc = m.cmd_claims(_args(apply=True))
    assert rc == 0
    reloaded = tracking.load_record(tdir / "wt-owner.yaml")
    by_ref = {c.ref: c.state for c in reloaded.resources}
    assert by_ref["cs-1"] == "released"
    assert by_ref["m/p/wt-c"] == "active"   # never touched


def test_selector_narrows_to_named_worktrees(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _owner(tdir, "wt-a", [tracking.ResourceClaim(kind="codespace", ref="cs-a", state="at-rest")])
    _owner(tdir, "wt-b", [tracking.ResourceClaim(kind="codespace", ref="cs-b", state="at-rest")])
    rc = m.cmd_claims(_args(selectors=["wt-a"], apply=True))
    assert rc == 0
    assert tracking.load_record(tdir / "wt-a.yaml").resources[0].state == "released"
    assert tracking.load_record(tdir / "wt-b.yaml").resources[0].state == "at-rest"


def test_no_at_rest_claims_reports_empty(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _owner(tdir, "wt-owner", [
        tracking.ResourceClaim(kind="worktree", ref="m/p/wt-c", state="active"),
    ])
    rc = m.cmd_claims(_args(apply=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["applied"] is True
    assert out["released"] == []
    assert out["count"] == 0


def test_human_readable_output_dry_run_and_apply(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _owner(tdir, "wt-owner", [
        tracking.ResourceClaim(kind="codespace", ref="cs-1", state="at-rest"),
    ])
    rc = m.cmd_claims(_args(apply=False, json_=False))
    assert rc == 0
    printed = capfd.readouterr().out
    assert "Would release" in printed and "wt-owner: codespace cs-1" in printed
