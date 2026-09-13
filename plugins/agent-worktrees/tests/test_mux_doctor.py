"""Tests for :mod:`mux_doctor` -- cutover mux-state verification + repair.

A handoff cutover isn't complete just because a spawn succeeded; the
operator's console tab must actually show the successor pane. These tests
cover the three outcomes: already current (no-op), not current but
successfully repaired (doctored), and not current and unrepairable (a
failed cutover, never silently accepted).
"""

from __future__ import annotations

import subprocess

from agent_worktrees import mux_doctor, sessions


def test_already_current_is_a_noop(monkeypatch):
    monkeypatch.setattr(sessions, "mux_active_pane_named", lambda name, **k: "%5")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not attempt a repair when already current")
        ),
    )
    out = mux_doctor.verify_and_doctor_current_pane("%5", "wt-abc")
    assert out == {
        "ok": True, "was_current": True, "doctored": False, "active_pane": "%5",
    }


def test_not_current_is_doctored_and_reverified(monkeypatch):
    # First check: wrong pane active. No attached clients -> falls back to
    # select-window/select-pane, both succeed. Second check: pane is active.
    active_calls = iter(["%3", "%5"])
    monkeypatch.setattr(
        sessions, "mux_active_pane_named",
        lambda name, **k: next(active_calls),
    )

    class _Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    out = mux_doctor.verify_and_doctor_current_pane("%5", "wt-abc")
    assert out == {
        "ok": True, "was_current": False, "doctored": True, "active_pane": "%5",
    }


def test_attached_client_is_repaired_via_switch_client(monkeypatch):
    # An attached client is targeted precisely with switch-client -c <tty>,
    # not just the session-level select-window/select-pane.
    active_calls = iter(["%3", "%5"])
    monkeypatch.setattr(
        sessions, "mux_active_pane_named",
        lambda name, **k: next(active_calls),
    )
    calls = []

    def _run(argv, **k):
        calls.append(argv)

        class _Result:
            returncode = 0
            stdout = "/dev/pts/7\n" if argv[1] == "list-clients" else ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)
    out = mux_doctor.verify_and_doctor_current_pane("%5", "wt-abc")
    assert out["ok"] is True
    assert ["tmux", "switch-client", "-c", "/dev/pts/7", "-t", "%5"] in calls
    assert not any(argv[1] == "select-window" for argv in calls)


def test_doctor_failure_reports_unrepaired(monkeypatch):
    # Wrong pane active, repair commands fail, and it stays wrong.
    monkeypatch.setattr(sessions, "mux_active_pane_named", lambda name, **k: "%3")

    class _Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Failed())
    out = mux_doctor.verify_and_doctor_current_pane("%5", "wt-abc")
    assert out["ok"] is False
    assert out["was_current"] is False
    assert out["active_pane"] == "%3"


def test_select_commands_raising_is_treated_as_unrepaired(monkeypatch):
    monkeypatch.setattr(sessions, "mux_active_pane_named", lambda name, **k: "%3")

    def _boom(*a, **k):
        raise OSError("mux binary not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    out = mux_doctor.verify_and_doctor_current_pane("%5", "wt-abc")
    assert out["ok"] is False
    assert out["doctored"] is False
