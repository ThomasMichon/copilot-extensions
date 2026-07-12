"""Tests for D5 CLI embodiment: detached mux+Copilot spawn primitives + cmd.

Cover the pure argv construction for a detached ``new-session`` and the
``embody`` command's control flow (target selection, resume-vs-create, seed,
dry-run) with the mux subprocess boundary and worktree side-effects mocked --
no real tmux/psmux is invoked and no worktree is created.
"""

from __future__ import annotations

import argparse
import json

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import sessions


# -- build_mux_new_session_argv (pure) --------------------------------------
class TestBuildMuxNewSessionArgv:
    def test_tmux_detached_strips_identity_and_propagates_env(self):
        argv = sessions.build_mux_new_session_argv(
            "wt1-abc",
            "/w/wt1",
            ["bash", "setup.sh", "--allow-all-tools"],
            {"COPILOT_FEATURE_FLAGS": "x"},
            mux="tmux",
            pane_wrapper="/does/not/exist",
        )
        assert argv[:2] == ["tmux", "new-session"]
        assert "-d" in argv  # detached
        i = argv.index("-s")
        assert argv[i + 1] == "wt-wt1-abc"  # session name (no '=' for new-session)
        assert "-P" in argv and "#{pane_id}" in argv
        j = argv.index("-c")
        assert argv[j + 1] == "/w/wt1"
        k = argv.index("-e")
        assert argv[k + 1] == "COPILOT_FEATURE_FLAGS=x"
        # identity strip prefix precedes the command
        e = argv.index("env")
        assert argv[e:e + 7] == [
            "env", "-u", "WORKTREE_PROJECT", "-u", "WORKTREE_ID",
            "-u", "APERTURE_WORKTREE_ID",
        ]
        assert argv[-3:] == ["bash", "setup.sh", "--allow-all-tools"]
        assert "--" not in argv

    def test_tmux_with_wrapper_wraps_command(self, tmp_path):
        wrapper = tmp_path / "pane-wrapper.sh"
        wrapper.write_text("#!/usr/bin/env bash\nexec \"$@\"\n")
        argv = sessions.build_mux_new_session_argv(
            "id1", "/w", ["copilot"], None,
            mux="tmux", pane_wrapper=str(wrapper),
        )
        b = argv.index("bash")
        assert argv[b + 1] == str(wrapper)
        assert argv[b + 2:] == ["copilot"]

    def test_psmux_runs_command_directly_no_identity_prefix(self):
        argv = sessions.build_mux_new_session_argv(
            "id2", "C:/w", ["pwsh.exe", "-File", "s.ps1"], None, mux="psmux",
        )
        assert argv[:2] == ["psmux", "new-session"]
        assert "-d" in argv
        i = argv.index("-s")
        assert argv[i + 1] == "wt-id2"
        assert "env" not in argv
        assert argv[-3:] == ["pwsh.exe", "-File", "s.ps1"]

    def test_empty_work_dir_omits_c_flag(self):
        argv = sessions.build_mux_new_session_argv(
            "id3", "", ["copilot"], None, mux="tmux", pane_wrapper="/nope",
        )
        assert "-c" not in argv


# -- mux_new_session (subprocess mocked) ------------------------------------
class TestMuxNewSession:
    def test_success_returns_session_and_pane(self, monkeypatch):
        class R:
            returncode = 0
            stdout = "%2\n"
            stderr = ""

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        out = sessions.mux_new_session("id", "/w", ["copilot"], None, mux="tmux")
        assert out["ok"] is True
        assert out["session"] == "wt-id"
        assert out["new_pane"] == "%2"

    def test_failure_returns_error(self, monkeypatch):
        class R:
            returncode = 1
            stdout = ""
            stderr = "duplicate session"

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        out = sessions.mux_new_session("id", "/w", ["copilot"], None, mux="tmux")
        assert out["ok"] is False
        assert "duplicate session" in out["error"]


# -- cmd_embody control flow ------------------------------------------------
def _ns(**kw):
    base = dict(worktree_id=None, new=False, seed=None, verify_timeout=0.0,
                recovery=False, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _stub_config(monkeypatch):
    class _Cfg:
        pass
    monkeypatch.setattr(m.cfg, "load_config", lambda: _Cfg())
    monkeypatch.setattr(m, "_build_launch_cmd", lambda c, a, w: ["copilot"])
    monkeypatch.setattr(m, "_build_env", lambda p, s=None: {})
    monkeypatch.setattr(m, "_repo_session_env", lambda c, w="": {})


class TestCmdEmbody:
    def test_requires_a_target(self, capfd):
        rc = m.cmd_embody(_ns())
        assert rc == 2
        assert "requires --worktree-id" in capfd.readouterr().out

    def test_new_and_worktree_id_are_exclusive(self, capfd):
        rc = m.cmd_embody(_ns(new=True, worktree_id="x"))
        assert rc == 2
        assert "mutually exclusive" in capfd.readouterr().out

    def test_existing_worktree_not_found(self, monkeypatch, capfd, tmp_path):
        _stub_config(monkeypatch)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda r: "wtX")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        rc = m.cmd_embody(_ns(worktree_id="wtX"))
        assert rc == 1
        assert "Worktree not found" in capfd.readouterr().out

    def test_resume_when_mux_session_exists(self, monkeypatch, capfd, tmp_path):
        _stub_config(monkeypatch)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda r: "wtY")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        (tmp_path / "wtY.yaml").write_text("x")
        monkeypatch.setattr(
            m.tracking, "load_record",
            lambda p: type("Rec", (), {"worktree_path": "/w/wtY"})(),
        )
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        monkeypatch.setattr(sessions, "mux_active_pane", lambda w: "%1")
        # must NOT spawn a duplicate
        monkeypatch.setattr(sessions, "mux_new_session",
                            lambda *a, **k: pytest.fail("should not spawn"))
        rc = m.cmd_embody(_ns(worktree_id="wtY"))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["created"] is False and out["resumed"] is True
        assert out["session"] == "wt-wtY" and out["new_pane"] == "%1"

    def test_create_detached_session_and_seed(self, monkeypatch, capfd, tmp_path):
        _stub_config(monkeypatch)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda r: "wtZ")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        (tmp_path / "wtZ.yaml").write_text("x")
        monkeypatch.setattr(
            m.tracking, "load_record",
            lambda p: type("Rec", (), {"worktree_path": "/w/wtZ"})(),
        )
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        spawned = {}
        def _spawn(wt, wd, cmd, env, **k):
            spawned.update(wt=wt, wd=wd, cmd=cmd)
            return {"ok": True, "session": f"wt-{wt}", "new_pane": "%5",
                    "error": None}
        monkeypatch.setattr(sessions, "mux_new_session", _spawn)
        seeded = {}
        def _seed(pane, seed, **k):
            seeded.update(pane=pane, seed=seed)
            return {"ok": True, "pane": pane, "ready": True, "sent": True}
        monkeypatch.setattr(sessions, "mux_seed_pane", _seed)

        rc = m.cmd_embody(_ns(worktree_id="wtZ", seed="do the thing"))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["created"] is True and out["new_pane"] == "%5"
        assert out["seeded"] is True and out["seed_ready"] is True
        assert spawned == {"wt": "wtZ", "wd": "/w/wtZ", "cmd": ["copilot"]}
        assert seeded == {"pane": "%5", "seed": "do the thing"}

    def test_new_creates_worktree_first(self, monkeypatch, capfd):
        _stub_config(monkeypatch)
        monkeypatch.setattr(
            m, "_create_worktree_core",
            lambda c, **k: {"worktree": {"id": "fresh-1", "path": "/w/fresh-1"}},
        )
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        monkeypatch.setattr(
            sessions, "mux_new_session",
            lambda wt, wd, cmd, env, **k: {
                "ok": True, "session": f"wt-{wt}", "new_pane": "%9", "error": None},
        )
        rc = m.cmd_embody(_ns(new=True))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["worktree_id"] == "fresh-1"
        assert out["session"] == "wt-fresh-1" and out["created"] is True

    def test_spawn_failure_exits_4(self, monkeypatch, capfd, tmp_path):
        _stub_config(monkeypatch)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda r: "wtE")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        (tmp_path / "wtE.yaml").write_text("x")
        monkeypatch.setattr(
            m.tracking, "load_record",
            lambda p: type("Rec", (), {"worktree_path": "/w/wtE"})(),
        )
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        monkeypatch.setattr(
            sessions, "mux_new_session",
            lambda *a, **k: {"ok": False, "session": "wt-wtE",
                             "new_pane": None, "error": "boom"},
        )
        rc = m.cmd_embody(_ns(worktree_id="wtE"))
        assert rc == 4
        assert "boom" in capfd.readouterr().out

    def test_dry_run_reports_plan(self, monkeypatch, capfd, tmp_path):
        _stub_config(monkeypatch)
        monkeypatch.setattr(m, "_resolve_worktree_id", lambda r: "wtD")
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
        (tmp_path / "wtD.yaml").write_text("x")
        monkeypatch.setattr(
            m.tracking, "load_record",
            lambda p: type("Rec", (), {"worktree_path": "/w/wtD"})(),
        )
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        monkeypatch.setattr(sessions, "mux_new_session",
                            lambda *a, **k: pytest.fail("dry-run must not spawn"))
        rc = m.cmd_embody(_ns(worktree_id="wtD", dry_run=True))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["dry_run"] is True and out["would"] == "create"
        assert out["cmd"] == ["copilot"]
