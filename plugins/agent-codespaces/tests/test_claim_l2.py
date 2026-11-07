"""Two-tier (L1 local + L2 cross-machine) claim behavior for the CodeSpace broker."""

from __future__ import annotations

import pytest
import json
import subprocess

from agent_codespaces import coordination as coord
from agent_codespaces import lease as lease_mod

REAL_INSPECT = coord.inspect
REAL_RENEW = coord.renew


@pytest.fixture
def leases(monkeypatch, tmp_path):
    """Redirect lease state to a tmp dir so tests never touch real state."""
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(lease_mod, "resolve_owner_worktree", lambda: "/wt/a")
    monkeypatch.setattr(coord, "owner_ref", lambda: "m/p/w")
    monkeypatch.setattr(coord, "inspect", lambda name: {
        "state": "leased", "holder": "m/p/w",
        "token": lease_mod._read_leases()[name].lease_token,
    })
    monkeypatch.setattr(coord, "_run", lambda args, **kw: subprocess.CompletedProcess(
        args, 0, json.dumps({
            "worktree_id": "w", "ref": args[2], "disposition": "released",
        }), "",
    ))
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


@pytest.mark.parametrize("holder,expected", [
    ("m/p/w#new-session", True), ("other/p/w", False), ("m/other/w", False),
])
def test_renew_conflict_reconciles_full_owner_and_current_token(leases, monkeypatch, holder, expected):
    monkeypatch.setattr(coord, "acquire", _acq("a" * 40))
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")

    def run(args, **kw):
        if args[:2] == ["lease", "renew"]:
            return subprocess.CompletedProcess(args, 3, "", "stale fencing token")
        assert args[:2] == ["lease", "inspect"]
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "state": "leased", "holder": holder, "token": "b" * 40, "live": True,
        }), "")

    monkeypatch.setattr(coord, "inspect", REAL_INSPECT)
    monkeypatch.setattr(coord, "renew", REAL_RENEW)
    monkeypatch.setattr(coord, "_run", run)
    if expected:
        assert lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w").lease_token == "b" * 40
    else:
        with pytest.raises(lease_mod.ClaimConflict):
            lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
        assert lease_mod._read_leases()["cs-one"].lease_token == "a" * 40


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


@pytest.mark.parametrize("release", [
    lambda: lease_mod.release("cs-one"),
    lambda: lease_mod.release_worktree_claims("/wt/a"),
])
def test_generic_and_bulk_return_keep_unknown_shared_custody(leases, monkeypatch, release):
    monkeypatch.setattr(coord, "acquire", _acq("tok-1"))
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    before = lease_mod.LEASE_FILE.read_bytes()
    monkeypatch.setattr(coord, "release", lambda *a, **kw: coord.L2Result("unavailable"))
    with pytest.raises(RuntimeError, match="retained"):
        release()
    assert lease_mod.LEASE_FILE.read_bytes() == before


def test_return_from_another_caller_settles_original_qualified_owner(leases, monkeypatch):
    monkeypatch.setattr(coord, "acquire", _acq("tok-1"))
    owner = str(leases / "original")
    lease_mod.claim("cs-one", owner, holder_ref="m/p/w")
    monkeypatch.setattr(lease_mod, "resolve_owner_worktree", lambda: str(leases / "caller"))
    monkeypatch.setattr(coord, "owner_ref", lambda: "m/other/w")
    effects = []

    def run(args, **kw):
        if args[:2] == ["claims", "owner"]:
            payload = {"kind": "codespace", "ref": "cs-one", "owners": [{
                "worktree_path": owner, "qualified_ref": "m/p/w", "state": "active",
            }]}
        else:
            assert args == [
                "claims", "settle", "cs-one", "--owner-ref", "m/p/w", "--json", "--released",
            ]
            assert effects == ["release"]
            payload = {"worktree_id": "w", "ref": "cs-one", "disposition": "released"}
            effects.append("settle-original")
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(coord, "_run", run)
    monkeypatch.setattr(
        coord, "release", lambda *a, **kw: effects.append("release") or coord.L2Result("ok"),
    )
    assert lease_mod.release_claim("cs-one", owner)
    assert effects == ["release", "settle-original"]
    assert lease_mod.get_lease("cs-one") is None


@pytest.mark.parametrize("metadata", [
    [], [{"worktree_path": "/wrong/path", "qualified_ref": "m/p/w"}],
    [{"worktree_path": "/wt/a", "qualified_ref": "different/p/w"}],
])
def test_uncertain_reverse_owner_never_releases(leases, monkeypatch, metadata):
    monkeypatch.setattr(coord, "acquire", _acq("tok-1"))
    owner = str(leases / "original")
    lease_mod.claim("cs-one", owner, holder_ref="m/p/w")
    monkeypatch.setattr(lease_mod, "resolve_owner_worktree", lambda: "other")
    monkeypatch.setattr(coord, "_run", lambda args, **kw: subprocess.CompletedProcess(
        args, 0, json.dumps({"owners": metadata}), "",
    ))
    monkeypatch.setattr(coord, "release", lambda *a, **kw: pytest.fail("unconfirmed owner released"))
    with pytest.raises(RuntimeError, match="retained"):
        lease_mod.release_claim("cs-one", owner)
    assert lease_mod._read_leases()["cs-one"].worktree == owner
