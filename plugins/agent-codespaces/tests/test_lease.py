"""Tests for the CodeSpace lease broker (state redirected to tmp)."""

from __future__ import annotations

import pytest

from agent_codespaces import lease as lease_mod


@pytest.fixture
def leases(monkeypatch, tmp_path):
    """Redirect lease state to a tmp dir so tests never touch real state."""
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    # ensure_runtime_dir() targets the real RUNTIME_DIR; stub it to a no-op so
    # the broker only writes under tmp_path.
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)
    return tmp_path


def test_borrow_records_lease(leases):
    lease = lease_mod.borrow("effort-a", "cs-one")
    assert lease.codespace == "cs-one"
    assert lease.effort == "effort-a"
    assert lease_mod.get_lease("cs-one").effort == "effort-a"


def test_lease_token_for_returns_held_token(leases):
    import time
    now = time.time()
    lease_mod._write_leases({
        "cs-one": lease_mod.Lease(
            codespace="cs-one", effort="e", pid=1, host="h",
            acquired_at=now, heartbeat_at=now, lease_token="tok-" + "a" * 40),
    })
    assert lease_mod.lease_token_for("cs-one") == "tok-" + "a" * 40


def test_lease_token_for_absent_or_untokened_is_none(leases):
    import time
    now = time.time()
    assert lease_mod.lease_token_for("cs-none") is None
    lease_mod._write_leases({
        "cs-two": lease_mod.Lease(
            codespace="cs-two", effort="e", pid=1, host="h",
            acquired_at=now, heartbeat_at=now, lease_token=""),
    })
    assert lease_mod.lease_token_for("cs-two") is None


def test_borrow_conflict_raises(leases):
    lease_mod.borrow("effort-a", "cs-one")
    with pytest.raises(RuntimeError, match="leased by effort 'effort-a'"):
        lease_mod.borrow("effort-b", "cs-one")


def test_borrow_force_takes_over(leases):
    lease_mod.borrow("effort-a", "cs-one")
    lease = lease_mod.borrow("effort-b", "cs-one", force=True)
    assert lease.effort == "effort-b"
    assert lease_mod.get_lease("cs-one").effort == "effort-b"


def test_borrow_same_effort_idempotent(leases):
    first = lease_mod.borrow("effort-a", "cs-one")
    second = lease_mod.borrow("effort-a", "cs-one")
    assert second.codespace == "cs-one"
    # acquired_at preserved across re-borrow by the same effort
    assert second.acquired_at == first.acquired_at


def test_force_takeover_resets_acquired_at(leases):
    first = lease_mod.borrow("effort-a", "cs-one")
    taken = lease_mod.borrow("effort-b", "cs-one", force=True)
    # A new effort's forced takeover starts a fresh acquisition.
    assert taken.acquired_at >= first.acquired_at
    assert taken.effort == "effort-b"


def test_borrow_requires_name(leases):
    with pytest.raises(RuntimeError, match="requires a CodeSpace name"):
        lease_mod.borrow("effort-a", "")


def test_release_by_codespace(leases):
    lease_mod.borrow("effort-a", "cs-one")
    assert lease_mod.release("cs-one") is True
    assert lease_mod.list_leases() == []


def test_release_by_effort(leases):
    lease_mod.borrow("effort-a", "cs-one")
    assert lease_mod.release("effort-a") is True
    assert lease_mod.get_lease("cs-one") is None


def test_release_missing_returns_false(leases):
    assert lease_mod.release("nope") is False


def test_heartbeat_refreshes(leases):
    lease_mod.borrow("effort-a", "cs-one")
    assert lease_mod.heartbeat("cs-one") is True
    assert lease_mod.heartbeat("cs-absent") is False


def test_multiple_codespaces_independent(leases):
    lease_mod.borrow("effort-a", "cs-one")
    lease_mod.borrow("effort-b", "cs-two")
    active = {le.codespace: le.effort for le in lease_mod.list_leases()}
    assert active == {"cs-one": "effort-a", "cs-two": "effort-b"}


def test_reclaim_after_ttl(leases):
    lease_mod.borrow("effort-a", "cs-one")
    # A negative TTL means any non-negative age is past expiry -- deterministic
    # regardless of clock resolution.
    assert lease_mod.list_leases(ttl=-1) == []


def test_lease_survives_within_ttl(leases):
    lease_mod.borrow("effort-a", "cs-one")
    active = lease_mod.list_leases()
    assert len(active) == 1
    assert active[0].effort == "effort-a"


# -- Exclusive worktree-keyed claims (#897) -----------------------------------


def test_claim_records_worktree_owner(leases):
    cl = lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})
    assert cl.codespace == "cs-one"
    assert cl.worktree == "/wt/a"
    assert cl.effort == ""  # owner is a lock-holder, not an effort (untangled)


def test_claim_same_owner_idempotent(leases):
    first = lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})
    second = lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})
    assert second.acquired_at == first.acquired_at  # preserved on refresh


def test_claim_live_different_owner_bounces(leases):
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a", "/wt/b"})
    with pytest.raises(lease_mod.ClaimConflict) as ei:
        lease_mod.claim("cs-one", "/wt/b", active={"/wt/a", "/wt/b"})
    assert ei.value.holder == "/wt/a"
    assert ei.value.codespace == "cs-one"


def test_claim_force_takes_over_live_owner(leases):
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a", "/wt/b"})
    cl = lease_mod.claim("cs-one", "/wt/b", force=True, active={"/wt/a", "/wt/b"})
    assert cl.worktree == "/wt/b"


def test_claim_auto_releases_dead_owner(leases):
    # /wt/a holds it but is absent from the active set and its path doesn't
    # exist -> positively dead -> /wt/b takes over WITHOUT --force.
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})
    cl = lease_mod.claim("cs-one", "/wt/b", active={"/wt/b"})
    assert cl.worktree == "/wt/b"


def test_claim_live_by_path_existence_bounces(leases, tmp_path):
    # active set unavailable (None): a holder whose path EXISTS is treated live
    # (path-existence fallback), so a different owner is bounced.
    wt = tmp_path / "wt-a"
    wt.mkdir()
    lease_mod.claim("cs-one", str(wt), active=None)
    with pytest.raises(lease_mod.ClaimConflict):
        lease_mod.claim("cs-one", str(tmp_path / "wt-b"), active=None)


# ── #1362: self-owned L2 conflict must adopt (not bounce), and a genuine
#           conflict must report the real local holder (not '(cross-machine)') ──

def _l2_conflict(holder):
    from agent_codespaces import coordination
    return lambda cs, holder_ref, **k: coordination.L2Result(
        "conflict", holder=holder)


def test_claim_self_conflict_same_owner_adopts(leases, monkeypatch):
    from agent_codespaces import coordination
    # A same-owner L1 record with NO L2 token (the #1362 trigger: a prior
    # L1-only claim), and the L2 acquire now reports OUR OWN ref as the holder.
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})  # token=""
    monkeypatch.setattr(coordination, "acquire", _l2_conflict("m/p/wt-a"))
    # Re-claim from the same owner -> must ADOPT (not raise), no --force needed.
    cl = lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"}, holder_ref="m/p/wt-a")
    assert cl.worktree == "/wt/a"


def test_claim_self_conflict_via_holder_ref_adopts(leases, monkeypatch):
    from agent_codespaces import coordination
    # No local L1 record, but the L2 acquire says WE hold it (same worktree id,
    # different session/machine-prefix in the ref) -> recognized as self, adopt.
    monkeypatch.setattr(coordination, "acquire",
                        _l2_conflict("othermachine/p/wt-a#sess"))
    cl = lease_mod.claim("cs-x", "/path/wt-a", active={"/path/wt-a"},
                         holder_ref="m/p/wt-a")
    assert cl.worktree == "/path/wt-a"


def test_claim_l2_conflict_reports_real_local_holder(leases, monkeypatch):
    from agent_codespaces import coordination
    # A DIFFERENT owner holds it locally; the L2 acquire (as /wt/b) conflicts.
    # The raised ClaimConflict must carry the real local worktree/host/pid, not
    # the '(cross-machine)'/pid-0 placeholder (#1362 defect 2).
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a", "/wt/b"})
    monkeypatch.setattr(coordination, "acquire", _l2_conflict("other/p/wt-z"))
    with pytest.raises(lease_mod.ClaimConflict) as ei:
        lease_mod.claim("cs-one", "/wt/b", active={"/wt/a", "/wt/b"},
                        holder_ref="m/p/wt-b")
    assert ei.value.holder == "/wt/a"
    assert ei.value.host != "(cross-machine)"
    assert ei.value.pid != 0


def test_claim_l2_conflict_cross_machine_names_ref(leases, monkeypatch):
    from agent_codespaces import coordination
    # No local record -> genuinely remote holder: name the remote ClaimRef
    # (not a blank), and mark it cross-machine.
    monkeypatch.setattr(coordination, "acquire", _l2_conflict("remote/p/wt-r"))
    with pytest.raises(lease_mod.ClaimConflict) as ei:
        lease_mod.claim("cs-remote", "/wt/b", active={"/wt/b"},
                        holder_ref="m/p/wt-b")
    assert ei.value.holder == "remote/p/wt-r"
    assert ei.value.host == "(cross-machine)"


def test_same_holder_ref_matches_worktree_id():
    assert lease_mod._same_holder_ref("m/p/wt-a", "other/p/wt-a#sess") is True
    assert lease_mod._same_holder_ref("m/p/wt-a", "m/p/wt-b") is False
    assert lease_mod._same_holder_ref(None, "m/p/wt-a") is False
    assert lease_mod._same_holder_ref("m/p/wt-a", "") is False


def test_release_claim_is_owner_scoped(leases):
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})
    assert lease_mod.release_claim("cs-one", "/wt/b") is False  # not the owner
    assert lease_mod.release_claim("cs-one", "/wt/a") is True
    assert lease_mod.get_lease("cs-one") is None


def test_release_worktree_claims_releases_all_for_owner(leases):
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a"})
    lease_mod.claim("cs-two", "/wt/a", active={"/wt/a"})
    lease_mod.claim("cs-three", "/wt/b", active={"/wt/a", "/wt/b"})
    released = lease_mod.release_worktree_claims("/wt/a")
    assert set(released) == {"cs-one", "cs-two"}
    assert lease_mod.get_lease("cs-three").worktree == "/wt/b"


def test_sweep_dead_releases_gone_worktree(leases):
    lease_mod.claim("cs-one", "/wt/gone", active={"/wt/gone"})
    released = lease_mod.sweep_dead(active=set())  # /wt/gone no longer active
    assert released == ["cs-one"]
    assert lease_mod.get_lease("cs-one") is None


def test_sweep_dead_keeps_live_and_legacy(leases, tmp_path):
    wt = tmp_path / "wt-live"
    wt.mkdir()
    lease_mod.claim("cs-live", str(wt), active=None)  # path exists -> live
    lease_mod.borrow("effort-x", "cs-legacy")  # advisory lease (no worktree)
    released = lease_mod.sweep_dead(active=set())
    assert released == []
    assert lease_mod.get_lease("cs-live") is not None
    assert lease_mod.get_lease("cs-legacy") is not None
