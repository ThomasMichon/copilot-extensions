"""Tests for the bind-session command -- the agent's explicit worktree declaration.

Unlike register-session (the sessionStart hook, which reads a stdin payload),
bind-session is a deliberate tool-subprocess verb: the agent runs it from inside
its worktree to *declare* ownership when it did not begin life there (a bare/HOME
resume, a spawned cutover successor, an ACP/STDIO launch). It self-identifies the
session from COPILOT_AGENT_SESSION_ID and the pane from TMUX_PANE/PSMUX_PANE, and
folds into the same idempotent tracking.register_session the hook uses.
"""

from __future__ import annotations

import argparse
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
        sessions=[],
    )
    save_record(rec, tracking_dir / f"{wt_id}.yaml")


def _args(**kw) -> argparse.Namespace:
    base = dict(worktree_dir=None, worktree_id=None, session_id=None, pane=None, pid=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _neutralize(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
    monkeypatch.setattr(m, "_spawn_status_updater", lambda wt, path: True)
    monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))


class TestBindSession:
    def test_binds_from_worktree_dir(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-a")
        monkeypatch.setenv("TMUX_PANE", "%3")

        rc = m.cmd_bind_session(_args(worktree_dir="/tmp/src/wt-a/sub"))
        assert rc == 0

        rec = load_record(tmp_tracking_dir / "wt-a.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-a"]
        assert rec.sessions[0].pane_id == "%3"
        assert captured["bound"] is True
        assert captured["worktree_id"] == "wt-a"
        assert captured["head_session"] == "sess-a"

    def test_session_id_and_pane_flags_win_over_env(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-b", "/tmp/src/wt-b")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "env-sess")

        rc = m.cmd_bind_session(
            _args(worktree_dir="/tmp/src/wt-b", session_id="flag-sess", pane="%9")
        )
        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-b.yaml")
        assert [s.session_id for s in rec.sessions] == ["flag-sess"]
        assert rec.sessions[0].pane_id == "%9"

    def test_worktree_id_takes_precedence_over_dir(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-c", "/tmp/src/wt-c")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda wid: wid)

        rc = m.cmd_bind_session(
            _args(worktree_id="wt-c", worktree_dir="/tmp/unrelated", session_id="sess-c")
        )
        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-c.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-c"]

    def test_idempotent_rebind_updates_not_duplicates(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-d", "/tmp/src/wt-d")
        captured: dict = {}
        _neutralize(monkeypatch, captured)

        m.cmd_bind_session(_args(worktree_dir="/tmp/src/wt-d", session_id="sess-d", pane="%1"))
        m.cmd_bind_session(_args(worktree_dir="/tmp/src/wt-d", session_id="sess-d", pane="%2"))

        rec = load_record(tmp_tracking_dir / "wt-d.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-d"]
        assert rec.sessions[0].pane_id == "%2"

    def test_no_session_id_errors_exit_2(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-e", "/tmp/src/wt-e")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)

        rc = m.cmd_bind_session(_args(worktree_dir="/tmp/src/wt-e"))
        assert rc == 2

    def test_untracked_dir_errors_exit_3(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-f")

        rc = m.cmd_bind_session(_args(worktree_dir="/tmp/not/a/worktree"))
        assert rc == 3


class TestBindNudgeDecision:
    def test_no_head_fires(self):
        from agent_worktrees.tracking import WorktreeRecord

        rec = WorktreeRecord(
            worktree_id="wt-n", branch="b", worktree_path="/w", repo="r",
            machine="m", platform="wsl", started_at="t", last_resumed_at="t",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[],
        )
        # fresh worktree, no sessions -> no head -> nudge
        assert m._bind_nudge_should_fire(rec) is True

    def test_bound_head_quiet(self):
        from agent_worktrees.tracking import WorktreeRecord, SessionEntry

        rec = WorktreeRecord(
            worktree_id="wt-o", branch="b", worktree_path="/w", repo="r",
            machine="m", platform="wsl", started_at="t", last_resumed_at="t",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[SessionEntry(session_id="s1", started_at="t")],
            head_session="s1",
        )
        assert m._bind_nudge_should_fire(rec) is False

    def test_none_record_quiet(self):
        assert m._bind_nudge_should_fire(None) is False


class TestBindNudgeCmd:
    def test_untracked_cwd_emits_empty(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        rc = m.cmd_bind_nudge(argparse.Namespace(cwd="/tmp/not/a/wt", stdin=False))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_unbound_worktree_nudges_then_cooldown(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        _save_record(tmp_tracking_dir, "wt-p", "/tmp/src/wt-p")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        rc = m.cmd_bind_nudge(argparse.Namespace(cwd="/tmp/src/wt-p", stdin=False))
        assert rc == 0
        out1 = capsys.readouterr().out
        assert "bind-session --worktree-dir=/tmp/src/wt-p" in out1
        assert "additionalContext" in out1
        # Second call within cooldown -> quiet
        rc = m.cmd_bind_nudge(argparse.Namespace(cwd="/tmp/src/wt-p", stdin=False))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_bound_worktree_quiet(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        from agent_worktrees.tracking import WorktreeRecord, SessionEntry, save_record

        rec = WorktreeRecord(
            worktree_id="wt-q", branch="b", worktree_path="/tmp/src/wt-q",
            repo="r", machine="m", platform="wsl", started_at="t",
            last_resumed_at="t", resume_count=0, title=None, status="active",
            completed_at=None,
            sessions=[SessionEntry(session_id="s1", started_at="t")],
            head_session="s1",
        )
        save_record(rec, tmp_tracking_dir / "wt-q.yaml")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        rc = m.cmd_bind_nudge(argparse.Namespace(cwd="/tmp/src/wt-q", stdin=False))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"
