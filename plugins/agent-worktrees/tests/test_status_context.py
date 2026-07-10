"""Tests for the `status-context` left status-bar segment."""

from __future__ import annotations

import argparse

from agent_worktrees import __main__ as m
from agent_worktrees import tracking


def _ns(**kw):
    base = {"path": None, "plain": True}
    base.update(kw)
    return argparse.Namespace(**base)


def _record(**kw):
    base = dict(
        worktree_id="lambda-core-win-20260625-221940-8e45",
        branch="worktree/lambda-core-win-20260625-221940-8e45",
        worktree_path="/w/lambda-core-win-20260625-221940-8e45",
        repo="aperture-labs",
        machine="lambda-core",
        platform="windows",
        started_at="",
        last_resumed_at="",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
    )
    base.update(kw)
    return tracking.WorktreeRecord(**base)


def test_status_context_registered():
    assert m.COMMAND_MAP["status-context"] is m.cmd_status_context
    assert m._WORKTREE_VERBS["status-context"] == "status-context"


def test_platform_short_mapping():
    assert m._platform_short("windows") == "win"
    assert m._platform_short("wsl") == "wsl"
    assert m._platform_short("linux") == "linux"


def test_status_context_plain_with_record(monkeypatch, capsys):
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: _record())
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    assert capsys.readouterr().out.strip() == "lambda-core  win  aperture-labs:8e45"


def test_status_context_styled_with_record(monkeypatch, capsys):
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: _record())
    rc = m.cmd_status_context(_ns(plain=False))
    assert rc == 0
    out = capsys.readouterr().out
    # Identity values present, wrapped in tmux style directives.
    assert "lambda-core" in out
    assert "win" in out
    assert "aperture-labs:8e45" in out
    assert "#[fg=" in out and "#[default]" in out
    # Environment renders as an OS-colored badge; no pipe delimiters.
    assert f"bg={m._ENV_BG['win']}" in out
    assert "|" not in out


def test_status_context_fallback_no_record(monkeypatch, capsys):
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: None)
    monkeypatch.setattr(m.cfg, "detect_machine", lambda *a, **k: "borealis")
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "wsl")
    rc = m.cmd_status_context(_ns())
    assert rc == 0
    # No record -> machine + env only, repo:id4 omitted.
    assert capsys.readouterr().out.strip() == "borealis  wsl"
