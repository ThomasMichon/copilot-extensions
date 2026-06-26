"""Tests for the turn-count refinement of the `status-segment` block."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import git_ops, sessions, tracking


def _record(**kw):
    base = dict(
        worktree_id="lambda-core-win-20260625-221940-8e45",
        branch="worktree/lambda-core-win-20260625-221940-8e45",
        worktree_path="/w/wt",
        repo="aperture-labs",
        machine="lambda-core",
        platform="windows",
        started_at="",
        last_resumed_at="",
        resume_count=0,
        title="",
        status="active",
        completed_at=None,
        handoff_prompt=None,
    )
    base.update(kw)
    return tracking.WorktreeRecord(**base)


def _ns(target):
    return argparse.Namespace(path=target, fetch=False, plain=True,
                              no_title=True)


def _wire(monkeypatch, target, *, state, turns):
    info = git_ops.WorktreeStateInfo(state=state)
    rec = _record(worktree_path=target)
    monkeypatch.setattr(m, "_detect_upstream_branch", lambda *a, **k: "master")
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: rec)
    monkeypatch.setattr(m.git_ops, "classify_worktree", lambda *a, **k: info)
    monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)
    ctx = sessions.SessionContext()
    if turns:
        ctx.turn_count[m._normalize_path(target)] = turns
    monkeypatch.setattr(m.sessions, "scan_sessions_fast", lambda recs: ctx)


def test_unused_with_turns_renders_convo(monkeypatch, capsys):
    target = str(Path("wt-x").resolve())
    _wire(monkeypatch, target, state=git_ops.WorktreeState.UNUSED, turns=7)
    rc = m.cmd_status_segment(_ns(target))
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "CONVO" in out
    assert "7" in out
    assert "UNUSED" not in out


def test_unused_without_turns_stays_unused(monkeypatch, capsys):
    target = str(Path("wt-y").resolve())
    _wire(monkeypatch, target, state=git_ops.WorktreeState.UNUSED, turns=0)
    rc = m.cmd_status_segment(_ns(target))
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "UNUSED" in out
    assert "CONVO" not in out


def test_turns_do_not_override_dirty(monkeypatch, capsys):
    # CONVO only refines UNUSED; a worktree with real git state is unaffected.
    target = str(Path("wt-z").resolve())
    _wire(monkeypatch, target, state=git_ops.WorktreeState.DIRTY, turns=12)
    rc = m.cmd_status_segment(_ns(target))
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "DIRTY" in out
    assert "CONVO" not in out
