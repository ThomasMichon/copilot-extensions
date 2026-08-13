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
    monkeypatch.setattr(sweep, "gone_of", lambda ref: seen.setdefault("gone_ref", ref) or None)
    conf = types.SimpleNamespace(tag="C")
    g, s = sweep.make_resolvers(conf)
    wt = tracking.ResourceClaim(kind="worktree", ref="m/p/c", state="active")
    # Both resolvers take the CLAIM now and route a worktree kind to gone_of/safe_of.
    g(wt)
    s(wt)
    assert seen["cfg"] is conf and seen["gone_ref"] == "m/p/c"


# ── leaseable-kind reclaim via the lease disposition mirror (item 2) ──────────

def test_lease_disposition_reads_context(monkeypatch):
    from agent_worktrees import lease_config, lease_store
    snap = types.SimpleNamespace(
        record=types.SimpleNamespace(context={"disposition": "at-rest"}))
    monkeypatch.setattr(lease_config, "load_lease_settings", lambda: object())
    monkeypatch.setattr(lease_store, "GitLeaseStore",
                        lambda s: types.SimpleNamespace(inspect=lambda kind, ref: snap))
    assert sweep.lease_disposition_of("codespace", "box", types.SimpleNamespace()) == "at-rest"


def test_lease_disposition_absent_or_error_is_none(monkeypatch):
    from agent_worktrees import lease_config, lease_store
    # Absent lease -> None.
    monkeypatch.setattr(lease_config, "load_lease_settings", lambda: object())
    monkeypatch.setattr(lease_store, "GitLeaseStore",
                        lambda s: types.SimpleNamespace(inspect=lambda kind, ref: None))
    assert sweep.lease_disposition_of("codespace", "box", types.SimpleNamespace()) is None
    # Unconfigured store (raises) -> None (degrade-safe).
    def _raise():
        raise RuntimeError("no store configured")
    monkeypatch.setattr(lease_config, "load_lease_settings", _raise)
    assert sweep.lease_disposition_of("codespace", "box", types.SimpleNamespace()) is None


def test_leaseable_settled_only_on_settled_disposition(monkeypatch):
    claim = tracking.ResourceClaim(kind="codespace", ref="box", state="active")
    for disp in ("at-rest", "released", "abandoned"):
        monkeypatch.setattr(sweep, "lease_disposition_of", lambda k, r, c, d=disp: d)
        assert sweep.leaseable_settled(claim, types.SimpleNamespace()) is True
    for disp in ("active", None):
        monkeypatch.setattr(sweep, "lease_disposition_of", lambda k, r, c, d=disp: d)
        assert sweep.leaseable_settled(claim, types.SimpleNamespace()) is None


def test_claim_gone_and_safe_route_codespace_to_lease_mirror(monkeypatch):
    monkeypatch.setattr(sweep, "leaseable_settled", lambda claim, config: True)
    cs = tracking.ResourceClaim(kind="codespace", ref="box", state="active")
    assert sweep.claim_gone(cs, types.SimpleNamespace()) is True
    assert sweep.claim_safe(cs, types.SimpleNamespace()) is True


def test_claim_gone_safe_unknown_kind_is_spare():
    other = tracking.ResourceClaim(kind="bridge", ref="s", state="active")
    assert sweep.claim_gone(other, types.SimpleNamespace()) is None
    assert sweep.claim_safe(other, types.SimpleNamespace()) is None


def test_sweep_reclaims_at_rest_codespace_via_mirror(monkeypatch):
    # End-to-end through make_resolvers: an active codespace claim whose lease
    # mirror shows at-rest is abandoned; an active one with no mirror is spared.
    rec = tracking.WorktreeRecord(
        worktree_id="wt", branch="worktree/wt", worktree_path="/x", repo="p",
        machine="m", platform="windows", started_at="t", last_resumed_at="t",
        resume_count=0, title=None, status="active", completed_at=None,
        resources=[
            tracking.ResourceClaim(kind="codespace", ref="settled-box", state="active"),
            tracking.ResourceClaim(kind="codespace", ref="active-box", state="active"),
        ],
    )
    dispositions = {"settled-box": "at-rest", "active-box": None}
    monkeypatch.setattr(sweep, "lease_disposition_of",
                        lambda kind, ref, config: dispositions.get(ref))
    g, s = sweep.make_resolvers(types.SimpleNamespace())
    flipped = tracking.sweep_abandoned_obligations(rec, gone_of=g, safe_of=s, save=False)
    assert [c.ref for c in flipped] == ["settled-box"]
    assert rec.resources[0].state == "abandoned"
    assert rec.resources[1].state == "active"


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
