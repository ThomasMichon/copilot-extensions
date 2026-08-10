"""Phase 4 tests: the `abandoned` disposition + the never-wedge reclaim sweep."""

from __future__ import annotations

import argparse
import json

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import obligations, tracking

# ── vocabulary ───────────────────────────────────────────────────────────────

def test_abandoned_in_dispositions():
    assert obligations.ABANDONED == "abandoned"
    assert "abandoned" in obligations.DISPOSITIONS


def test_abandoned_does_not_block_and_is_not_held():
    assert obligations.blocks_finalize("abandoned") is False
    assert obligations.is_held("abandoned") is False
    assert obligations.is_abandoned("abandoned") is True


def test_claim_state_parses_and_reports_abandoned():
    c = tracking.ResourceClaim(kind="worktree", ref="m/p/w", state="abandoned")
    assert c.is_abandoned and not c.is_unsettled and not c.is_live


def test_should_abandon_requires_definitive_gone_and_safe():
    assert obligations.should_abandon(gone=True, safe=True) is True
    for g, s in [(True, None), (None, True), (True, False), (False, True),
                 (None, None), (False, False)]:
        assert obligations.should_abandon(gone=g, safe=s) is False


# ── sweep core (injected resolvers) ──────────────────────────────────────────

def _rec(resources):
    return tracking.WorktreeRecord(
        worktree_id="wt-owner", branch="worktree/wt-owner",
        worktree_path="/x", repo="p", machine="m", platform="windows",
        started_at="t", last_resumed_at="t", resume_count=0, title=None,
        status="active", completed_at=None, resources=resources)


def test_sweep_abandons_only_gone_and_safe(tmp_path):
    claims = [
        tracking.ResourceClaim(kind="worktree", ref="m/p/gone-safe", state="active"),
        tracking.ResourceClaim(kind="worktree", ref="m/p/gone-unsafe", state="active"),
        tracking.ResourceClaim(kind="worktree", ref="m/p/live", state="active"),
        tracking.ResourceClaim(kind="worktree", ref="m/p/at-rest", state="at-rest"),
    ]
    rec = _rec(claims)
    gone = {"m/p/gone-safe": True, "m/p/gone-unsafe": True, "m/p/live": False}
    safe = {"m/p/gone-safe": True, "m/p/gone-unsafe": False, "m/p/live": None}
    flipped = tracking.sweep_abandoned_obligations(
        rec, gone_of=lambda r: gone.get(r), safe_of=lambda c: safe.get(c.ref),
        save=False)
    assert [c.ref for c in flipped] == ["m/p/gone-safe"]
    assert claims[0].state == "abandoned"
    assert claims[1].state == "active"   # gone but unsafe -> spared
    assert claims[2].state == "active"   # live -> spared
    assert claims[3].state == "at-rest"  # not active -> skipped entirely


def test_sweep_resolver_exception_is_spare(tmp_path):
    rec = _rec([tracking.ResourceClaim(kind="worktree", ref="m/p/x", state="active")])
    def boom(_):
        raise RuntimeError("probe blew up")
    flipped = tracking.sweep_abandoned_obligations(
        rec, gone_of=boom, safe_of=boom, save=False)
    assert flipped == [] and rec.resources[0].state == "active"


# ── _claims_sweep CLI (child-record resolution) ──────────────────────────────

def _seed_project(tmp_path, monkeypatch, machine="m", project="p"):
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr(cfg, "tracking_dir",
                        lambda: tmp_path / f".{project}" / "worktrees")
    monkeypatch.setattr(cfg, "load_config",
                        lambda *a, **k: __import__("types").SimpleNamespace(machine=machine))
    d = tmp_path / f".{project}" / "worktrees"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _child(tdir, wt_id, status):
    rec = tracking.create_new_record(
        wt_id, f"worktree/{wt_id}", str(tdir.parent / wt_id), "p", "m", "windows", tdir)
    rec.status = status
    tracking.save_record(rec, tdir / f"{wt_id}.yaml")


def _owner_with_claim(tdir, owner_id, child_ref, kind="worktree"):
    rec = tracking.create_new_record(
        owner_id, f"worktree/{owner_id}", str(tdir.parent / owner_id), "p", "m",
        "windows", tdir)
    tracking.add_resource_claim(
        rec, tracking.ResourceClaim(kind=kind, ref=child_ref,
                                    created_at=tracking._now_iso(), state="active"),
        save=False)
    tracking.save_record(rec, tdir / f"{owner_id}.yaml")


def _sweep_args(*, apply=False, json_=True):
    return argparse.Namespace(target=["sweep"], apply=apply, json=json_)


def test_cli_sweep_dry_run_reports_but_does_not_write(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _child(tdir, "wt-child", "finalized")
    _owner_with_claim(tdir, "wt-owner", "m/p/wt-child")
    rc = m.cmd_claims(_sweep_args(apply=False))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["applied"] is False and out["count"] == 1
    # dry-run: the owner's on-disk claim is still active.
    owner = tracking.load_record(tdir / "wt-owner.yaml")
    assert owner.resources[0].state == "active"


def test_cli_sweep_apply_abandons_finalized_child_claim(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _child(tdir, "wt-child", "finalized")
    _owner_with_claim(tdir, "wt-owner", "m/p/wt-child")
    rc = m.cmd_claims(_sweep_args(apply=True))
    assert rc == 0
    owner = tracking.load_record(tdir / "wt-owner.yaml")
    assert owner.resources[0].state == "abandoned"


def test_cli_sweep_spares_orphaned_and_active_children(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    _child(tdir, "wt-orphan", "orphaned")     # gone but unsafe
    _child(tdir, "wt-live", "active")         # live
    _owner_with_claim(tdir, "wt-o1", "m/p/wt-orphan")
    _owner_with_claim(tdir, "wt-o2", "m/p/wt-live")
    rc = m.cmd_claims(_sweep_args(apply=True))
    assert rc == 0
    assert tracking.load_record(tdir / "wt-o1.yaml").resources[0].state == "active"
    assert tracking.load_record(tdir / "wt-o2.yaml").resources[0].state == "active"


def test_cli_sweep_non_worktree_kind_is_spared(tmp_path, monkeypatch, capfd):
    tdir = _seed_project(tmp_path, monkeypatch)
    # A finalized "child" record exists, but the claim is a codespace kind ->
    # not provable within agent-worktrees -> spared.
    _child(tdir, "cs-x", "finalized")
    _owner_with_claim(tdir, "wt-o", "m/p/cs-x", kind="codespace")
    rc = m.cmd_claims(_sweep_args(apply=True))
    assert rc == 0
    assert tracking.load_record(tdir / "wt-o.yaml").resources[0].state == "active"
