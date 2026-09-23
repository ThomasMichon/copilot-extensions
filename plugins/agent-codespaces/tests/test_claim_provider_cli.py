"""Claim-provider callback tests for agent-codespaces (claim-provider-pattern
effort): ``claim-status``/``claim-reclaim`` for the ``codespace:`` namespace
agent-worktrees' claim-provider registry resolves.
"""
from __future__ import annotations

import argparse
import json
import types

from agent_codespaces import claim_provider_cli as cpc

import pytest


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
                        lambda name, account=None: calls.append(("sync", account)) or {"ok": True})
    monkeypatch.setattr(cpc, "delete_codespace",
                        lambda name, force=True, account=None: calls.append(("delete", account)))
    monkeypatch.setattr(cpc, "release_lease", lambda name: True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    assert calls == [("sync", "acct-nonambient"), ("delete", "acct-nonambient")]


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


def test_claim_reclaim_refuses_when_leased_during_recovery(monkeypatch, capsys):
    """Narrows (does not eliminate -- there is no atomic fence primitive)
    the window between the initial lease check and the destructive delete:
    another effort can legitimately acquire the CodeSpace during the
    (potentially minutes-long) session-recovery step -- re-verify
    immediately before the point of no return."""
    lease_calls = {"n": 0}

    def _get_lease(name):
        lease_calls["n"] += 1
        if lease_calls["n"] == 1:
            return None  # free at the initial check
        return types.SimpleNamespace(effort="late-borrower")  # acquired during recovery

    monkeypatch.setattr(cpc, "get_lease", _get_lease)
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})
    called = {"n": 0}
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "late-borrower" in out["detail"]
    assert called["n"] == 0
    assert lease_calls["n"] == 2


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
