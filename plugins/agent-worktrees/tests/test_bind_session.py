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
from agent_worktrees import tracking
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
    base = dict(
        worktree_dir=None,
        worktree_id=None,
        session_id=None,
        pane=None,
        pid=None,
        handoff_token=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _neutralize(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
    monkeypatch.setattr(m, "_spawn_status_updater", lambda wt, path: True)
    monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))


class TestBindSession:
    def test_acknowledges_session_start_candidate_atomically(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-ack", "/tmp/src/wt-ack")
        tracking.register_session("wt-ack", "old")
        rec = load_record(tmp_tracking_dir / "wt-ack.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        tracking.register_session("wt-ack", "new")
        rec = load_record(tmp_tracking_dir / "wt-ack.yaml")
        tracking.associate_handoff_candidate(rec, "task-123", "new")
        captured: dict = {}
        _neutralize(monkeypatch, captured)

        rc = m.cmd_bind_session(_args(
            worktree_dir="/tmp/src/wt-ack",
            session_id="new",
            handoff_token="task-123",
        ))

        assert rc == 0
        assert captured["candidate_before_ack"] == "new"
        assert captured["candidate_acknowledged"] is True
        assert captured["head_session"] == "new"

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
        monkeypatch.setattr(
            m, "_activate_project_for_worktree_id", lambda _wt: "test-project"
        )
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

        m.cmd_bind_session(
            _args(
                worktree_dir="/tmp/src/wt-d",
                session_id="sess-d",
                pane="%1",
                pid=123,
            )
        )
        m.cmd_bind_session(_args(worktree_dir="/tmp/src/wt-d", session_id="sess-d", pane="%2"))

        rec = load_record(tmp_tracking_dir / "wt-d.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-d"]
        assert rec.sessions[0].pane_id == "%2"
        assert rec.sessions[0].pid == 123

    def test_worktree_id_activates_owning_project(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-direct", "/tmp/src/wt-direct")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        activated: list[str] = []
        m.cfg.set_active_project(None)
        monkeypatch.setattr(
            m,
            "_activate_project_for_worktree_id",
            lambda wt: activated.append(wt) or "test-project",
        )
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda wt: wt)

        assert m.cmd_bind_session(
            _args(worktree_id="wt-direct", session_id="sess-direct")
        ) == 0
        assert activated == ["wt-direct"]

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


class TestHistoryDigestCmd:
    def test_digest_cmd_activates_project_and_prints(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        # Regression: history-digest is a _NO_PROJECT_COMMANDS verb, so it must
        # activate project context from cwd itself -- otherwise tracking_dir()
        # resolves wrong and the history reads back empty (caught in live verify).
        import agent_worktrees.disposition_history as dh

        _save_record(tmp_tracking_dir, "wt-h", "/tmp/src/wt-h")
        dh.append("wt-h", at="2026-01-01T00:00:01", summary="did work",
                  title=None, follow_up=False, changed=["summary"],
                  session_id="sess-h")
        activated = {}
        monkeypatch.setattr(m, "_activate_project_for_path",
                            lambda c: activated.update(cwd=c))
        monkeypatch.setattr(m, "_infer_worktree_id", lambda wid, cfg=None: "wt-h")
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda wid: wid)

        rc = m.cmd_history_digest(
            argparse.Namespace(worktree_id=None, limit=8)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "recent history" in out
        assert "did work" in out
        assert activated.get("cwd")  # project WAS activated from cwd

    def test_digest_cmd_empty_when_no_worktree(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m, "_infer_worktree_id", lambda wid, cfg=None: None)
        rc = m.cmd_history_digest(argparse.Namespace(worktree_id=None, limit=8))
        assert rc == 0
        assert capsys.readouterr().out.strip() == ""


class TestNoteHandoff:
    def test_appends_session_tagged_handoff_entry(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        import agent_worktrees.disposition_history as dh

        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m, "find_worktree_id_by_cwd", lambda c: "wt-hd", raising=False)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        captured = {}
        monkeypatch.setattr(m, "_json_output", lambda o: captured.update(o))
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-pred")

        rc = m.cmd_note_handoff(argparse.Namespace(
            task="task123", title="Fix the widget",
            worktree_dir="/tmp/src/wt-hd", worktree_id=None, session_id=None))
        assert rc == 0
        assert captured["noted"] is True
        e = dh.read("wt-hd")[-1]
        assert e["kind"] == "handoff"
        assert e["session"] == "sess-pred"
        assert "task123" in e["summary"] and "Fix the widget" in e["summary"]
        record = m.tracking.load_record(tmp_tracking_dir / "wt-hd.yaml")
        assert record.handoff_counter == 1
        assert record.handoffs[0].token == "task123"
        assert record.handoffs[0].state == "pending"
        assert captured["handoff_ordinal"] == 1

    def test_untracked_is_silent_noop(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: None)
        captured = {}
        monkeypatch.setattr(m, "_json_output", lambda o: captured.update(o))
        rc = m.cmd_note_handoff(argparse.Namespace(
            task="t", title=None, worktree_dir="/tmp/nope",
            worktree_id=None, session_id="s"))
        assert rc == 0
        assert captured["noted"] is False


class TestSessionRole:
    def _rec(self, sessions, head=None):
        from agent_worktrees.tracking import WorktreeRecord
        return WorktreeRecord(
            worktree_id="wt", branch="b", worktree_path="/w", repo="r",
            machine="m", platform="wsl", started_at="t", last_resumed_at="t",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=sessions, head_session=head)

    def _entry(self, sid, state="active"):
        from agent_worktrees.tracking import SessionEntry
        return SessionEntry(session_id=sid, started_at="t", state=state)

    def test_role_head(self):
        r = self._rec([self._entry("A")], head="A")
        assert m._session_role(r, "A")["role"] == "head"
        assert m._session_role(r, "A")["is_head"] is True

    def test_role_superseded_when_registered_and_not_head(self):
        r = self._rec([self._entry("A"), self._entry("B")], head="A")
        assert m._session_role(r, "B")["role"] == "superseded"

    def test_role_unbound_when_unregistered_and_active_head(self):
        r = self._rec([self._entry("A")], head="A")
        assert m._session_role(r, "NEW")["role"] == "unbound"

    def test_role_successor_elect_on_pending_handoff(self):
        r = self._rec([self._entry("A", state="handed-off")])
        role = m._session_role(r, "NEW")
        assert role["role"] == "successor-elect"
        assert role["pending_handoff_predecessor"] == "A"

    def test_role_head_elect_when_empty(self):
        r = self._rec([])
        assert m._session_role(r, "NEW")["role"] == "head-elect"

    def test_succession_header_pending(self):
        r = self._rec([self._entry("Apred", state="handed-off")])
        h = m._succession_header(r)
        assert "handoff is pending" in h

    def test_succession_header_active_head(self):
        r = self._rec([self._entry("Ahead")], head="Ahead")
        h = m._succession_header(r)
        assert "current head session on record" in h

    def test_succession_header_none_when_no_head(self):
        assert m._succession_header(self._rec([])) == ""

    def test_cmd_session_role_untracked(self, monkeypatch, capsys):
        monkeypatch.setattr(m, "_resolve_worktree_for_read",
                            lambda wid, wd=None, sid=None: None)
        captured = {}
        monkeypatch.setattr(m, "_json_output", lambda o: captured.update(o))
        rc = m.cmd_session_role(argparse.Namespace(
            session_id="s", worktree_id=None, worktree_dir=None))
        assert rc == 0
        assert captured["role"] == "untracked"

    def test_parser_accepts_json_compatibility_flag(self):
        args = m.build_parser().parse_args(["session-role", "--json"])
        assert args.json is True
