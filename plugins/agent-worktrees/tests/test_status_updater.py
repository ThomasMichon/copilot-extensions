"""Tests for the `status-updater` background loop and the render helpers.

The updater moves the status-bar work off psmux's paint path: instead of the
bar polling ``#(agent-worktrees ...)`` (a process spawn per render, which
psmux runs synchronously), a detached loop renders in-process and pushes the
result into session options ``@aw_ctx`` (identity, once) and ``@aw_seg``
(disposition, on an interval).  These tests drive the loop with a fake mux
binary so no real psmux/tmux is required.
"""

from __future__ import annotations

import argparse
import subprocess
import time

from agent_worktrees import __main__ as m


def _ns(**kw):
    base = {"session": "wt-test", "mux": "psmux", "path": "/w/x", "interval": 5}
    base.update(kw)
    return argparse.Namespace(**base)


def test_status_updater_registered():
    assert m.COMMAND_MAP["status-updater"] is m.cmd_status_updater
    assert m._WORKTREE_VERBS["status-updater"] == "status-updater"


def test_render_helpers_back_the_print_wrappers(monkeypatch, capsys):
    """The cmd_* wrappers must print exactly what the renderers return."""
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTXLINE")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEGLINE")

    assert m.cmd_status_context(argparse.Namespace(path=None, plain=False)) == 0
    assert capsys.readouterr().out.strip() == "CTXLINE"

    assert m.cmd_status_segment(
        argparse.Namespace(path=None, fetch=False, plain=False, no_title=False)
    ) == 0
    assert capsys.readouterr().out.strip() == "SEGLINE"


def test_render_wrapper_prints_nothing_when_empty(monkeypatch, capsys):
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "")
    rc = m.cmd_status_segment(
        argparse.Namespace(path=None, fetch=False, plain=False, no_title=False)
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


def _fake_mux(has_session_codes, calls):
    """Build a fake subprocess.run for the mux binary.

    ``has_session_codes`` is consumed one return-code per ``has-session``
    call; ``set-option`` invocations are recorded into ``calls`` as
    ``(option, value)`` tuples.
    """
    codes = iter(has_session_codes)

    def fake_run(argv, **_kw):
        verb = argv[1]
        if verb == "has-session":
            return subprocess.CompletedProcess(argv, next(codes, 1), "", "")
        if verb == "set-option":
            # argv == [bin, set-option, -t, <sess>, <opt>, <val>]
            calls.append((argv[4], argv[5]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return fake_run


def test_status_updater_sets_ctx_once_then_seg_until_gone(monkeypatch):
    calls: list[tuple[str, str]] = []
    # present (initial guard), present (loop iter 1), gone (loop iter 2).
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], calls))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # Identity pushed exactly once, first.
    assert calls[0] == ("@aw_ctx", "CTX")
    assert [c for c in calls if c[0] == "@aw_ctx"] == [("@aw_ctx", "CTX")]
    # Disposition pushed at least once.
    assert ("@aw_seg", "SEG") in calls


def test_status_updater_noop_when_session_absent(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([1], calls))  # gone at start
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert calls == []  # never set any option for a dead session


def test_status_updater_requires_session():
    assert m.cmd_status_updater(_ns(session="")) == 2


def test_status_updater_survives_render_errors(monkeypatch):
    """A transient render exception must not kill the loop or leak out."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], calls))

    def boom(*_a, **_k):
        raise RuntimeError("git hiccup")

    monkeypatch.setattr(m, "_render_status_context", boom)
    monkeypatch.setattr(m, "_render_status_segment", boom)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # ctx render raised -> no @aw_ctx; seg render raised -> empty @aw_seg set.
    assert ("@aw_seg", "") in calls
