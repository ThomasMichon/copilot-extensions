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


def test_claim_status_list_failure_degrades(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("gh CLI not found")
    monkeypatch.setattr(cpc, "list_codespaces", _boom)
    rc = cpc.cmd_claim_status(argparse.Namespace(name="cs-a"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is False and "gh CLI not found" in out["detail"]


def test_claim_reclaim_dry_run_never_deletes(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(cpc, "delete_codespace", lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=False), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "would delete" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_apply_success(monkeypatch, capsys):
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
    def _boom(*a, **k):
        raise RuntimeError("gh codespace delete failed: HTTP 404: Not Found")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]


def test_claim_reclaim_real_failure(monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("HTTP 500: server exploded")
    monkeypatch.setattr(cpc, "delete_codespace", _boom)
    rc = cpc.cmd_claim_reclaim(
        argparse.Namespace(name="cs-a", apply=True), release_lease_quietly=lambda n: None)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "exploded" in out["detail"]
