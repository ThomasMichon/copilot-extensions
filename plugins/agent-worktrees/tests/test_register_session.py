"""Tests for the register-session command (sessionStart hook entrypoint).

The Copilot CLI delivers session info to the sessionStart hook as a JSON
payload on stdin (COPILOT_AGENT_SESSION_ID is not reliably set in the hook
environment), so the command must read --stdin and resolve the worktree
from the payload cwd.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees.tracking import WorktreeRecord, load_record, save_record


def _save_record(tracking_dir: Path, wt_id: str, wt_path: str) -> None:
    rec = WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="test-repo",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        handoff_prompt=None,
        sessions=[],
    )
    save_record(rec, tracking_dir / f"{wt_id}.yaml")


def _args(**kw) -> argparse.Namespace:
    base = dict(worktree_id=None, session_id=None, cwd=None, stdin=False, pid=None)
    base.update(kw)
    return argparse.Namespace(**base)


class TestRegisterSessionStdin:
    def test_resolves_worktree_from_stdin_cwd(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-x", "/tmp/src/wt-x")
        payload = '{"sessionId":"sess-1","cwd":"/tmp/src/wt-x/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))
        assert rc == 0

        rec = load_record(tmp_tracking_dir / "wt-x.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-1"]

    def test_explicit_worktree_id_takes_precedence(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-y", "/tmp/src/wt-y")
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        rc = m.cmd_register_session(
            _args(worktree_id="wt-y", session_id="sess-2", stdin=True)
        )
        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-y.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-2"]

    def test_unknown_cwd_is_silent_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
        rc = m.cmd_register_session(_args(stdin=True))
        assert rc == 0  # silent no-op, never an error
        rec = load_record(tmp_tracking_dir / "wt-z.yaml")
        assert rec.sessions == []

    def test_no_session_id_is_silent_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
        rc = m.cmd_register_session(_args(worktree_id="wt-none", stdin=True))
        assert rc == 0
