"""Claim-provider callback tests for agent-codespaces (claim-provider-pattern
effort): ``claim-status``/``claim-reclaim`` for the ``codespace:`` namespace
agent-worktrees' claim-provider registry resolves.
"""
from __future__ import annotations

import argparse
import json

from agent_codespaces import claim_provider_cli as cpc


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


def test_claim_reclaim_blocks_delete_when_recovery_fails_and_still_exists(monkeypatch, capsys):
    """A FAILED session recovery must block the delete when the CodeSpace
    genuinely still exists (unattended path, no operator present to notice
    a warn-and-continue): unlike `_cmd_delete`'s human-facing default, this
    callback honors its own docstring's 'never destroys an unrecovered
    session' promise."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: {"ok": False, "detail": "connect failed"})
    monkeypatch.setattr(cpc, "get_codespace_status", lambda name: (True, "Available"))
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "recovery failed" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_recovery_failure_confirmed_gone_is_still_idempotent(monkeypatch, capsys):
    """Recovery failing because the CodeSpace is ALREADY gone (nothing to
    connect to) must still resolve as an idempotent reclaim, not a refusal."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: {"ok": False, "detail": "connect failed"})
    monkeypatch.setattr(cpc, "get_codespace_status", lambda name: (False, None))
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    released = {}
    monkeypatch.setattr(cpc, "release_lease",
                        lambda name: released.setdefault("name", name) or True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]
    assert called["n"] == 0  # delete_codespace never invoked; confirmed via status lookup
    assert released["name"] == "cs-a"


def test_claim_reclaim_recovery_failure_ambiguous_status_refuses(monkeypatch, capsys):
    """If the existence check itself fails (ambiguous), refuse rather than
    guess -- never treat an indeterminate state as confirmed absence."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda *a, **k: {"ok": False, "detail": "connect failed"})

    def _boom(name):
        raise RuntimeError("gh CLI not found")
    monkeypatch.setattr(cpc, "get_codespace_status", _boom)
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="cs-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False
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
