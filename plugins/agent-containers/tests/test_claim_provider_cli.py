"""Claim-provider callback tests for agent-containers (claim-provider-pattern
effort): ``claim-status``/``claim-reclaim`` for the ``container:`` namespace
agent-worktrees' claim-provider registry resolves.
"""
from __future__ import annotations

import argparse
import json

from agent_containers import claim_provider_cli as cpc
from agent_containers import lifecycle


def test_claim_status_exists(monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "inspect_state", lambda name: "running")
    rc = cpc.cmd_claim_status(argparse.Namespace(name="c-a"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": True, "state": "running"}


def test_claim_status_absent(monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "inspect_state", lambda name: None)
    rc = cpc.cmd_claim_status(argparse.Namespace(name="c-missing"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": False}


def test_claim_reclaim_dry_run_never_removes(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(lifecycle, "remove_container",
                        lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=False))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "would remove" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_apply_success(monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "remove_container", lambda *a, **k: None)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "removed" in out["detail"]


def test_claim_reclaim_already_gone_is_idempotent(monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("docker rm c-a failed: Error: No such container: c-a")
    monkeypatch.setattr(lifecycle, "remove_container", _boom)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]


def test_claim_reclaim_real_failure(monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("docker rm c-a failed: permission denied")
    monkeypatch.setattr(lifecycle, "remove_container", _boom)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "permission denied" in out["detail"]
