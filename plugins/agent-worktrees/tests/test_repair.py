"""Tests for the ``repair`` subcommand (in-place local-state reconcile)."""
from __future__ import annotations

import argparse

from agent_worktrees import __main__ as m


def _args() -> argparse.Namespace:
    return argparse.Namespace()


def _wire(monkeypatch):
    calls = {"binstubs": 0}
    monkeypatch.setattr(m.inst, "reconcile_binstubs",
                        lambda: calls.__setitem__("binstubs", calls["binstubs"] + 1))
    return calls


def test_repair_reconciles_binstubs(monkeypatch):
    calls = _wire(monkeypatch)
    assert m.cmd_repair(_args()) == 0
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
