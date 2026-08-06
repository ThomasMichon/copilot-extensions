"""Tests for the ``repair`` subcommand (in-place local-state reconcile).

``cmd_repair`` reconciles *deployed* state without touching the plugin/runtime
version: it redeploys project binstubs and regenerates the Windows Terminal
fragment (heal hidden profiles + reclaim our orphaned generatedProfiles). It is
the version-independent counterpart to ``update`` -- the right tool when the
dropdown or a binstub is wrong but the runtime is already current.
"""
from __future__ import annotations

import argparse

from agent_worktrees import __main__ as m


def _args(terminal: bool = False, binstubs: bool = False) -> argparse.Namespace:
    return argparse.Namespace(terminal=terminal, binstubs=binstubs)


def _wire(monkeypatch, *, system="Windows", refresh_ok=True):
    calls = {"binstubs": 0, "refresh": 0}
    monkeypatch.setattr(m.inst, "reconcile_binstubs",
                        lambda: calls.__setitem__("binstubs", calls["binstubs"] + 1))
    monkeypatch.setattr(
        m, "_refresh_terminal_profiles",
        lambda: (calls.__setitem__("refresh", calls["refresh"] + 1) or refresh_ok))
    monkeypatch.setattr(m.platform, "system", lambda: system)
    # Neutralize the read-only diagnosis so the command doesn't touch real WT.
    from agent_worktrees import terminal_fragment as tf
    monkeypatch.setattr(tf, "diagnose_wt_state", lambda: None)
    return calls


def test_default_repairs_both(monkeypatch):
    calls = _wire(monkeypatch)
    assert m.cmd_repair(_args()) == 0
    assert calls["binstubs"] == 1
    assert calls["refresh"] == 1


def test_terminal_only(monkeypatch):
    calls = _wire(monkeypatch)
    assert m.cmd_repair(_args(terminal=True)) == 0
    assert calls["binstubs"] == 0
    assert calls["refresh"] == 1


def test_binstubs_only(monkeypatch):
    calls = _wire(monkeypatch)
    assert m.cmd_repair(_args(binstubs=True)) == 0
    assert calls["binstubs"] == 1
    assert calls["refresh"] == 0


def test_terminal_repair_skipped_off_windows(monkeypatch):
    calls = _wire(monkeypatch, system="Linux")
    assert m.cmd_repair(_args(terminal=True)) == 0
    # Windows-only: the fragment refresh never runs on Linux.
    assert calls["refresh"] == 0


def test_nonzero_exit_when_refresh_fails(monkeypatch):
    _wire(monkeypatch, refresh_ok=False)
    assert m.cmd_repair(_args(terminal=True)) == 1


def test_binstub_failure_does_not_abort_terminal(monkeypatch):
    calls = _wire(monkeypatch)

    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(m.inst, "reconcile_binstubs", _boom)
    # Both targets requested; binstub failure is reported (rc 1) but the terminal
    # repair still runs.
    assert m.cmd_repair(_args()) == 1
    assert calls["refresh"] == 1


def test_repair_registered_in_dispatch_and_parser():
    assert "repair" in m.COMMAND_MAP
    parser = m.build_parser()
    ns = parser.parse_args(["repair", "--terminal"])
    assert ns.command == "repair"
    assert ns.terminal is True and ns.binstubs is False
