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


# ── pr-kind reclaim via a GitHub merge check (pr-claim-accountability Ph1) ────

def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_github_pr_view_args_recognizes_forms():
    assert sweep._github_pr_view_args(
        "https://github.com/o/r/pull/12") == ["https://github.com/o/r/pull/12"]
    assert sweep._github_pr_view_args("o/r#34") == ["34", "--repo", "o/r"]
    # ADO url, bare number, junk -> None (the GitHub recognizer; ADO is matched
    # separately by _ado_pr_view_args).
    assert sweep._github_pr_view_args(
        "https://my-org.visualstudio.com/Example-Web/_git/example-web/pullrequest/9") is None
    assert sweep._github_pr_view_args("123") is None
    assert sweep._github_pr_view_args("") is None


def test_pr_merged_true_only_on_merged(monkeypatch):
    monkeypatch.setattr(sweep.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda *a, **k: _proc(0, '{"state": "MERGED"}'))
    assert sweep.pr_merged("o/r#1") is True
    for state in ("OPEN", "CLOSED"):
        monkeypatch.setattr(sweep.subprocess, "run",
                            lambda *a, s=state, **k: _proc(0, f'{{"state": "{s}"}}'))
        assert sweep.pr_merged("o/r#1") is None


def test_pr_merged_degrades_to_none(monkeypatch):
    # unrecognized ref (bare number / junk) -> never shells out
    monkeypatch.setattr(sweep.shutil, "which",
                        lambda _: (_ for _ in ()).throw(AssertionError("no shell")))
    assert sweep.pr_merged("123") is None
    assert sweep.pr_merged("not-a-ref") is None
    # gh missing
    monkeypatch.setattr(sweep.shutil, "which", lambda _: None)
    assert sweep.pr_merged("o/r#1") is None
    # gh error (e.g. wrong account / not visible) -> None
    monkeypatch.setattr(sweep.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda *a, **k: _proc(1, stderr="could not resolve"))
    assert sweep.pr_merged("o/r#1") is None
    # unparseable json -> None
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda *a, **k: _proc(0, "not json"))
    assert sweep.pr_merged("o/r#1") is None


def test_ado_pr_view_args_recognizes_forms():
    # classic <org>.visualstudio.com -> org = https://<host>/
    assert sweep._ado_pr_view_args(
        "https://my-org.visualstudio.com/Example-Web/_git/example-web/pullrequest/2285417"
    ) == ["--id", "2285417", "--org", "https://my-org.visualstudio.com/"]
    # modern dev.azure.com/<org> -> org = https://dev.azure.com/<org>/
    assert sweep._ado_pr_view_args(
        "https://dev.azure.com/my-org/Example-Web/_git/example-web/pullrequest/2285417"
    ) == ["--id", "2285417", "--org", "https://dev.azure.com/my-org/"]
    # GitHub refs, bare number, junk -> None
    assert sweep._ado_pr_view_args("https://github.com/o/r/pull/12") is None
    assert sweep._ado_pr_view_args("o/r#34") is None
    assert sweep._ado_pr_view_args("123") is None
    assert sweep._ado_pr_view_args("") is None


def test_ado_pr_merged_true_only_on_completed(monkeypatch):
    ado = "https://my-org.visualstudio.com/P/_git/r/pullrequest/9"
    monkeypatch.setattr(sweep.shutil, "which", lambda _: "az")
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda *a, **k: _proc(0, "completed\n"))
    assert sweep._ado_pr_merged(ado) is True
    assert sweep.pr_merged(ado) is True  # routes through the dispatcher too
    for status in ("active", "abandoned", "notSet"):
        monkeypatch.setattr(sweep.subprocess, "run",
                            lambda *a, s=status, **k: _proc(0, f"{s}\n"))
        assert sweep._ado_pr_merged(ado) is None


def test_ado_pr_merged_degrades_to_none(monkeypatch):
    ado = "https://dev.azure.com/o/p/_git/r/pullrequest/5"
    # non-ADO ref -> never shells out
    monkeypatch.setattr(sweep.shutil, "which",
                        lambda _: (_ for _ in ()).throw(AssertionError("no az")))
    assert sweep._ado_pr_merged("o/r#1") is None
    # az missing (no azure-devops CLI)
    monkeypatch.setattr(sweep.shutil, "which", lambda _: None)
    assert sweep._ado_pr_merged(ado) is None
    # az error (unauthenticated / not visible) -> None
    monkeypatch.setattr(sweep.shutil, "which", lambda _: "az")
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda *a, **k: _proc(1, stderr="not authenticated"))
    assert sweep._ado_pr_merged(ado) is None
    # empty / whitespace output -> None
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda *a, **k: _proc(0, "\n"))
    assert sweep._ado_pr_merged(ado) is None


def test_pr_merged_dispatches_github_vs_ado(monkeypatch):
    calls = []
    monkeypatch.setattr(sweep, "_github_pr_merged",
                        lambda ref: calls.append(("gh", ref)) or True)
    monkeypatch.setattr(sweep, "_ado_pr_merged",
                        lambda ref: calls.append(("ado", ref)) or True)
    assert sweep.pr_merged("o/r#7") is True
    assert sweep.pr_merged(
        "https://my-org.visualstudio.com/P/_git/r/pullrequest/8") is True
    assert sweep.pr_merged("123") is None  # unrecognized -> neither backend
    assert [c[0] for c in calls] == ["gh", "ado"]


def test_claim_gone_safe_route_pr_to_merge_check(monkeypatch):
    monkeypatch.setattr(sweep, "pr_merged", lambda ref: True)
    c = tracking.ResourceClaim(kind="pr", ref="o/r#7", state="active")
    assert sweep.claim_gone(c, types.SimpleNamespace()) is True
    assert sweep.claim_safe(c, types.SimpleNamespace()) is True
    monkeypatch.setattr(sweep, "pr_merged", lambda ref: None)
    assert sweep.claim_gone(c, types.SimpleNamespace()) is None
    assert sweep.claim_safe(c, types.SimpleNamespace()) is None


def test_sweep_reclaims_merged_pr_via_make_resolvers(monkeypatch):
    rec = tracking.WorktreeRecord(
        worktree_id="wt", branch="worktree/wt", worktree_path="/x", repo="p",
        machine="m", platform="windows", started_at="t", last_resumed_at="t",
        resume_count=0, title=None, status="active", completed_at=None,
        resources=[
            tracking.ResourceClaim(kind="pr", ref="o/r#merged", state="active"),
            tracking.ResourceClaim(kind="pr", ref="o/r#open", state="active"),
        ],
    )
    merged = {"o/r#merged": True, "o/r#open": None}
    monkeypatch.setattr(sweep, "pr_merged", lambda ref: merged.get(ref))
    g, s = sweep.make_resolvers(types.SimpleNamespace())
    flipped = tracking.sweep_abandoned_obligations(rec, gone_of=g, safe_of=s, save=False)
    assert [c.ref for c in flipped] == ["o/r#merged"]
    assert rec.resources[0].state == "abandoned"   # merged -> reclaimed
    assert rec.resources[1].state == "active"      # open -> spared (still owed)


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
