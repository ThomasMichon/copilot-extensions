"""Tests for the ``session-reopen-nudge`` command -- the userPromptSubmit
hook that reactivates a Phase 8 (worktree-finality-and-obligations) session
claim once it has gone non-``active`` (e.g. settled by a prior finalize, or
released by a prior sessionEnd) while the same process keeps talking.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import obligations, session_reopen_nudge_cli, tracking
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


class TestSessionReopenNudgeDecision:
    def test_reactivates_a_settled_claim(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch,
    ):
        _save_record(tmp_tracking_dir, "wt-reopen", "/tmp/src/wt-reopen")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        tracking.register_session("wt-reopen", "sess-r")
        yaml_path = tmp_tracking_dir / "wt-reopen.yaml"
        rec = load_record(yaml_path)
        ref = tracking.format_claim_ref(rec.machine, rec.repo, rec.worktree_id, session="sess-r")
        tracking.settle_resource_claim(rec, ref, disposition=obligations.AT_REST)
        before = next(c for c in load_record(yaml_path).resources if c.ref == ref)
        assert before.state == "at-rest"

        result = session_reopen_nudge_cli._session_reopen_nudge_decision(
            "/tmp/src/wt-reopen", "sess-r",
        )
        assert result == {}
        after = next(c for c in load_record(yaml_path).resources if c.ref == ref)
        assert after.state == obligations.ACTIVE

    def test_noop_when_already_active(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch,
    ):
        _save_record(tmp_tracking_dir, "wt-active", "/tmp/src/wt-active")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        tracking.register_session("wt-active", "sess-a")
        yaml_path = tmp_tracking_dir / "wt-active.yaml"

        assert session_reopen_nudge_cli._session_reopen_nudge_decision(
            "/tmp/src/wt-active", "sess-a",
        ) == {}
        rec = load_record(yaml_path)
        ref = tracking.format_claim_ref(rec.machine, rec.repo, rec.worktree_id, session="sess-a")
        claim = next(c for c in rec.resources if c.ref == ref)
        assert claim.state == "active"

    def test_noop_for_a_session_not_on_the_record(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch,
    ):
        _save_record(tmp_tracking_dir, "wt-unknown", "/tmp/src/wt-unknown")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)

        assert session_reopen_nudge_cli._session_reopen_nudge_decision(
            "/tmp/src/wt-unknown", "sess-never-registered",
        ) == {}
        rec = load_record(tmp_tracking_dir / "wt-unknown.yaml")
        assert rec.resources == []

    def test_noop_for_untracked_cwd(self, monkeypatch_config, monkeypatch):
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        assert session_reopen_nudge_cli._session_reopen_nudge_decision(
            "/tmp/not/a/worktree", "sess-x",
        ) == {}

    def test_noop_without_a_session_id(self, monkeypatch_config):
        assert session_reopen_nudge_cli._session_reopen_nudge_decision(
            "/tmp/src/anything", None,
        ) == {}


class TestSessionReopenNudgeCmd:
    def test_cmd_always_emits_empty_object(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys,
    ):
        _save_record(tmp_tracking_dir, "wt-nudge-cmd", "/tmp/src/wt-nudge-cmd")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        tracking.register_session("wt-nudge-cmd", "sess-cmd")
        rc = m.cmd_session_reopen_nudge(
            argparse.Namespace(session_id="sess-cmd", cwd="/tmp/src/wt-nudge-cmd", stdin=False)
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_cmd_reads_stdin_payload(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys,
    ):
        _save_record(tmp_tracking_dir, "wt-nudge-stdin", "/tmp/src/wt-nudge-stdin")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        tracking.register_session("wt-nudge-stdin", "sess-stdin")
        monkeypatch.setattr(
            m, "_read_hook_stdin",
            lambda: {"sessionId": "sess-stdin", "cwd": "/tmp/src/wt-nudge-stdin"},
        )
        rc = m.cmd_session_reopen_nudge(
            argparse.Namespace(session_id=None, cwd=None, stdin=True)
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_cmd_falls_back_to_env_session_id(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys,
    ):
        _save_record(tmp_tracking_dir, "wt-nudge-env", "/tmp/src/wt-nudge-env")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        tracking.register_session("wt-nudge-env", "sess-env")
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-env")
        rc = m.cmd_session_reopen_nudge(
            argparse.Namespace(session_id=None, cwd="/tmp/src/wt-nudge-env", stdin=False)
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"
