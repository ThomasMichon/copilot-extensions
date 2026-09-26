"""Claim-provider callback tests for agent-codespaces (claim-provider-pattern
effort): ``claim-status``/``claim-reclaim`` for the ``codespace:`` namespace
agent-worktrees' claim-provider registry resolves.
"""
from __future__ import annotations

import argparse
import json
import types
from contextlib import contextmanager
import time

from agent_codespaces import claim_provider_cli as cpc
from agent_codespaces import lease as lease_mod

import pytest


@pytest.fixture(autouse=True)
def _lease_state_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(
        lease_mod,
        "_DEPLOY_HOLDS_FILE",
        tmp_path / "deploy-holds.json",
    )
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)


@pytest.fixture(autouse=True)
def _no_lease_by_default(monkeypatch):
    """Most reclaim tests aren't exercising the lease guard itself --
    default to unleased so they don't all need to mock this explicitly."""
    monkeypatch.setattr(cpc, "get_lease", lambda name: None)


@pytest.fixture(autouse=True)
def _no_account_resolution_by_default(monkeypatch):
    """Most reclaim tests aren't exercising the account-resolution/threading
    behavior itself -- default to "exists, no specific account resolved" so
    they don't all need to mock the up-front
    ``get_codespace_status_with_account`` call this callback now makes
    (claim-provider-pattern effort review finding: "Preserve the resolved
    account through CodeSpace reclamation"), and downstream calls still
    receive ``account=None`` exactly as before that change."""
    monkeypatch.setattr(cpc, "get_codespace_status_with_account",
                        lambda name: (True, None, None))


@pytest.fixture(autouse=True)
def _mint_token_by_default(monkeypatch):
    """A resolved (non-None) account now gets re-verified against
    gh_account.token_for_account before proceeding (claim-provider-pattern
    effort review finding: "Preserve validated credentials during status
    and reclaim") -- default to a successful mint so tests not exercising
    THAT behavior specifically don't all need to mock it."""
    monkeypatch.setattr(
        "agent_codespaces.gh_account.token_for_account", lambda login: "fake-token")


def test_claim_status_exists(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "get_codespace_status", lambda name: (True, "Shutdown"))
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-b"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": True, "state": "Shutdown"}


def test_claim_status_absent(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "get_codespace_status", lambda name: (False, None))
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-missing"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": False}


def test_claim_status_lookup_failure_is_not_false_absence(monkeypatch, capsys):
    """A lookup failure (gh/network/auth trouble, or an ambiguous listing)
    must exit non-zero -- never a false ``exists: false`` that could make a
    live claim look reclaimable."""
    def _boom(name):
        raise RuntimeError("gh CLI not found")
    monkeypatch.setattr(cpc, "get_codespace_status", _boom)
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-a"))
    assert rc != 0
    assert capsys.readouterr().out == ""


def test_claim_reclaim_dry_run_never_deletes(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=False))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "would delete" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_refuses_actively_leased_codespace(monkeypatch, capsys):
    called = {"n": 0}
    lease = types.SimpleNamespace(effort="other-effort")
    monkeypatch.setattr(cpc, "get_lease", lambda name: lease)
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: called.__setitem__("n", 1))
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "other-effort" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_apply_recovers_sessions_and_releases_lease(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda name, **k: calls.append(("sync", name)) or {"ok": True})
    monkeypatch.setattr(cpc, "delete_codespace",
                        lambda name, **k: calls.append(("delete", name)))
    monkeypatch.setattr(cpc, "release_lease",
                        lambda name: calls.append(("release", name)) or True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "deleted" in out["detail"]
    assert calls == [("sync", "cs-a"), ("delete", "cs-a"), ("release", "cs-a")]


def test_claim_reclaim_threads_resolved_account_through(monkeypatch, capsys):
    """The account get_codespace_status_with_account() confirms existence
    under must be threaded through sync_codespace_sessions/delete_codespace,
    not independently re-derived by each (claim-provider-pattern effort
    review finding: "Preserve the resolved account through CodeSpace
    reclamation") -- a CodeSpace found only under a non-ambient account
    would otherwise be recovered/deleted with the WRONG credentials."""
    monkeypatch.setattr(cpc, "get_codespace_status_with_account",
                        lambda name: (True, "Available", "acct-nonambient"))
    calls = []
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda name, account=None, **k: calls.append(("sync", account)) or {"ok": True})
    monkeypatch.setattr(cpc, "delete_codespace",
                        lambda name, force=True, account=None, **k: calls.append(("delete", account)))
    monkeypatch.setattr(cpc, "release_lease", lambda name: True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    assert calls == [("sync", "acct-nonambient"), ("delete", "acct-nonambient")]


def test_claim_reclaim_threads_the_exact_minted_token_through(monkeypatch, capsys):
    """The EXACT token minted for the resolved account must be threaded
    through both sync_codespace_sessions and delete_codespace -- never
    letting either independently re-derive (and possibly silently
    ambient-fallback for) credentials moments later (claim-provider-
    pattern effort review finding: "Preserve validated credentials during
    status and reclaim")."""
    monkeypatch.setattr(cpc, "get_codespace_status_with_account",
                        lambda name: (True, "Available", "acct-nonambient"))
    monkeypatch.setattr(
        "agent_codespaces.gh_account.token_for_account",
        lambda login: "exact-minted-token" if login == "acct-nonambient" else None)
    calls = []

    def _fake_sync(name, account=None, token=None, lock=None):
        calls.append(("sync", token))
        return {"ok": True}

    monkeypatch.setattr(cpc, "sync_codespace_sessions", _fake_sync)
    monkeypatch.setattr(cpc, "delete_codespace",
                        lambda name, force=True, account=None, token=None: calls.append(("delete", token)))
    monkeypatch.setattr(cpc, "release_lease", lambda name: True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    assert calls == [("sync", "exact-minted-token"), ("delete", "exact-minted-token")]


def test_claim_reclaim_fails_closed_when_remint_fails_for_resolved_account(monkeypatch, capsys):
    """sync_codespace_sessions/delete_codespace pin to resolved_account via
    the PERMISSIVE gh_account.env_for_account, which silently falls back
    to ambient credentials when it cannot mint a token. Re-verifying
    minting immediately before using them must fail closed (never proceed
    under ambient fallback) when that re-mint fails (claim-provider-pattern
    effort review finding: "Preserve validated credentials during status
    and reclaim")."""
    monkeypatch.setattr(cpc, "get_codespace_status_with_account",
                        lambda name: (True, "Available", "acct-nonambient"))
    monkeypatch.setattr(
        "agent_codespaces.gh_account.token_for_account", lambda login: None)
    called = {"n": 0}
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: called.__setitem__("n", 1))
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "could not mint" in out["detail"]
    assert called["n"] == 0  # never even reached session recovery/delete


def test_claim_reclaim_refuses_when_leased_after_hold_acquired(monkeypatch, capsys):
    """The initial lease check runs before the deploy hold exists at all, so
    a second check still matters after the hold is acquired."""
    lease_calls = {"n": 0}

    def _get_lease(name):
        lease_calls["n"] += 1
        if lease_calls["n"] == 1:
            return None
        return types.SimpleNamespace(effort="late-borrower")

    monkeypatch.setattr(cpc, "get_lease", _get_lease)
    @contextmanager
    def _hold(*_args, **_kwargs):
        yield object()
    monkeypatch.setattr(cpc, "deploy_hold", _hold)
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})
    called = {"n": 0}
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "late-borrower" in out["detail"]
    assert called["n"] == 0
    assert lease_calls["n"] == 2


def test_claim_reclaim_holds_deploy_fence_through_recovery_and_delete(monkeypatch, capsys):
    events = []

    @contextmanager
    def _hold(name, operation):
        hold = types.SimpleNamespace(token="hold-token")
        events.append(("enter", name, operation))
        yield hold
        events.append(("exit", name, operation))

    monkeypatch.setattr(cpc, "deploy_hold", _hold)
    monkeypatch.setattr(
        cpc,
        "verify_deploy_hold",
        lambda name, token: events.append(("verify", name, token))
        or types.SimpleNamespace(expires_at=time.time() + 600),
    )
    monkeypatch.setattr(
        cpc,
        "sync_codespace_sessions",
        lambda name, **k: events.append(("sync", name)) or {"ok": True},
    )
    monkeypatch.setattr(
        cpc,
        "delete_codespace",
        lambda name, **k: events.append(("delete", name)),
    )
    monkeypatch.setattr(
        cpc,
        "release_lease",
        lambda name: events.append(("release", name)) or True,
    )

    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True
    assert events == [
        ("enter", "cs-a", "claim-reclaim"),
        ("sync", "cs-a"),
        ("verify", "cs-a", "hold-token"),
        ("delete", "cs-a"),
        ("release", "cs-a"),
        ("exit", "cs-a", "claim-reclaim"),
    ]


def test_claim_reclaim_reports_busy_deploy_hold(monkeypatch, capsys):
    monkeypatch.setattr(
        cpc,
        "deploy_hold",
        lambda *_a, **_k: (_ for _ in ()).throw(
            cpc.DeployHoldError("CodeSpace 'cs-a' already has a provider claim-reclaim hold")
        ),
    )
    called = {"n": 0}
    monkeypatch.setattr(
        cpc,
        "sync_codespace_sessions",
        lambda *a, **k: called.__setitem__("n", 1),
    )
    monkeypatch.setattr(
        cpc,
        "delete_codespace",
        lambda *a, **k: called.__setitem__("n", 1),
    )

    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False
    assert "already has a provider claim-reclaim hold" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_fails_closed_when_hold_expires_before_delete(monkeypatch, capsys):
    @contextmanager
    def _hold(*_args, **_kwargs):
        yield types.SimpleNamespace(token="hold-token")

    monkeypatch.setattr(cpc, "deploy_hold", _hold)
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        cpc,
        "verify_deploy_hold",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("Provider lifecycle hold for 'cs-a' is no longer owned by this operation")
        ),
    )
    called = {"n": 0}
    monkeypatch.setattr(
        cpc,
        "delete_codespace",
        lambda *a, **k: called.__setitem__("n", 1),
    )

    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False
    assert "admission/hold check failed" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_refuses_to_start_delete_when_hold_budget_is_nearly_exhausted(
    monkeypatch,
    capsys,
):
    @contextmanager
    def _hold(*_args, **_kwargs):
        yield types.SimpleNamespace(token="hold-token")

    monkeypatch.setattr(cpc, "deploy_hold", _hold)
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        cpc,
        "verify_deploy_hold",
        lambda *a, **k: types.SimpleNamespace(
            expires_at=time.time() + cpc._DELETE_TIMEOUT_SECONDS
        ),
    )
    marked = {}
    monkeypatch.setattr(
        cpc,
        "mark_deploy_hold_uncertain",
        lambda name, token: marked.update({"name": name, "token": token}),
    )
    called = {"n": 0}
    monkeypatch.setattr(
        cpc,
        "delete_codespace",
        lambda *a, **k: called.__setitem__("n", 1),
    )

    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False
    assert "hold budget is nearly exhausted" in out["detail"]
    assert marked == {"name": "cs-a", "token": "hold-token"}
    assert called["n"] == 0


def test_claim_reclaim_marks_hold_uncertain_on_ambiguous_delete_failure(monkeypatch, capsys):
    @contextmanager
    def _hold(*_args, **_kwargs):
        yield types.SimpleNamespace(token="hold-token")

    monkeypatch.setattr(cpc, "deploy_hold", _hold)
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        cpc,
        "verify_deploy_hold",
        lambda *a, **k: types.SimpleNamespace(expires_at=time.time() + 600),
    )

    def _boom(*a, **k):
        raise RuntimeError("HTTP 500: server exploded")

    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    marked = {}
    monkeypatch.setattr(
        cpc,
        "mark_deploy_hold_uncertain",
        lambda name, token: marked.update({"name": name, "token": token}),
    )

    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False
    assert marked == {"name": "cs-a", "token": "hold-token"}


def test_claim_reclaim_blocks_delete_when_recovery_fails_and_still_exists(monkeypatch, capsys):
    """A FAILED session recovery must block the delete when the CodeSpace
    genuinely still exists (unattended path, no operator present to notice
    a warn-and-continue): unlike `_cmd_delete`'s human-facing default, this
    callback honors its own docstring's 'never destroys an unrecovered
    session' promise."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "get_codespace_status_with_account",
                        lambda name: (True, "Available", None))
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: {"ok": False, "detail": "connect failed"})
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "recovery failed" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_recovery_failure_confirmed_gone_is_still_idempotent(monkeypatch, capsys):
    """Confirmed absence at the up-front status check resolves as an
    idempotent reclaim immediately -- never spending time on session
    recovery for a resource that's already gone."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "get_codespace_status_with_account",
                        lambda name: (False, None, None))
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: called.__setitem__("n", 1))
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    released = {}
    monkeypatch.setattr(cpc, "release_lease",
                        lambda name: released.setdefault("name", name) or True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]
    assert called["n"] == 0  # neither sync nor delete invoked; confirmed via up-front status lookup
    assert released["name"] == "cs-a"


def test_claim_reclaim_status_lookup_failure_fails_closed(monkeypatch, capsys):
    """An authoritative status-lookup failure must fail closed immediately
    -- never fall through to session recovery/delete under
    ambient/re-derived credentials (claim-provider-pattern effort review
    finding: "Fail closed on lookup errors and return immediately when
    absent")."""
    called = {"n": 0}

    def _boom(name):
        raise RuntimeError("gh CLI not found")
    monkeypatch.setattr(cpc, "get_codespace_status_with_account", _boom)
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: called.__setitem__("n", 1))
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "status lookup failed" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_already_gone_releases_lease_too(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})

    def _boom(*a, **k):
        raise RuntimeError("gh codespace delete failed: HTTP 404: Not Found")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    released = {}
    monkeypatch.setattr(cpc, "release_lease",
                        lambda name: released.setdefault("name", name) or True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]
    assert released["name"] == "cs-a"


def test_claim_reclaim_transient_failure_is_not_reclaimed(monkeypatch, capsys):
    """A DNS/network hiccup ("could not resolve host") must NOT be treated
    as "already gone" -- that would let a live obligation be discarded
    during an outage."""
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})

    def _boom(*a, **k):
        raise RuntimeError("gh codespace delete failed: could not resolve host")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False


def test_claim_reclaim_real_failure(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})

    def _boom(*a, **k):
        raise RuntimeError("HTTP 500: server exploded")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "exploded" in out["detail"]
