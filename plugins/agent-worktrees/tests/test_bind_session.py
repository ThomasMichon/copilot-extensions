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
import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import output
from agent_worktrees import activity, tracking, worktree_identity
from agent_worktrees import status_updater_cli
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


def _write_managed_mux_mapping(
    root: Path,
    *,
    project: str,
    worktree_id: str,
    worktree_path: str,
    mux_session: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mux-mapping.json").write_text(
        json.dumps(
            [
                {
                    "project": project,
                    "worktree_id": worktree_id,
                    "worktree_path": worktree_path,
                    "mux_session": mux_session,
                    "mux_bin": "psmux",
                    "mapping_revision": 1,
                    "live": True,
                }
            ]
        ),
        encoding="utf-8",
    )


def _neutralize(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
    monkeypatch.setattr(m, "_spawn_status_updater", lambda wt, path: True)
    monkeypatch.setattr(output, "_json_output", lambda data: captured.update(data))


class TestBindSession:
    def test_manager_owned_session_skips_registry_and_only_ensures_monitor(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch, tmp_path: Path
    ):
        _save_record(tmp_tracking_dir, "wt-managed", "/tmp/src/wt-managed")
        manager_root = tmp_path / "manager-root"
        _write_managed_mux_mapping(
            manager_root,
            project="test-project",
            worktree_id="wt-managed",
            worktree_path="/tmp/src/wt-managed",
            mux_session="wt-managed",
        )
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(manager_root))
        monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        ensured: list[bool] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda *_args, **_kwargs: pytest.fail("manager-owned sessions must not spawn status-updater"),
        )
        monkeypatch.setattr(
            m,
            "_register_session_for_monitor",
            lambda *_args, **_kwargs: pytest.fail("manager-owned sessions must not reseed status-monitor.d"),
        )
        monkeypatch.setattr(
            m, "_ensure_status_monitor", lambda: ensured.append(True) or True
        )

        rc = m.cmd_bind_session(_args(
            worktree_dir="/tmp/src/wt-managed",
            session_id="sess-managed",
        ))

        assert rc == 0
        assert captured["bound"] is True
        assert ensured

    def test_acknowledges_session_start_candidate_atomically(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-ack", "/tmp/src/wt-ack")
        tracking.register_session("wt-ack", "old")
        rec = load_record(tmp_tracking_dir / "wt-ack.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        tracking.register_session(
            "wt-ack", "new", candidate_token="task-123",
        )
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

    def test_acknowledges_session_start_candidate_emits_stage_10_and_11(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 Stages 10/11: bind-session's ``--handoff-token`` acknowledgement
        performs the actual link_handoff() head transfer, so it must emit
        handoff_successor_claimed + handoff_pickup_confirmed_predecessor_closing
        together, exactly once, at that call site."""
        _save_record(tmp_tracking_dir, "wt-ack2", "/tmp/src/wt-ack2")
        tracking.register_session("wt-ack2", "old")
        rec = load_record(tmp_tracking_dir / "wt-ack2.yaml")
        tracking.open_handoff(rec, "old", "task-999")
        tracking.register_session(
            "wt-ack2", "new", candidate_token="task-999",
        )
        rec = load_record(tmp_tracking_dir / "wt-ack2.yaml")
        tracking.associate_handoff_candidate(rec, "task-999", "new")
        captured: dict = {}
        _neutralize(monkeypatch, captured)

        recorded: list[tuple[str, dict]] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append((event, kwargs))
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        rc = m.cmd_bind_session(_args(
            worktree_dir="/tmp/src/wt-ack2",
            session_id="new",
            handoff_token="task-999",
        ))

        assert rc == 0
        claimed = [kw for ev, kw in recorded if ev == "handoff_successor_claimed"]
        closing = [
            kw for ev, kw in recorded
            if ev == "handoff_pickup_confirmed_predecessor_closing"
        ]
        assert len(claimed) == 1
        assert claimed[0]["worktree_id"] == "wt-ack2"
        assert claimed[0]["session_id"] == "new"
        assert claimed[0]["handoff_token"] == "task-999"
        assert claimed[0]["predecessor_session_id"] == "old"
        assert len(closing) == 1
        assert closing[0]["session_id"] == "old"
        assert closing[0]["successor_session_id"] == "new"

    def test_stage_10_and_11_carry_the_mux_pane_launch_id(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 review finding: bind-session runs in the mux pane, whose
        environment carries WORKTREE_LAUNCH_ID -- Stage 10/11 events must
        carry it too, or they drop out of ``activity --launch-id`` and break
        flow correlation."""
        _save_record(tmp_tracking_dir, "wt-ack3", "/tmp/src/wt-ack3")
        tracking.register_session("wt-ack3", "old")
        rec = load_record(tmp_tracking_dir / "wt-ack3.yaml")
        tracking.open_handoff(rec, "old", "task-launch")
        captured: dict = {}
        _neutralize(monkeypatch, captured)
        monkeypatch.setenv("WORKTREE_LAUNCH_ID", "flow-launch-1")

        recorded: list[tuple[str, dict]] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append((event, kwargs))
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        rc = m.cmd_bind_session(_args(
            worktree_dir="/tmp/src/wt-ack3",
            session_id="new",
            handoff_token="task-launch",
        ))

        assert rc == 0
        claimed = [kw for ev, kw in recorded if ev == "handoff_successor_claimed"]
        closing = [
            kw for ev, kw in recorded
            if ev == "handoff_pickup_confirmed_predecessor_closing"
        ]
        assert claimed[0]["launch_id"] == "flow-launch-1"
        assert closing[0]["launch_id"] == "flow-launch-1"

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
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wid: wid)

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
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wt: wt)

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
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        rc = m.cmd_bind_nudge(argparse.Namespace(cwd="/tmp/not/a/wt", stdin=False))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_unbound_worktree_nudges_then_cooldown(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        _save_record(tmp_tracking_dir, "wt-p", "/tmp/src/wt-p")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
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
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        rc = m.cmd_bind_nudge(argparse.Namespace(cwd="/tmp/src/wt-q", stdin=False))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_expired_deadline_does_not_stamp(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-deadline", "/tmp/src/wt-deadline")
        monkeypatch.setattr(
            status_updater_cli, "_activate_project_for_path", lambda c, force=False: None)
        assert m._bind_nudge_decision(
            "/tmp/src/wt-deadline", deadline=0
        ) == {}
        assert not (
            tmp_tracking_dir / "wt-deadline.bind-nudge-at"
        ).exists()


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
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path",
                            lambda c: activated.update(cwd=c))
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, cfg=None: "wt-h")
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wid: wid)

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
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, cfg=None: None)
        rc = m.cmd_history_digest(argparse.Namespace(worktree_id=None, limit=8))
        assert rc == 0
        assert capsys.readouterr().out.strip() == ""


class TestNoteHandoff:
    def test_appends_session_tagged_handoff_entry(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, capsys
    ):
        import agent_worktrees.disposition_history as dh

        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m, "find_worktree_id_by_cwd", lambda c: "wt-hd", raising=False)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
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

    def test_handoff_entry_reflects_an_already_paused_worktree(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """The snapshot must read the record's actual `paused` disposition,
        not silently default to False (which would misrepresent an
        already-paused worktree in its own history)."""
        import agent_worktrees.disposition_history as dh

        _save_record(tmp_tracking_dir, "wt-hp", "/tmp/src/wt-hp")
        record = m.tracking.load_record(tmp_tracking_dir / "wt-hp.yaml")
        m.tracking.set_disposition(record, paused=True, tracking_path=tmp_tracking_dir)
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hp")
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-pred")

        rc = m.cmd_note_handoff(argparse.Namespace(
            task="task456", title="Fix another widget",
            worktree_dir="/tmp/src/wt-hp", worktree_id=None, session_id=None))
        assert rc == 0
        e = dh.read("wt-hp")[-1]
        assert e["kind"] == "handoff"
        assert e["paused"] is True

    def test_untracked_is_silent_noop(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: None)
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        rc = m.cmd_note_handoff(argparse.Namespace(
            task="t", title=None, worktree_dir="/tmp/nope",
            worktree_id=None, session_id="s"))
        assert rc == 0
        assert captured["noted"] is False

    def test_sends_an_affirmative_wake_to_the_resident_monitor(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """note-handoff persists durably FIRST, then wakes -- never the
        other order -- and a wake failure (no resident running, in this
        test) must never break the command's own success."""
        from agent_worktrees import hook_ipc

        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        calls = []
        seen_handoff_tokens_at_wake_time = []

        def _fake_send(kind, payload, **kw):
            # Enforce the ordering claim for real: read the record back
            # from disk INSIDE the wake callback. If send_best_effort were
            # ever moved before the persist, this would observe an empty
            # pending_handoffs list -- a mere call-recorded spy could not
            # tell the two orderings apart.
            record = m.tracking.load_record(tmp_tracking_dir / "wt-hd.yaml")
            seen_handoff_tokens_at_wake_time.append(
                [h.token for h in record.pending_handoffs]
            )
            calls.append((kind, payload))
            return False

        monkeypatch.setattr(hook_ipc, "send_best_effort", _fake_send)

        rc = m.cmd_note_handoff(argparse.Namespace(
            task="t1", title=None, worktree_dir="/tmp/src/wt-hd",
            worktree_id=None, session_id="s"))

        assert rc == 0, "a False/failed wake must never fail the command itself"
        assert calls == [("handoffWake", {"worktree_id": "wt-hd"})]
        assert seen_handoff_tokens_at_wake_time == [["t1"]], (
            "the persisted handoff must already be readable from disk at "
            "the moment of the wake call -- persist, then notify, never "
            "the reverse"
        )

    def test_untracked_worktree_never_sends_a_wake(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        from agent_worktrees import hook_ipc

        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: None)
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        calls = []
        monkeypatch.setattr(
            hook_ipc, "send_best_effort",
            lambda kind, payload, **kw: calls.append((kind, payload)),
        )

        rc = m.cmd_note_handoff(argparse.Namespace(
            task="t", title=None, worktree_dir="/tmp/nope",
            worktree_id=None, session_id="s"))

        assert rc == 0
        assert calls == [], "nothing to wake the monitor about for an untracked path"


class TestCancelHandoff:
    def test_cancels_the_one_matching_pending_entry(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-pred")

        # Open the handoff the same way note-handoff would.
        rc = m.cmd_note_handoff(argparse.Namespace(
            task="task123", title="Fix the widget",
            worktree_dir="/tmp/src/wt-hd", worktree_id=None, session_id=None))
        assert rc == 0
        record = m.tracking.load_record(tmp_tracking_dir / "wt-hd.yaml")
        assert record.handoffs[0].state == "pending"

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task123", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        assert captured["cancelled"] is True
        assert captured["worktree_id"] == "wt-hd"
        record = m.tracking.load_record(tmp_tracking_dir / "wt-hd.yaml")
        assert record.handoffs[0].state == "cancelled"

    def test_dry_run_reports_eligibility_without_mutating_the_ledger(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """Real regression this guards (PR #4570 review round 18): a caller
        with a separate destructive action to fence (context-handoff's task
        abandon / file write) must be able to peek at candidate/eligibility
        BEFORE committing that action, then commit the real cancellation
        only afterward. Committing first (the round-17 shape) left the
        ledger showing "cancelled" even when the caller's own action then
        failed. --dry-run must never call cancel_handoff or save_record."""
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-pred")

        rc = m.cmd_note_handoff(argparse.Namespace(
            task="task456", title="Fix the widget",
            worktree_dir="/tmp/src/wt-hd", worktree_id=None, session_id=None))
        assert rc == 0

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task456", worktree_dir="/tmp/src/wt-hd", worktree_id=None, dry_run=True))
        assert rc == 0
        assert captured["cancelled"] is False
        assert captured["dry_run"] is True
        assert captured["eligible"] is True
        assert captured["candidate"] is None
        # The ledger must be untouched -- still pending, not cancelled.
        record = m.tracking.load_record(tmp_tracking_dir / "wt-hd.yaml")
        assert record.handoffs[0].state == "pending"

        # The real (non-dry-run) call still works afterward.
        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task456", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        assert captured["cancelled"] is True
        record = m.tracking.load_record(tmp_tracking_dir / "wt-hd.yaml")
        assert record.handoffs[0].state == "cancelled"

    def test_never_cancels_a_handoff_with_an_associated_candidate(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """Real regression this guards (PR #4570 review round 16):
        associate_handoff_candidate() leaves the handoff `pending` while
        setting `candidate` -- a successor can register during cutover
        before it formally consumes the task/file. `pending` alone must
        never be read as "safe to cancel"; a candidate-associated handoff is
        already mid-pickup and cancel-handoff must decline (idempotent
        no-op), never report a false success over an in-flight successor."""
        yaml_path = tmp_tracking_dir / "wt-hd.yaml"
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        m.tracking.register_session("wt-hd", "sess-pred", source="handoff")
        m.tracking.register_session("wt-hd", "sess-candidate", source="handoff")

        with m.tracking._RecordLock(yaml_path):
            record = m.tracking.load_record(yaml_path)
            m.tracking.open_handoff(record, "sess-pred", "task-mid-cutover", save=False)
            m.tracking.associate_handoff_candidate(
                record, "task-mid-cutover", "sess-candidate", save=False)
            m.tracking.save_record(record, yaml_path)

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-mid-cutover", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        after = m.tracking.load_record(yaml_path)
        assert after.handoffs[0].state == "pending"
        assert after.handoffs[0].candidate == "sess-candidate"
        # Round 17: the caller must be able to tell "declined because a
        # candidate is mid-pickup" apart from "declined, nothing to worry
        # about" -- surfaced regardless of `cancelled`'s own value.
        assert captured["cancelled"] is False
        assert captured["candidate"] == "sess-candidate"

    def test_advances_lifecycle_revision_so_a_stale_writer_cannot_resurrect_it(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """Real regression this guards (PR #4570 review round 6): cancelling
        must bump lifecycle_revision like open_handoff and every other
        mutation path do -- save_record()'s optimistic-concurrency check only
        preserves newer session/handoff data when the revision increases, so
        skipping this would let an unrelated writer holding a
        pre-cancellation snapshot later save with the same (or lower)
        revision and silently restore the handoff to pending."""
        yaml_path = tmp_tracking_dir / "wt-hd.yaml"
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-pred")

        m.cmd_note_handoff(argparse.Namespace(
            task="task-rev", title=None,
            worktree_dir="/tmp/src/wt-hd", worktree_id=None, session_id=None))
        before = m.tracking.load_record(yaml_path).lifecycle_revision

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-rev", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        after = m.tracking.load_record(yaml_path)
        assert after.lifecycle_revision > before
        assert after.handoffs[0].state == "cancelled"
        # Round 8 finding: cancelling must not permanently strand the
        # worktree headless -- sess-pred was moved to "yielded" when the
        # handoff opened; since no successor ever took over, it must be
        # restored to "active" and reclaim head.
        assert after.session_entry("sess-pred").state == "active"
        assert after.resolved_head_session == "sess-pred"

    def test_does_not_reclaim_head_for_a_predecessor_a_successor_already_superseded(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """The headless-recovery restoration above must never clobber a
        LEGITIMATE newer head: if a successor already claimed head some
        other way (not by consuming THIS handoff), cancelling a stale
        handoff must leave that successor as head and the original
        predecessor still yielded.

        Built directly via tracking primitives (not register_session/
        cmd_note_handoff) so the successor's head claim never routes through
        register_session's own broad "rebind" auto-cancel heuristic --
        that would cancel this handoff itself before cmd_cancel_handoff ever
        runs, testing that heuristic instead of cancel_handoff's own guard.
        """
        yaml_path = tmp_tracking_dir / "wt-hd.yaml"
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        m.tracking.register_session("wt-hd", "sess-pred", source="handoff")
        m.tracking.register_session("wt-hd", "sess-successor", source="handoff")

        with m.tracking._RecordLock(yaml_path):
            record = m.tracking.load_record(yaml_path)
            m.tracking.open_handoff(record, "sess-pred", "task-superseded", save=False)
            record.session_entry("sess-successor").state = "active"
            m.tracking._append_head_transition(record, "sess-successor", reason="test-setup")
            m.tracking.save_record(record, yaml_path)

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-superseded", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        after = m.tracking.load_record(yaml_path)
        assert after.handoffs[0].state == "cancelled"
        assert after.resolved_head_session == "sess-successor"
        assert after.session_entry("sess-pred").state == "yielded"

    def test_does_not_restore_a_stale_predecessor_over_a_newer_yielded_head(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """Real regression this guards (PR #4570 review round 15):
        resolved_head_session deliberately hides YIELDED sessions, so "no
        current head" alone is not proof the worktree is genuinely headless
        -- a genuinely newer head that has since yielded its OWN pending
        handoff also reads as "no head" that way. Cancelling an OLDER
        handoff (sess-a) must not restore sess-a to head and silently
        overwrite sess-b's newer (still-pending) lineage."""
        yaml_path = tmp_tracking_dir / "wt-hd.yaml"
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        m.tracking.register_session("wt-hd", "sess-a", source="handoff")
        m.tracking.register_session("wt-hd", "sess-b", source="handoff")

        with m.tracking._RecordLock(yaml_path):
            record = m.tracking.load_record(yaml_path)
            m.tracking.open_handoff(record, "sess-a", "task-a", save=False)
            # sess-b claims head via its own transition (a real successor
            # pickup/bind), then ALSO opens its own pending handoff, yielding.
            record.session_entry("sess-b").state = "active"
            m.tracking._append_head_transition(record, "sess-b", reason="test-setup")
            m.tracking.open_handoff(record, "sess-b", "task-b", save=False)
            m.tracking.save_record(record, yaml_path)

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-a", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        after = m.tracking.load_record(yaml_path)
        by_token = {h.token: h.state for h in after.handoffs}
        assert by_token["task-a"] == "cancelled"
        assert by_token["task-b"] == "pending"
        assert after.session_entry("sess-a").state == "yielded"
        assert after.head_transitions[-1].session_id == "sess-b"

    def test_explicit_worktree_id_always_activates_its_owning_project(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        """An explicit --worktree-id (no cwd resolution at all) must always
        activate that worktree's OWNING project before touching
        cfg.tracking_dir() -- unconditionally, not only when no project is
        active yet. Real regression this guards (PR #4570 review round 13):
        if a DIFFERENT project already happens to be active (e.g. abort
        running from inside a different adopted checkout), skipping
        relocation would read cfg.tracking_dir() for the wrong project
        entirely."""
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        # A DIFFERENT project is already active -- activation must still run.
        monkeypatch.setattr(m.cfg, "active_project", lambda: "some-other-project")
        activated = []
        monkeypatch.setattr(
            m, "_activate_project_for_worktree_id",
            lambda wt_id: activated.append(wt_id) or True,
        )
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))

        m.cmd_note_handoff(argparse.Namespace(
            task="task-explicit", title=None,
            worktree_dir="/tmp/src/wt-hd", worktree_id=None, session_id="sess-x"))
        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-explicit", worktree_dir=None, worktree_id="wt-hd"))
        assert rc == 0
        assert activated == ["wt-hd"]
        assert captured["cancelled"] is True

    def test_explicit_unknown_worktree_id_fails_closed_instead_of_tracebacking(
        self, monkeypatch_config, monkeypatch
    ):
        """Real regression this guards (PR #4570 review round 12): when no
        project is active and activation cannot find the owner (an unknown
        or mistyped explicit ID), fail closed with a clean JSON result --
        never fall through to _resolve_worktree_id/cfg.tracking_dir(), which
        require an active project and would raise instead. Mirrors the
        paired explicit-ID fail-closed pattern in session_binding_cli.py."""
        monkeypatch.setattr(m.cfg, "active_project", lambda: None)
        monkeypatch.setattr(m, "_activate_project_for_worktree_id", lambda wt_id: False)
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-x", worktree_dir=None, worktree_id="wt-unknown"))
        assert rc == 1
        assert captured["cancelled"] is False
        assert "wt-unknown" in captured["reason"]

    def test_does_not_cancel_an_unrelated_pending_handoff(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        # Real regression this guards: cancel-handoff must be scoped to its
        # exact token, never a blanket "cancel everything pending" sweep --
        # that's _cancel_pending_handoffs's job, reserved for new-session
        # registration, not an explicit external abort of ONE handoff.
        #
        # Both predecessor sessions are registered up front (rather than via
        # separate cmd_note_handoff calls) so opening the second handoff
        # doesn't itself look like a brand-new session bootstrapping onto a
        # worktree with only yielded predecessors -- that's a distinct,
        # legitimate register_session rebind heuristic
        # (_pending_handoffs_all_from_yielded) this test isn't exercising.
        yaml_path = tmp_tracking_dir / "wt-hd.yaml"
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        monkeypatch.setattr(output, "_json_output", lambda o: None)
        m.tracking.register_session("wt-hd", "sess-pred", source="handoff")
        m.tracking.register_session("wt-hd", "sess-other", source="handoff")

        with m.tracking._RecordLock(yaml_path):
            record = m.tracking.load_record(yaml_path)
            m.tracking.open_handoff(record, "sess-pred", "task-keep", save=False)
            m.tracking.open_handoff(record, "sess-other", "task-cancel", save=False)
            m.tracking.save_record(record, yaml_path)

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="task-cancel", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        record = m.tracking.load_record(yaml_path)
        by_token = {h.token: h.state for h in record.handoffs}
        assert by_token["task-cancel"] == "cancelled"
        assert by_token["task-keep"] == "pending"

    def test_idempotent_on_an_already_cancelled_or_unknown_token(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: "wt-hd")
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        _save_record(tmp_tracking_dir, "wt-hd", "/tmp/src/wt-hd")

        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="never-existed", worktree_dir="/tmp/src/wt-hd", worktree_id=None))
        assert rc == 0
        assert captured["cancelled"] is False

    def test_untracked_is_silent_noop(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(status_updater_cli, "_activate_project_for_path", lambda c: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: None)
        captured = {}
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        rc = m.cmd_cancel_handoff(argparse.Namespace(
            token="t", worktree_dir="/tmp/nope", worktree_id=None))
        assert rc == 0
        assert captured["cancelled"] is False


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
        monkeypatch.setattr(output, "_json_output", lambda o: captured.update(o))
        rc = m.cmd_session_role(argparse.Namespace(
            session_id="s", worktree_id=None, worktree_dir=None))
        assert rc == 0
        assert captured["role"] == "untracked"

    def test_parser_accepts_json_compatibility_flag(self):
        args = m.build_parser().parse_args(["session-role", "--json"])
        assert args.json is True


def test_list_row_keeps_a_yielded_head_resumable(tmp_tracking_dir: Path, monkeypatch_config) -> None:
    """A head that opened a handoff no successor ever linked here (it was
    consumed from another checkout) stays the row's resumable session, so a
    Resume reopens it instead of starting a blank one."""
    _save_record(tmp_tracking_dir, "wt-yield", "/tmp/src/wt-yield")
    tracking.register_session("wt-yield", "orchestrator")
    rec = load_record(tmp_tracking_dir / "wt-yield.yaml")
    tracking.open_handoff(rec, "orchestrator", "task-elsewhere")
    rec = load_record(tmp_tracking_dir / "wt-yield.yaml")
    assert rec.resolved_head_session is None  # the ledger still hides it as head

    row = m._worktree_to_dict(rec)
    assert row["last_session_id"] == "orchestrator"
    assert row["head_yielded"] is True

    tracking.register_session("wt-yield", "successor")  # a successor that does register wins
    row = m._worktree_to_dict(load_record(tmp_tracking_dir / "wt-yield.yaml"))
    assert row["last_session_id"] == "successor"
    assert "head_yielded" not in row


def test_resume_reopens_a_yielded_head_over_a_newer_conversation(
    tmp_tracking_dir: Path, tmp_session_state_dir: Path, monkeypatch_config, monkeypatch,
) -> None:
    """Resume's execution-time resolver agrees with the listing: the yielded
    head is the target, ahead of a newer non-head conversation the filesystem
    fallback would pick -- as long as it still has conversation data."""
    from conftest import make_session_dir

    from agent_worktrees import sessions

    _save_record(tmp_tracking_dir, "wt-yres", "/tmp/src/wt-yres")
    tracking.register_session("wt-yres", "orchestrator")
    tracking.open_handoff(load_record(tmp_tracking_dir / "wt-yres.yaml"), "orchestrator", "t-x")
    rec = load_record(tmp_tracking_dir / "wt-yres.yaml")
    assert rec.resolved_head_session is None
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_session_state_dir)
    monkeypatch.setattr(sessions, "find_latest_session_id_fast", lambda path, regs: "newer")

    make_session_dir(tmp_session_state_dir, "orchestrator", "/tmp/src/wt-yres",
                     has_events_file=False)  # a stub: not resumable
    assert sessions.resolve_resume_target(rec) == "newer"
    make_session_dir(tmp_session_state_dir, "orchestrator", "/tmp/src/wt-yres")
    assert sessions.resolve_resume_target(rec) == "orchestrator"
