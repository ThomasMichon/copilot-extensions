"""Claim-provider callback tests for agent-codespaces (claim-provider-pattern
effort): ``claim-status``/``claim-reclaim`` for the ``codespace:`` namespace
agent-worktrees' claim-provider registry resolves.
"""
from __future__ import annotations

import argparse
import json
import types

from agent_codespaces import claim_provider_cli as cpc


def _cs(name, state="Available"):
    return types.SimpleNamespace(name=name, state=state)


def test_claim_status_exists(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "list_codespaces", lambda: [_cs("cs-a"), _cs("cs-b", "Shutdown")])
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-b"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": True, "state": "Shutdown"}


def test_claim_status_absent(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "list_codespaces", lambda: [_cs("cs-a")])
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-missing"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": False}


def test_claim_status_listing_failure_is_not_false_absence(monkeypatch, capsys):
    """A listing failure (gh/network/auth trouble) must exit non-zero -- never
    a false ``exists: false`` that could make a live claim look reclaimable."""
    def _boom():
        raise RuntimeError("gh CLI not found")
    monkeypatch.setattr(cpc, "list_codespaces", _boom)
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-a"))
    assert rc != 0
    assert capsys.readouterr().out == ""


def test_claim_reclaim_dry_run_never_deletes(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=False), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "would delete" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_apply_recovers_sessions_before_deleting(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cpc, "sync_codespace_sessions",
                        lambda name, **k: calls.append(("sync", name)) or {"ok": True})
    monkeypatch.setattr(cpc, "delete_codespace",
                        lambda name, **k: calls.append(("delete", name)))
    released = {}
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True),
        release_lease_quietly=lambda n: released.setdefault("name", n))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "deleted" in out["detail"]
    assert calls == [("sync", "cs-a"), ("delete", "cs-a")]
    assert released["name"] == "cs-a"


def test_claim_reclaim_apply_success(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: None)
    released = {}
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True),
        release_lease_quietly=lambda n: released.setdefault("name", n))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "deleted" in out["detail"]
    assert released["name"] == "cs-a"


def test_claim_reclaim_already_gone_is_idempotent(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})

    def _boom(*a, **k):
        raise RuntimeError("gh codespace delete failed: HTTP 404: Not Found")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]


def test_claim_reclaim_transient_failure_is_not_reclaimed(monkeypatch, capsys):
    """A DNS/network hiccup ("could not resolve host") must NOT be treated
    as "already gone" -- that would let a live obligation be discarded
    during an outage."""
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})

    def _boom(*a, **k):
        raise RuntimeError("gh codespace delete failed: could not resolve host")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False


def test_claim_reclaim_real_failure(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "sync_codespace_sessions", lambda *a, **k: {"ok": True})

    def _boom(*a, **k):
        raise RuntimeError("HTTP 500: server exploded")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "exploded" in out["detail"]
