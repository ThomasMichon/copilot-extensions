"""Tests for `agent-worktrees claims fleet-audit` (worktree-finality-and-
obligations Phase 6): read-only inventory across legacy boolean follow-ups,
active effort bindings, at-rest claims, and finalized records whose current
evidence is no longer final."""

from __future__ import annotations

import argparse
import json

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import effort_focus, tracking


def _seed_project(tmp_path, monkeypatch, machine="m", project="p"):
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr(cfg, "tracking_dir",
                        lambda: tmp_path / f".{project}" / "worktrees")
    monkeypatch.setattr(cfg, "load_config",
                        lambda *a, **k: __import__("types").SimpleNamespace(machine=machine))
    d = tmp_path / f".{project}" / "worktrees"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rec(tdir, wt_id, **overrides):
    rec = tracking.create_new_record(
        wt_id, f"worktree/{wt_id}", str(tdir.parent / wt_id), "p", "m",
        "windows", tdir)
    for k, v in overrides.items():
        setattr(rec, k, v)
    tracking.save_record(rec, tdir / f"{wt_id}.yaml")
    return rec


def _args(*, json_=True):
    return argparse.Namespace(target=["fleet-audit"], json=json_)


def test_fleet_audit_surfaces_all_four_categories(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    # 1. Legacy boolean follow-up: flagged, no itemized entries.
    _rec(tdir, "wt-legacy-fu", follow_up=True)
    # 2. Active effort binding.
    _rec(tdir, "wt-effort", active_effort=effort_focus.make_active_effort(
        "efforts/active/some-effort/README.md", "worker", "Phase 1"))
    # 3. At-rest claim.
    rec_claims = _rec(tdir, "wt-at-rest")
    tracking.add_resource_claim(
        rec_claims, tracking.ResourceClaim(kind="codespace", ref="cs-1", state="at-rest"),
        save=False)
    tracking.save_record(rec_claims, tdir / "wt-at-rest.yaml")
    # 4. Finalized record whose worktree directory is gone -- still counts
    # as FINAL (git-COMPLETED fallback), so it must NOT show up as stale.
    _rec(tdir, "wt-finalized-clean", status="finalized")
    # 5. Finalized record that gained a held claim afterward -- Phase 1's
    # reopen fix would normally flip status back to "active" the moment
    # `add_resource_claim` runs; simulate the pre-Phase-1 / hand-edited-YAML
    # case this bullet is actually meant to catch by writing the claim
    # directly onto the resources list without going through that helper.
    stale = _rec(tdir, "wt-stale-finalized", status="finalized")
    stale.resources.append(
        tracking.ResourceClaim(kind="codespace", ref="cs-2", state="active"))
    tracking.save_record(stale, tdir / "wt-stale-finalized.yaml")

    rc = m.cmd_claims(_args())
    assert rc == 0
    out = json.loads(capfd.readouterr().out)

    assert out["legacy_follow_ups"] == ["wt-legacy-fu"]
    assert out["active_effort_bindings"] == [{
        "worktree_id": "wt-effort",
        "path": "efforts/active/some-effort/README.md",
    }]
    assert out["at_rest_claims"] == [{
        "worktree_id": "wt-at-rest", "kind": "codespace", "ref": "cs-1",
    }]
    stale_ids = {s["worktree_id"] for s in out["stale_finalized"]}
    assert stale_ids == {"wt-stale-finalized"}
    assert "wt-finalized-clean" not in stale_ids


def test_fleet_audit_human_readable_output(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _rec(tdir, "wt-legacy-fu", follow_up=True)
    rc = m.cmd_claims(_args(json_=False))
    assert rc == 0
    printed = capfd.readouterr().out
    assert "legacy boolean follow-ups: 1" in printed
    assert "wt-legacy-fu" in printed


def test_fleet_audit_clean_fleet_reports_all_zero(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _rec(tdir, "wt-plain")
    rc = m.cmd_claims(_args())
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["legacy_follow_ups"] == []
    assert out["active_effort_bindings"] == []
    assert out["at_rest_claims"] == []
    assert out["stale_finalized"] == []
