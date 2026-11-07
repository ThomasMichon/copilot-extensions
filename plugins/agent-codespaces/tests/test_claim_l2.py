"""Two-tier (L1 local + L2 cross-machine) claim behavior for the CodeSpace broker."""

from __future__ import annotations

import pytest
import json
import os
import subprocess
import time
from types import SimpleNamespace

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
    monkeypatch.setattr(lease_mod, "_DEPLOY_HOLDS_FILE", tmp_path / "deploy-holds.json")
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(lease_mod, "resolve_owner_worktree", lambda: "/wt/a")
    monkeypatch.setattr(coord, "owner_ref", lambda: "m/p/w")
    monkeypatch.setattr(coord, "inspect", lambda name: {
        "state": "leased", "holder": "m/p/w",
        "token": lease_mod._read_leases()[name].lease_token,
    })
    def run(args, **kwargs):
        if args[:3] == ["--project", "p", "claims"]:
            payload = {
                "version": 1, "machine": "m", "repo": "p", "worktree_id": "w",
                "outbound": [
                    {"kind": "codespace", "ref": name, "state": "active"}
                    for name in lease_mod._read_leases()
                ],
            }
        else:
            payload = {"worktree_id": "w", "ref": args[2], "disposition": "released"}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(coord, "_run", run)
    return tmp_path


def _acq(token):
    return lambda key, holder, **kw: coord.L2Result("ok", token=token)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_existing_provider_hold_refuses_claim_before_shared_effects(leases, monkeypatch, existing, force):
    if existing:
        monkeypatch.setattr(coord, "acquire", _acq("a" * 40))
        lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    original = lease_mod.LEASE_FILE.read_bytes() if existing else None
    shared = {"state": "leased" if existing else "absent", "token": "a" * 40}
    calls = []

    def acquire(*args, **kwargs):
        calls.append("acquire")
        shared.update(state="leased", token="b" * 40)
        return coord.L2Result("ok", token=shared["token"])

    def renew(*args, **kwargs):
        calls.append("renew")
        shared["token"] = "b" * 40
        return coord.L2Result("ok", token=shared["token"])

    def release(*args, **kwargs):
        calls.append("release")
        shared["state"] = "released"
        return coord.L2Result("ok")

    monkeypatch.setattr(coord, "acquire", acquire)
    monkeypatch.setattr(coord, "renew", renew)
    monkeypatch.setattr(coord, "release", release)
    with lease_mod.deploy_hold("cs-one", "stop"):
        with pytest.raises(lease_mod.ProviderAdmissionError):
            lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w", force=force)
    assert shared == {"state": "leased" if existing else "absent", "token": "a" * 40}
    assert calls == []
    assert (lease_mod.LEASE_FILE.read_bytes() if lease_mod.LEASE_FILE.exists() else None) == original


def test_provider_hold_creation_joins_claim_exclusion(leases, monkeypatch):
    monkeypatch.setattr(coord, "acquire", _acq("a" * 40))
    lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")

    def renew(*args, **kwargs):
        with pytest.raises(lease_mod.ProviderAdmissionError, match="busy"):
            with lease_mod.deploy_hold("cs-one", "stop"):
                pytest.fail("provider hold interleaved with claim admission")
        return coord.L2Result("ok", token="b" * 40)

    monkeypatch.setattr(coord, "renew", renew)
    assert lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w").lease_token == "b" * 40
    assert lease_mod.get_deploy_hold("cs-one") is None


@pytest.mark.parametrize("mode", ["renew", "adopt", "adopt-absent", "acquire", "renew-replaced"])
def test_late_hold_cleanup_releases_only_this_attempts_acquisition(leases, monkeypatch, mode):
    original = None
    if mode in ("renew", "adopt", "renew-replaced"):
        monkeypatch.setattr(coord, "acquire", _acq("a" * 40))
        original = lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    shared = {"state": "leased", "token": "b" * 40}
    released = []

    def coordinate(*args, **kwargs):
        # A legacy writer can bypass the new exclusion; retain the final check.
        now = time.time()
        hold = lease_mod.DeployHold(
            "cs-one", "stop", "hold-token", os.getpid(),
            lease_mod._this_host(), lease_mod._this_environment(), now, now, now + 60,
        )
        with lease_mod._lease_lock():
            lease_mod._write_records(lease_mod._DEPLOY_HOLDS_FILE, {"cs-one": hold})
            if mode == "renew-replaced":
                records = lease_mod._read_leases()
                records["cs-one"].worktree = "/wt/b"
                records["cs-one"].lease_token = "c" * 40
                lease_mod._write_leases(records)
        return coord.L2Result(
            "conflict" if mode.startswith("adopt") else "ok",
            token="b" * 40, holder="m/p/w",
        )

    def release(name, token):
        released.append(token)
        shared["state"] = "released"
        return coord.L2Result("ok")

    monkeypatch.setattr(coord, "acquire", coordinate)
    monkeypatch.setattr(coord, "renew", coordinate)
    monkeypatch.setattr(coord, "release", release)
    with pytest.raises(lease_mod.ProviderAdmissionError):
        lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    assert shared["state"] == ("released" if mode == "acquire" else "leased")
    assert released == (["b" * 40] if mode == "acquire" else [])
    held = lease_mod._read_leases().get("cs-one")
    if mode == "renew-replaced":
        assert held.worktree == "/wt/b" and held.lease_token == "c" * 40
    elif original:
        assert held.lease_token == "b" * 40
        assert held.worktree == original.worktree and held.acquired_at == original.acquired_at
    else:
        assert held is None


@pytest.mark.parametrize("exception", [False, True])
def test_new_acquisition_rollback_failure_remains_explicit(leases, monkeypatch, exception):
    states = iter(({}, {"cs-one": SimpleNamespace(operation="stop")}))
    monkeypatch.setattr(lease_mod, "_read_live_records", lambda *a, **kw: next(states))
    monkeypatch.setattr(coord, "acquire", _acq("b" * 40))
    returned = []

    def release(name, token):
        returned.append((name, token))
        if exception:
            raise RuntimeError("return transport unavailable")
        return coord.L2Result("unavailable")

    monkeypatch.setattr(coord, "release", release)
    with pytest.raises(lease_mod.ClaimAdmissionRejected, match="newly acquired.*unconfirmed"):
        lease_mod.claim("cs-one", "/wt/a", holder_ref="m/p/w")
    assert returned == [("cs-one", "b" * 40)]
    assert lease_mod._read_leases() == {}


def test_known_local_conflict_refuses_before_shared_acquisition(leases, monkeypatch):
    lease_mod.claim("cs-one", "/wt/a", active={"/wt/a", "/wt/b"})
    monkeypatch.setattr(coord, "acquire", lambda *a, **kw: pytest.fail("acquisition before local admission"))
    with pytest.raises(lease_mod.ClaimConflict):
        lease_mod.claim("cs-one", "/wt/b", active={"/wt/a", "/wt/b"}, holder_ref="m/p/w-b")


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
    assert json.loads(lease_mod.LEASE_FILE.read_bytes())["cs-one"] == json.loads(before)["cs-one"]


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
        elif args[:3] == ["--project", "p", "claims"]:
            payload = {
                "version": 1, "machine": "m", "repo": "p", "worktree_id": "w",
                "outbound": [{"kind": "codespace", "ref": "cs-one", "state": "active"}],
            }
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
