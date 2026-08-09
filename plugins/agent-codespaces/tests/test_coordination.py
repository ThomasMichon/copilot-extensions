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
    for name in ("owner_ref", "acquire", "renew", "release", "inspect", "list_leases")
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


def test_acquire_missing_binstub_is_unavailable(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: None)
    assert coord.acquire("cs-one", "m/p/w").unavailable


def test_renew_requires_token(monkeypatch):
    # no token -> unavailable without touching _run
    assert coord.renew("cs-one", "").unavailable


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


def test_renew_conflict_is_reported(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(3, stderr="lease lost"))
    assert coord.renew("cs-one", "a" * 40).conflict


def test_release_ok(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "{}"))
    assert coord.release("cs-one", "a" * 40).ok


def test_release_without_token_is_unavailable():
    assert coord.release("cs-one", "").unavailable


def test_owner_ref_from_binstub(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: _proc(0, "tmichon-dev6/odsp-web/wt-123\n"))
    assert coord.owner_ref() == "tmichon-dev6/odsp-web/wt-123"


def test_owner_ref_explicit_wins(monkeypatch):
    monkeypatch.setattr(coord, "_run", lambda *a, **k: pytest.fail("should not shell"))
    assert coord.owner_ref(explicit="m/p/w") == "m/p/w"


def test_owner_ref_unresolvable_is_none(monkeypatch):
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
