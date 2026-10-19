"""Tests for the ``repair`` subcommand (in-place local-state reconcile)."""
from __future__ import annotations

import argparse

import pytest

from agent_worktrees import __main__ as m

pytestmark = pytest.mark.guard


def _args() -> argparse.Namespace:
    return argparse.Namespace()


def _wire(monkeypatch, blocked):
    calls = {"binstubs": 0}

    def reconcile():
        calls["binstubs"] += 1
        return {"blocked": blocked}

    monkeypatch.setattr(m.inst, "reconcile_binstubs", reconcile)
    return calls


@pytest.mark.parametrize(("blocked", "exit_code"), [([], 0), (["example"], 1)], ids=["ready", "blocked"])
def test_repair_reconciles_binstubs(monkeypatch, blocked, exit_code):
    calls = _wire(monkeypatch, blocked)
    assert m.cmd_repair(_args()) == exit_code
    assert calls["binstubs"] == 1


def test_binstub_failure_returns_nonzero(monkeypatch):
    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(m.inst, "reconcile_binstubs", _boom)
    assert m.cmd_repair(_args()) == 1


def test_repair_registered_in_dispatch_and_parser():
    assert "repair" in m.COMMAND_MAP
    parser = m.build_parser()
    ns = parser.parse_args(["repair"])
    assert ns.command == "repair"
