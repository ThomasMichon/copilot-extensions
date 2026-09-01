"""Two-tier (L1 local + L2 cross-machine) claim behavior for the CodeSpace broker."""

from __future__ import annotations

import pytest

from agent_codespaces import coordination as coord
from agent_codespaces import lease as lease_mod


@pytest.fixture
def leases(monkeypatch, tmp_path):
    """Redirect lease state to a tmp dir so tests never touch real state."""
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)
    return tmp_path


def _acq(token):
    return lambda key, holder, **kw: coord.L2Result("ok", token=token)


def test_claim_takes_l2_lease_and_stores_token(leases, monkeypatch):
    seen = {}

    def fake_acquire(key, holder, **kw):
        seen.update(key=key, holder=holder)
        return coord.L2Result("ok", token="tok-1")

    monkeypatch.setattr(lease_mod.coordination, "acquire", fake_acquire)
    lease = lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    assert lease.lease_token == "tok-1"
    assert seen == {"key": "cs-one", "holder": "m/p/w"}
    # token persisted to L1 for later renew/release
    assert lease_mod.get_lease("cs-one").lease_token == "tok-1"


def test_claim_blocks_on_cross_machine_conflict(leases, monkeypatch):
    monkeypatch.setattr(
        lease_mod.coordination, "acquire",
        lambda key, holder, **kw: coord.L2Result("conflict", holder="otherhost/p/other-wt"),
    )
    with pytest.raises(lease_mod.ClaimConflict, match="other-wt"):
        lease_mod.claim("cs-one", "/wt/a", holder_ref="myhost/p/my-wt")
    # no local claim was written when the cross-machine acquire lost
    assert lease_mod.get_lease("cs-one") is None


def test_claim_blocks_on_coordination_rejection_before_local_state(
    leases,
    monkeypatch,
):
    monkeypatch.setattr(
        lease_mod.coordination,
        "preflight",
        lambda holder: coord.PreflightResult(
            "rejected",
            code="knowledge_binding_required",
            detail="repair binding",
        ),
    )
    monkeypatch.setattr(
        lease_mod.coordination,
        "acquire",
        lambda *a, **k: pytest.fail("rejected claim attempted lease acquisition"),
    )

    with pytest.raises(
        lease_mod.CoordinationRejected,
        match="knowledge_binding_required",
    ):
        lease_mod.claim("cs-one", "/wt/a", holder_ref="myhost/p/my-wt")
    assert lease_mod.get_lease("cs-one") is None


def test_force_over_cross_machine_conflict_proceeds_without_token(leases, monkeypatch):
    monkeypatch.setattr(
        lease_mod.coordination, "acquire",
        lambda key, holder, **kw: coord.L2Result("conflict", holder="other/p/w"),
    )
    lease = lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w", force=True)
    assert lease.worktree == "/wt/a"
    assert lease.lease_token == ""  # forced past L2; no distributed lease held


def test_l2_unavailable_falls_back_to_l1(leases, monkeypatch):
    monkeypatch.setattr(
        lease_mod.coordination, "acquire",
        lambda key, holder, **kw: coord.L2Result("unavailable"),
    )
    lease = lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    assert lease.worktree == "/wt/a"
    assert lease.lease_token == ""
    assert lease_mod.get_lease("cs-one").worktree == "/wt/a"


def test_no_holder_ref_skips_l2(leases, monkeypatch):
    monkeypatch.setattr(
        lease_mod.coordination, "acquire",
        lambda *a, **k: pytest.fail("L2 must not be attempted without a holder_ref"),
    )
    lease = lease_mod.claim("cs-one", "/wt/a")
    assert lease.worktree == "/wt/a"
    assert lease.lease_token == ""


def test_reclaim_same_owner_renews_l2(leases, monkeypatch):
    monkeypatch.setattr(lease_mod.coordination, "acquire", _acq("tok-1"))
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")

    renews = {}

    def fake_renew(key, token, **kw):
        renews.update(key=key, token=token)
        return coord.L2Result("ok", token="tok-2")

    monkeypatch.setattr(
        lease_mod.coordination, "acquire",
        lambda *a, **k: pytest.fail("re-claim by same owner must renew, not acquire"),
    )
    monkeypatch.setattr(
        lease_mod.coordination,
        "preflight",
        lambda *a, **k: pytest.fail(
            "existing lease renewal must not rerun acquisition preflight"
        ),
    )
    monkeypatch.setattr(lease_mod.coordination, "renew", fake_renew)
    lease = lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    assert renews == {"key": "cs-one", "token": "tok-1"}
    assert lease.lease_token == "tok-2"


def test_release_claim_releases_l2(leases, monkeypatch):
    monkeypatch.setattr(lease_mod.coordination, "acquire", _acq("tok-1"))
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")

    released = {}
    monkeypatch.setattr(
        lease_mod.coordination, "release",
        lambda key, token, **kw: released.update(key=key, token=token) or coord.L2Result("ok"),
    )
    assert lease_mod.release_claim("cs-one", "/wt/a") is True
    assert released == {"key": "cs-one", "token": "tok-1"}
    assert lease_mod.get_lease("cs-one") is None


def test_release_worktree_claims_releases_all_l2(leases, monkeypatch):
    tokens = iter(["tok-1", "tok-2"])
    monkeypatch.setattr(
        lease_mod.coordination, "acquire",
        lambda key, holder, **kw: coord.L2Result("ok", token=next(tokens)),
    )
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    lease_mod.claim("cs-two", "/wt/a", holder_ref="m/p/w")

    released = []
    monkeypatch.setattr(
        lease_mod.coordination, "release",
        lambda key, token, **kw: released.append((key, token)) or coord.L2Result("ok"),
    )
    out = lease_mod.release_worktree_claims("/wt/a")
    assert set(out) == {"cs-one", "cs-two"}
    assert set(released) == {("cs-one", "tok-1"), ("cs-two", "tok-2")}


def test_heartbeat_renews_and_rotates_token(leases, monkeypatch):
    monkeypatch.setattr(lease_mod.coordination, "acquire", _acq("tok-1"))
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")

    monkeypatch.setattr(
        lease_mod.coordination, "renew",
        lambda key, token, **kw: coord.L2Result("ok", token="tok-2"),
    )
    assert lease_mod.heartbeat("cs-one") is True
    assert lease_mod.get_lease("cs-one").lease_token == "tok-2"


def test_legacy_lease_without_token_skips_l2_release(leases, monkeypatch):
    # An L1-only claim (no holder_ref -> no token) must not shell an L2 release.
    lease_mod.claim("cs-one", "/wt/a")
    monkeypatch.setattr(
        lease_mod.coordination, "release",
        lambda *a, **k: pytest.fail("no L2 release without a token"),
    )
    assert lease_mod.release_claim("cs-one", "/wt/a") is True
