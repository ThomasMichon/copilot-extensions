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
        sessions=[],
    )
    save_record(rec, tracking_dir / f"{wt_id}.yaml")


def _args(**kw) -> argparse.Namespace:
    base = dict(
        worktree_id=None, session_id=None, cwd=None, stdin=False, pid=None, pane=None
    )
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

    def test_records_pane_from_explicit_arg(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-pane", "/tmp/src/wt-pane")
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))

        rc = m.cmd_register_session(
            _args(worktree_id="wt-pane", session_id="sess-pane", pane="%12")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-pane.yaml")
        assert rec.sessions is not None
        assert rec.sessions[0].pane_id == "%12"

    def test_records_pane_from_mux_env(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-env", "/tmp/src/wt-env")
        monkeypatch.setenv("TMUX_PANE", "%13")
        monkeypatch.delenv("PSMUX_PANE", raising=False)
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))

        rc = m.cmd_register_session(
            _args(worktree_id="wt-env", session_id="sess-env")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-env.yaml")
        assert rec.sessions is not None
        assert rec.sessions[0].pane_id == "%13"

    def test_reregistration_updates_existing_pane(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-update", "/tmp/src/wt-update")
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        m.cmd_register_session(
            _args(worktree_id="wt-update", session_id="sess-update", pane="%1")
        )

        rc = m.cmd_register_session(
            _args(worktree_id="wt-update", session_id="sess-update", pane="%2")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-update.yaml")
        assert rec.sessions is not None
        assert len(rec.sessions) == 1
        assert rec.sessions[0].pane_id == "%2"

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

    def test_unknown_cwd_still_ensures_resident_monitor(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
        ensured: list[bool] = []
        monkeypatch.setattr(
            m, "_ensure_status_monitor", lambda: ensured.append(True) or True)

        assert m.cmd_register_session(_args(stdin=True)) == 0
        assert ensured == [True]

    def test_no_session_id_is_silent_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
        rc = m.cmd_register_session(_args(worktree_id="wt-none", stdin=True))
        assert rc == 0


def test_register_session_is_a_no_project_command():
    """main() must not balk before dispatch.

    The Copilot CLI runs plugin hooks from the *plugin install dir*, not a
    worktree, so register-session cannot require CWD-based project resolution
    -- otherwise main() balks (cmd_help_unrouted) and the handler never runs,
    leaving sessions[] empty (the #662 regression).  It resolves its own
    project from the payload cwd instead.
    """
    assert "register-session" in m._NO_PROJECT_COMMANDS


class TestRegisterSessionProjectResolution:
    """The handler resolves project context from the payload cwd itself, since
    it is a no-project command (main() sets no active project for it)."""

    def test_activates_project_from_payload_cwd(self, monkeypatch):
        seen: list[str | None] = []
        monkeypatch.setattr(
            m, "_activate_project_for_path", lambda c: seen.append(c)
        )
        # Lookup returns None -> silent no-op; we only assert the activation.
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: None)
        payload = '{"sessionId":"s","cwd":"/tmp/src/wt-x/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        assert seen == ["/tmp/src/wt-x/sub"]

    def test_lookup_error_is_silent_noop(self, monkeypatch):
        """A cwd outside any adopted project makes find_worktree_id_by_cwd
        raise (cfg.tracking_dir() -> project_name() RuntimeError).  The handler
        must swallow it and stay a silent no-op, never surfacing an error."""
        monkeypatch.setattr(m, "_activate_project_for_path", lambda c: None)

        def boom(_c):
            raise RuntimeError("no active project")

        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", boom)
        payload = '{"sessionId":"s","cwd":"/tmp/outside"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0


class TestBareResumeSessionBinding:
    def _set_binding(self, monkeypatch, session_id="target-session"):
        monkeypatch.setenv(m._SESSION_BIND_PROJECT, "test-project")
        monkeypatch.setenv(m._SESSION_BIND_WORKTREE, "wt-bound")
        monkeypatch.setenv(m._SESSION_BIND_SESSION, session_id)
        monkeypatch.setattr(
            m, "_resolve_active_project",
            lambda project: (project, Path("/tmp/project")),
        )

    def test_target_resume_binds_even_when_payload_cwd_is_home(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-bound", "/tmp/src/wt-bound")
        self._set_binding(monkeypatch)
        monkeypatch.setattr(
            m.sys, "stdin",
            io.StringIO('{"sessionId":"target-session","cwd":"/home/user"}'),
        )

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-bound.yaml")
        assert [s.session_id for s in rec.sessions] == ["target-session"]

    def test_temporary_home_session_does_not_consume_binding(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-bound", "/tmp/src/wt-bound")
        self._set_binding(monkeypatch)
        monkeypatch.setattr(
            m.sys, "stdin",
            io.StringIO('{"sessionId":"temporary-session","cwd":"/home/user"}'),
        )

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-bound.yaml")
        assert rec.sessions == []

    def test_session_end_uses_matching_binding(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-bound", "/tmp/src/wt-bound")
        self._set_binding(monkeypatch)
        m.tracking.register_session("wt-bound", "target-session")
        monkeypatch.setattr(m, "_capture_session_title", lambda *_: True)

        rc = m.cmd_deregister_session(
            argparse.Namespace(worktree_id=None, session_id="target-session")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-bound.yaml")
        assert rec.sessions[0].ended_at is not None


def test_deregister_session_worktree_is_optional_for_hook_inference():
    args = m.build_parser().parse_args(
        ["deregister-session", "--session-id", "session-1"]
    )
    assert args.worktree_id is None


class TestRegisterSessionReseedsStatusUpdater:
    """sessionStart must re-seed the status-bar updater so an attached
    long-lived session recovers its bar after a deploy retires the old updater
    (the launcher only spawns it at psmux create/join) -- dotfiles #915."""

    def test_reseeds_with_payload_cwd(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-x", "/tmp/src/wt-x")
        seen: list[tuple[str, str | None]] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        payload = '{"sessionId":"sess-1","cwd":"/tmp/src/wt-x/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        # Seeds the resolved worktree id against the payload cwd (the worktree).
        assert seen == [("wt-x", "/tmp/src/wt-x/sub")]

    def test_reseed_falls_back_to_record_path_when_cwd_absent(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """With an explicit --worktree-id and no payload cwd, the reseed must
        use the tracking record's path -- never the hook's install-dir cwd."""
        _save_record(tmp_tracking_dir, "wt-y", "/tmp/src/wt-y")
        seen: list[tuple[str, str | None]] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))

        rc = m.cmd_register_session(
            _args(worktree_id="wt-y", session_id="sess-2", stdin=True)
        )

        assert rc == 0
        assert seen == [("wt-y", "/tmp/src/wt-y")]

    def test_no_reseed_when_registration_is_a_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """A cwd outside any tracked worktree never registers -- and must never
        spawn an updater for a session that has no worktree/bar."""
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        seen: list = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        assert seen == []
