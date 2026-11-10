"""Tests for the cross-machine L2 lease coordination shim."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_codespaces import coordination as coord

# Capture the real functions at import time -- BEFORE the package conftest's
# autouse ``_neutralize_l2`` fixture replaces them with degrade stubs -- so this
# module (which tests the shim itself) can restore the real implementations.
_REAL = {
    name: getattr(coord, name)
    for name in ("owner_ref", "preflight", "acquire", "renew", "release", "inspect",
                 "list_leases", "publish_cleanliness", "list_cleanliness")
}


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["agent-worktrees"], returncode, stdout, stderr)


@pytest.fixture(autouse=True)
def _no_real_l2(monkeypatch):
    """This module tests the shim itself, so restore the real implementations
    (past the package conftest's neutralizer) -- but never let it shell a real
    subprocess: every test stubs ``coordination._run``."""
    for name, fn in _REAL.items():
        monkeypatch.setattr(coord, name, fn)
    monkeypatch.setattr(
        coord, "_run", lambda *a, **k: pytest.fail("unexpected real _run"),
    )


def test_acquire_ok_returns_token(monkeypatch):
    token = "a" * 40
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, json.dumps({"token": token})))
    res = coord.acquire("cs-one", "m/p/w")
    assert res.ok and res.token == token


def test_acquire_conflict_inspects_for_holder(monkeypatch):
    def fake_run(args, **kw):
        if args[:2] == ["lease", "acquire"]:
            return _proc(3, stderr="lease conflict: ...")
        if args[:2] == ["lease", "inspect"]:
            return _proc(0, json.dumps({"holder": "other/p/w", "state": "leased"}))
        return _proc(2)
    monkeypatch.setattr(coord, "_run", fake_run)
    res = coord.acquire("cs-one", "m/p/w")
    assert res.conflict and res.holder == "other/p/w"


def test_acquire_config_error_degrades_to_unavailable(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(2, stderr="no origin"))
    assert coord.acquire("cs-one", "m/p/w").unavailable


@pytest.mark.parametrize("capabilities", [None, [], "lease-acquire-released-only-v1", [1]])
def test_released_only_missing_or_malformed_support_never_acquires(monkeypatch, capabilities):
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return _proc(0, json.dumps({
            "version": 1, "ready": True, "code": "ready", "capabilities": capabilities,
        }))

    monkeypatch.setattr(coord, "_run", run)
    result = coord.acquire("cs-one", "m/p/w", released_only=True)
    assert result.rejected
    assert "does not support" in result.detail
    assert seen == [["--project", "p", "coordination-readiness"]]


@pytest.mark.parametrize("exit_code", [0, 2, 4])
def test_released_only_uses_selected_receiver_and_never_replays_baseline(monkeypatch, exit_code):
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        if args[:2] == ["--project", "p"]:
            return _proc(0, json.dumps({
                "version": 1, "ready": True, "code": "ready",
                "capabilities": [coord.RELEASED_ONLY_CAPABILITY],
            }))
        return _proc(exit_code, json.dumps({"token": "b" * 40}), "fixture failure")

    monkeypatch.setattr(coord, "_run", run)
    result = coord.acquire("cs-one", "m/p/w", released_only=True)
    assert result.ok is (exit_code == 0)
    assert len(seen) == 2
    assert seen[1] == [
        "lease", "acquire", "codespace", "cs-one", "--holder", "m/p/w",
        "--released-only", "--ttl", str(coord.DEFAULT_L2_TTL),
    ]


@pytest.mark.parametrize("token", [None, 42, int("1" * 40), int("1" * 64), "", "not-an-oid"])
def test_released_only_does_not_report_success_without_valid_token(monkeypatch, token):
    readiness = coord.PreflightResult(
        "ready", capabilities=frozenset({coord.RELEASED_ONLY_CAPABILITY}),
    )
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return _proc(0, json.dumps({"token": token}))

    monkeypatch.setattr(coord, "_run", run)
    result = coord.acquire(
        "cs-one", "m/p/w", released_only=True, preflight_result=readiness,
    )
    assert result.unavailable and "fencing token" in result.detail
    assert len(seen) == 1 and "--released-only" in seen[0]


@pytest.mark.parametrize("invalid", [
    None, {"resource": {"key": "cs-one"}}, {"state": []},
])
def test_strict_lease_inventory_refuses_unknown_rows(monkeypatch, invalid):
    monkeypatch.setattr(coord, "_run", lambda *args, **kwargs: _proc(0, json.dumps([invalid])))
    assert coord.list_leases(strict=True) is None


def test_strict_lease_inventory_distinguishes_expiry_from_release(monkeypatch):
    records = [{
        "resource": {"kind": "codespace", "key": name}, "holder": "m/p/old",
        "token": token * 40, "live": False, "state": state,
    } for name, state, token in (
        ("expired", "leased", "a"), ("returned", "released", "b"),
    )]
    monkeypatch.setattr(coord, "_run", lambda *args, **kwargs: _proc(0, json.dumps(records)))
    leases = coord.list_leases(strict=True)
    assert leases["expired"].state == "leased"
    assert leases["returned"].state == "released"


def test_acquire_missing_binstub_is_unavailable(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: None)
    assert coord.acquire("cs-one", "m/p/w").unavailable


def test_preflight_ready_for_owner_project(monkeypatch):
    payload = {"version": 1, "ready": True, "code": "ready", "error": None}
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return _proc(0, json.dumps(payload))

    monkeypatch.setattr(coord, "_run", fake_run)
    result = coord.preflight("machine/project/worktree#session")
    assert result.ready
    assert seen["args"] == [
        "--project", "project", "coordination-readiness"
    ]


@pytest.mark.parametrize(
    "code",
    ["knowledge_binding_required", "state_root_resolution_failed"],
)
def test_preflight_preserves_compatible_rejection(monkeypatch, code):
    payload = {
        "version": 1,
        "ready": False,
        "code": code,
        "error": "repair binding",
    }
    monkeypatch.setattr(
        coord, "_run", lambda *a, **k: _proc(3, json.dumps(payload))
    )
    result = coord.preflight("machine/project/worktree")
    assert result.rejected
    assert result.code == code
    assert result.detail == "repair binding"


@pytest.mark.parametrize(
    "process",
    [
        _proc(1, stderr="unknown command"),
        _proc(0, "not json"),
        _proc(0, json.dumps({"ready": True, "code": "ready"})),
        _proc(0, json.dumps({"version": 2, "ready": True, "code": "ready"})),
        _proc(0, json.dumps({"version": True, "ready": True, "code": "ready"})),
        _proc(3, json.dumps({
            "version": 1,
            "ready": False,
            "code": "future_code",
            "error": "future",
        })),
    ],
)
def test_preflight_incompatible_peer_degrades_as_absent(monkeypatch, process):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: process)
    assert coord.preflight("machine/project/worktree").absent


def test_acquire_preserves_structured_binding_rejection(monkeypatch):
    payload = {
        "error": "repair binding",
        "code": "knowledge_binding_required",
    }
    monkeypatch.setattr(
        coord,
        "_run",
        lambda *a, **k: _proc(5, stderr=json.dumps(payload)),
    )
    result = coord.acquire("cs-one", "m/p/w")
    assert result.rejected
    assert "knowledge_binding_required" in result.detail


def test_renew_requires_token(monkeypatch):
    # no token -> unavailable without touching _run
    assert coord.renew("cs-one", "").unavailable


def test_mirror_disposition_no_token_is_noop(monkeypatch):
    # no token -> False without shelling out (fixture fails on unexpected _run)
    assert coord.mirror_disposition("cs-one", "at-rest", None) is False
    assert coord.mirror_disposition("cs-one", "at-rest", "  ") is False


def test_mirror_disposition_ok_renews_with_disposition(monkeypatch):
    seen = {}
    def fake_run(args, **kw):
        seen["args"] = args
        return _proc(0)
    monkeypatch.setattr(coord, "_run", fake_run)
    assert coord.mirror_disposition("cs-one", "at-rest", "a" * 40) is True
    assert seen["args"][:4] == ["lease", "renew", "codespace", "cs-one"]
    assert "--token" in seen["args"] and "a" * 40 in seen["args"]
    assert seen["args"][seen["args"].index("--disposition") + 1] == "at-rest"


def test_mirror_disposition_conflict_or_error_is_false(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(3, stderr="ref moved"))
    assert coord.mirror_disposition("cs-one", "at-rest", "a" * 40) is False
    monkeypatch.setattr(coord, "_run", lambda *a, **k: None)
    assert coord.mirror_disposition("cs-one", "at-rest", "a" * 40) is False


def test_renew_ok_rotates_token(monkeypatch):
    new = "b" * 40
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, json.dumps({"token": new})))
    res = coord.renew("cs-one", "a" * 40)
    assert res.ok and res.token == new


# --- obligation journaling / settlement (Ph3b-wiring/2) ----------------------

def test_journal_obligation_shells_claims_add(monkeypatch):
    seen = {}
    def fake_run(args, **kw):
        seen["args"] = args
        return _proc(0, json.dumps({"worktree_id": "wt-b", "ref": "cs-one"}))
    monkeypatch.setattr(coord, "_run", fake_run)
    assert coord.journal_obligation("cs-one", "m/p/wt-b") is True
    assert seen["args"] == [
        "claims", "add", "codespace", "cs-one", "--owner-ref", "m/p/wt-b", "--json"]


def test_journal_obligation_no_holder_ref_is_noop(monkeypatch):
    monkeypatch.setattr(coord, "_run",
                        lambda *a, **k: pytest.fail("should not shell"))
    assert coord.journal_obligation("cs-one", None) is False
    assert coord.journal_obligation("cs-one", "  ") is False


def test_journal_obligation_degrades_on_error(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(2, stderr="boom"))
    assert coord.journal_obligation("cs-one", "m/p/wt-b") is False
    monkeypatch.setattr(coord, "_run", lambda *a, **k: None)  # no binstub
    assert coord.journal_obligation("cs-one", "m/p/wt-b") is False


def test_settle_obligation_shells_claims_settle(monkeypatch):
    seen = {}
    def fake_run(args, **kw):
        seen["args"] = args
        return _proc(0, json.dumps({"disposition": "at-rest"}))
    monkeypatch.setattr(coord, "_run", fake_run)
    assert coord.settle_obligation("cs-one", "m/p/wt-b") is True
    assert seen["args"] == [
        "claims", "settle", "cs-one", "--owner-ref", "m/p/wt-b", "--json"]


def test_settle_obligation_released_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(coord, "_run",
                        lambda args, **k: (seen.update(args=args), _proc(0, "{}"))[1])
    assert coord.settle_obligation("cs-one", "m/p/wt-b", released=True) is True
    assert "--released" in seen["args"]


def test_settle_obligation_degrades(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(1, stderr="no claim"))
    assert coord.settle_obligation("cs-one", "m/p/wt-b") is False
    assert coord.settle_obligation("cs-one", None) is False


@pytest.mark.parametrize("operation", ["journal", "settle"])
@pytest.mark.parametrize("bad", [
    "deferred", "wrong-owner", "wrong-ref", "wrong-state", "finalized", "malformed",
    "history-object", "history-invalid-row",
])
def test_confirmed_bookkeeping_refuses_unconfirmed_ack(monkeypatch, operation, bad):
    payload = {
        "worktree_id": "wt-b", "ref": "cs-one", "kind": "codespace",
        "state": "active", "disposition": "released",
    }
    if bad == "deferred":
        payload["deferred"] = True
    elif bad == "wrong-owner":
        payload["worktree_id"] = "other"
    elif bad == "wrong-ref":
        payload["ref"] = "other"
    elif bad == "wrong-state":
        payload.update(state="released", disposition="at-rest")
    elif bad == "finalized":
        payload["released_by_earlier_finalize"] = True
    elif bad == "history-object":
        payload["released_by_earlier_finalize"] = {}
    elif bad == "history-invalid-row":
        payload["released_by_earlier_finalize"] = [{"kind": "codespace", "ref": None}]
    monkeypatch.setattr(
        coord, "_run", lambda *a, **k: _proc(0, "{" if bad == "malformed" else json.dumps(payload)),
    )
    if operation == "journal":
        assert not coord.journal_obligation("cs-one", "m/p/wt-b", confirmed=True)
    else:
        assert not coord.settle_obligation("cs-one", "m/p/wt-b", released=True, confirmed=True)


def test_confirmed_bookkeeping_accepts_native_ack(monkeypatch):
    def run(args, **kw):
        return _proc(0, json.dumps({
            "worktree_id": "wt-b", "ref": "cs-one", "kind": "codespace",
            "state": "active", "disposition": "released",
        }))

    monkeypatch.setattr(coord, "_run", run)
    assert coord.journal_obligation("cs-one", "m/p/wt-b#session", confirmed=True)
    assert coord.settle_obligation("cs-one", "m/p/wt-b#session", released=True, confirmed=True)


def test_confirmed_bookkeeping_accepts_native_reopen_history(monkeypatch):
    payload = {
        "worktree_id": "wt-b", "ref": "cs-one", "kind": "codespace",
        "state": "active", "disposition": "released", "reopened": True,
        "released_by_earlier_finalize": [
            {"kind": "codespace", "ref": "previous-venue", "note": "prior return"},
        ],
    }
    monkeypatch.setattr(coord, "_run", lambda *a, **kw: _proc(0, json.dumps(payload)))
    assert coord.journal_obligation("cs-one", "m/p/wt-b", confirmed=True)
    assert coord.settle_obligation("cs-one", "m/p/wt-b", released=True, confirmed=True)


def test_renew_conflict_is_reported(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(3, stderr="lease lost"))
    assert coord.renew("cs-one", "a" * 40).conflict


def test_release_ok(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "{}"))
    assert coord.release("cs-one", "a" * 40).ok


def test_release_without_token_is_unavailable():
    assert coord.release("cs-one", "").unavailable


@pytest.mark.parametrize("readiness", [
    None, coord.PreflightResult("absent"), coord.PreflightResult("ready"),
    coord.PreflightResult("rejected", capabilities=frozenset({"lease-return-proof-v1"})),
])
def test_return_proof_release_requires_selected_support_before_effects(readiness):
    assert coord.release(
        "cs-one", "a" * 40, retain_return_proof=True, preflight_result=readiness,
    ).rejected


@pytest.mark.parametrize("exit_code", [0, 2, 4])
def test_return_proof_release_never_replays_baseline(monkeypatch, exit_code):
    readiness = coord.PreflightResult("ready", capabilities=frozenset({coord.RETURN_PROOF_CAPABILITY}))
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return _proc(exit_code, "{}", "fixture release result unknown")

    monkeypatch.setattr(coord, "_run", run)
    result = coord.release(
        "cs-one", "a" * 40, retain_return_proof=True, preflight_result=readiness,
    )
    assert result.ok is (exit_code == 0)
    assert seen == [["lease", "release", "codespace", "cs-one", "--token", "a" * 40, "--retain-return-proof"]]


def test_owner_ref_from_binstub(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "example-dev6/example-web/wt-123\n"))
    assert coord.owner_ref() == "example-dev6/example-web/wt-123"


def test_owner_ref_explicit_wins(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: pytest.fail("should not shell"))
    assert coord.owner_ref(explicit="m/p/w") == "m/p/w"


def test_owner_ref_unresolvable_is_none(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "\n"))
    assert coord.owner_ref() is None


def test_inspect_absent_is_none(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, json.dumps({"state": "absent"})))
    assert coord.inspect("cs-one") is None


def test_list_leases_projects_records_by_key(monkeypatch):
    rows = [
        {"resource": {"kind": "codespace", "key": "cs-a"},
         "holder": "m1/p/w1", "live": True, "expires_at": "2026-08-07T18:00:00Z",
         "token": "a" * 40},
        {"resource": {"kind": "codespace", "key": "cs-b"},
         "holder": "m2/p/w2", "live": False, "expires_at": "2026-08-07T17:00:00Z"},
    ]
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, json.dumps(rows)))
    out = coord.list_leases()
    assert set(out) == {"cs-a", "cs-b"}
    assert out["cs-a"].holder == "m1/p/w1" and out["cs-a"].live is True
    assert out["cs-a"].token == "a" * 40
    assert out["cs-b"].live is False


def test_list_leases_empty_is_empty_map(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "[]"))
    assert coord.list_leases() == {}


def test_list_leases_unavailable_is_none(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: None)
    assert coord.list_leases() is None


def test_list_leases_nonzero_exit_is_none(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(2, stderr="no origin"))
    assert coord.list_leases() is None


def test_list_leases_malformed_json_is_none(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "not json"))
    assert coord.list_leases() is None


def test_list_leases_skips_keyless_or_nondict_rows(monkeypatch):
    rows = ["junk", {"holder": "no-resource"}, {"resource": {"kind": "codespace", "key": ""}},
            {"resource": {"kind": "codespace", "key": "cs-ok"}, "holder": "m/p/w", "live": True}]
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, json.dumps(rows)))
    out = coord.list_leases()
    assert set(out) == {"cs-ok"}


# --- cleanliness beacon (Phase 3 / codespace-clean-beacon) --------------------


def test_publish_cleanliness_acquires_with_context(monkeypatch):
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        return _proc(0, json.dumps({"token": "b" * 40}))

    monkeypatch.setattr(coord, "_run", fake_run)
    ok = coord.publish_cleanliness(
        "cs-one", known=True, clean=True, dirty=False, ahead=0,
        unpushed_branches=0, holder="m/p/w", at="2026-08-10T00:00:00Z", ttl=3600,
    )
    assert ok is True
    args = captured["args"]
    assert args[:4] == ["lease", "acquire", coord.KIND_CLEAN, "cs-one"]
    joined = " ".join(args)
    assert "--holder m/p/w" in joined
    assert "clean=1" in args and "known=1" in args and "dirty=0" in args
    assert "at=2026-08-10T00:00:00Z" in args


def test_publish_cleanliness_conflict_is_degrade_safe(monkeypatch):
    # A still-live prior record (conflict, exit 3) -> False (existing verdict
    # stands), never raises.
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(3, stderr="conflict"))
    assert coord.publish_cleanliness(
        "cs-one", known=True, clean=True, dirty=False, ahead=0,
        unpushed_branches=0,
    ) is False


def test_publish_cleanliness_unavailable_when_no_binstub(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: None)
    assert coord.publish_cleanliness(
        "cs-one", known=False, clean=False, dirty=True, ahead=1,
        unpushed_branches=1,
    ) is False


def test_list_cleanliness_projects_context(monkeypatch):
    rows = [{
        "resource": {"kind": coord.KIND_CLEAN, "key": "cs-one"},
        "holder": "m/p/w",
        "live": True,
        "context": {"known": "1", "clean": "0", "dirty": "1", "ahead": "2",
                    "unpushed_branches": "1", "at": "2026-08-10T00:00:00Z"},
    }]
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, json.dumps(rows)))
    out = coord.list_cleanliness()
    rec = out["cs-one"]
    assert rec.known and rec.dirty and not rec.clean
    assert rec.ahead == 2 and rec.unpushed_branches == 1
    assert rec.by == "m/p/w" and rec.live is True
    assert rec.off_box_safe is False        # live + known + not clean


def test_clean_record_off_box_safe_tristate():
    from agent_codespaces.coordination import CleanRecord

    def rec(**kw):
        base = dict(key="k", known=True, clean=True, dirty=False, ahead=0,
                    unpushed_branches=0, at="", by="", live=True)
        base.update(kw)
        return CleanRecord(**base)

    assert rec().off_box_safe is True
    assert rec(clean=False).off_box_safe is False
    assert rec(live=False).off_box_safe is None      # expired
    assert rec(known=False).off_box_safe is None      # unknown


def test_list_cleanliness_unavailable_is_none(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(2, stderr="no origin"))
    assert coord.list_cleanliness() is None


# ── owner_ref resolution order (explicit > cwd shell) ──────

def test_owner_ref_explicit_wins_over_env(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/p/w")
    # Explicit context does not require CWD discovery.
    assert coord.owner_ref(explicit="  exp/p/w  ") == "exp/p/w"


def test_owner_ref_ignores_ambient_env_and_resolves_cwd(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "stale/proj/wt#parent")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return _proc(0, "cwd/proj/wt#child\n")
    monkeypatch.setattr(coord, "_run", run)
    assert coord.owner_ref(session_id="child") == "cwd/proj/wt#child"
    assert calls == [["get", "owner-ref", "--session-id", "child"]]


def test_owner_ref_falls_back_to_cwd_shell_when_no_env(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "shell/p/w\n"))
    assert coord.owner_ref() == "shell/p/w"


def test_owner_ref_blank_env_falls_through_to_shell(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "   ")
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "shell/p/w\n"))
    assert coord.owner_ref() == "shell/p/w"
