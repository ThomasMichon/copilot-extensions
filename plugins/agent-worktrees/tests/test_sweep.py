"""Tests for the never-wedge obligation-reclaim resolvers (sweep module)."""
from __future__ import annotations

import types

from agent_worktrees import sweep, tracking


def _rec(*states: str) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt-owner", branch="worktree/wt-owner",
        worktree_path="/x", repo="p", machine="m", platform="windows",
        started_at="t", last_resumed_at="t", resume_count=0, title=None,
        status="active", completed_at=None,
        resources=[tracking.ResourceClaim(kind="worktree", ref=f"m/p/c{i}", state=s)
                   for i, s in enumerate(states)],
    )


# ── gone_of ──────────────────────────────────────────────────────────────────

def test_gone_of_maps_claimant_liveness(monkeypatch):
    import agent_worktrees.claimant as claimant
    monkeypatch.setattr(claimant, "local_claimant_alive", lambda ref: False)
    assert sweep.gone_of("m/p/c") is True     # not alive -> gone
    monkeypatch.setattr(claimant, "local_claimant_alive", lambda ref: True)
    assert sweep.gone_of("m/p/c") is False    # alive -> present
    monkeypatch.setattr(claimant, "local_claimant_alive", lambda ref: None)
    assert sweep.gone_of("m/p/c") is None     # cross-machine/unknown -> spare


# ── safe_of ──────────────────────────────────────────────────────────────────

def test_safe_of_non_worktree_kind_is_spare():
    claim = tracking.ResourceClaim(kind="codespace", ref="m/p/cs", state="active")
    assert sweep.safe_of(claim, types.SimpleNamespace()) is None


def test_safe_of_finalized_child_is_safe(monkeypatch):
    monkeypatch.setattr(sweep, "load_claim_child_record",
                        lambda ref, config: (types.SimpleNamespace(status="finalized"), True))
    claim = tracking.ResourceClaim(kind="worktree", ref="m/p/c", state="active")
    assert sweep.safe_of(claim, types.SimpleNamespace()) is True


def test_safe_of_orphaned_child_is_unsafe(monkeypatch):
    monkeypatch.setattr(sweep, "load_claim_child_record",
                        lambda ref, config: (types.SimpleNamespace(status="orphaned"), True))
    claim = tracking.ResourceClaim(kind="worktree", ref="m/p/c", state="active")
    assert sweep.safe_of(claim, types.SimpleNamespace()) is False


def test_safe_of_active_child_defers_to_branch_check(monkeypatch):
    monkeypatch.setattr(sweep, "load_claim_child_record",
                        lambda ref, config: (types.SimpleNamespace(status="active"), True))
    monkeypatch.setattr(sweep, "child_branch_merged", lambda child, ref, config: True)
    claim = tracking.ResourceClaim(kind="worktree", ref="m/p/c", state="active")
    assert sweep.safe_of(claim, types.SimpleNamespace()) is True


# ── make_resolvers / self_heal ───────────────────────────────────────────────

def test_make_resolvers_binds_config(monkeypatch):
    seen = {}
    monkeypatch.setattr(sweep, "safe_of", lambda claim, config: seen.setdefault("cfg", config))
    conf = types.SimpleNamespace(tag="C")
    g, s = sweep.make_resolvers(conf)
    assert g is sweep.gone_of
    s(tracking.ResourceClaim(kind="worktree", ref="m/p/c", state="active"))
    assert seen["cfg"] is conf


def test_self_heal_abandons_only_gone_and_safe(monkeypatch):
    rec = _rec("active", "active", "at-rest")
    verdicts = {"m/p/c0": (True, True), "m/p/c1": (True, None)}
    monkeypatch.setattr(sweep, "gone_of", lambda ref: verdicts.get(ref, (None, None))[0])
    monkeypatch.setattr(sweep, "safe_of",
                        lambda claim, config: verdicts.get(claim.ref, (None, None))[1])
    healed = sweep.self_heal(rec, types.SimpleNamespace(), save=False)
    assert [c.ref for c in healed] == ["m/p/c0"]
    assert rec.resources[0].state == "abandoned"   # gone + safe
    assert rec.resources[1].state == "active"      # gone but unproven -> spared
    assert rec.resources[2].state == "at-rest"     # not active -> skipped


def test_self_heal_noop_when_nothing_qualifies(monkeypatch):
    rec = _rec("active")
    monkeypatch.setattr(sweep, "gone_of", lambda ref: None)
    monkeypatch.setattr(sweep, "safe_of", lambda claim, config: True)
    assert sweep.self_heal(rec, types.SimpleNamespace(), save=False) == []
    assert rec.resources[0].state == "active"
