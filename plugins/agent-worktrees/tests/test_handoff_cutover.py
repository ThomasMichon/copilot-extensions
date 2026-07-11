"""Tests for the live-cutover handoff mux primitives + ``handoff-cutover`` cmd.

These cover the *pure* argv construction and the command's control flow
(mode selection, arg validation, plan reconstruction) with the mux
subprocess boundary mocked -- no real tmux/psmux is invoked.
"""

from __future__ import annotations

import argparse
import json

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import sessions


# â”€â”€ build_mux_new_window_argv (pure) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class TestBuildMuxNewWindowArgv:
    def test_tmux_no_wrapper_strips_identity_and_propagates_env(self):
        argv = sessions.build_mux_new_window_argv(
            "wt1-abc",
            "/w/wt1",
            ["bash", "setup.sh", "--allow-all-tools", "-i", "seed text"],
            {"COPILOT_FEATURE_FLAGS": "x"},
            mux="tmux",
            pane_wrapper="/does/not/exist",
        )
        # target uses the tmux exact-match prefix
        assert argv[:2] == ["tmux", "new-window"]
        assert "-P" in argv and "#{pane_id}" in argv
        i = argv.index("-t")
        assert argv[i + 1] == "=wt-wt1-abc"
        # work dir
        j = argv.index("-c")
        assert argv[j + 1] == "/w/wt1"
        # env propagation
        k = argv.index("-e")
        assert argv[k + 1] == "COPILOT_FEATURE_FLAGS=x"
        # identity strip prefix precedes the command
        assert "env" in argv
        e = argv.index("env")
        assert argv[e:e + 7] == [
            "env", "-u", "WORKTREE_PROJECT", "-u", "WORKTREE_ID",
            "-u", "APERTURE_WORKTREE_ID",
        ]
        # command tail is verbatim (no -- separator, no wrapper)
        assert argv[-5:] == ["bash", "setup.sh", "--allow-all-tools", "-i", "seed text"]
        assert "--" not in argv

    def test_tmux_with_wrapper_wraps_command(self, tmp_path):
        wrapper = tmp_path / "pane-wrapper.sh"
        wrapper.write_text("#!/usr/bin/env bash\nexec \"$@\"\n")
        argv = sessions.build_mux_new_window_argv(
            "id1", "/w", ["copilot", "-i", "hi"], None,
            mux="tmux", pane_wrapper=str(wrapper),
        )
        # env -u ... bash <wrapper> copilot -i hi
        assert "bash" in argv
        b = argv.index("bash")
        assert argv[b + 1] == str(wrapper)
        assert argv[b + 2:] == ["copilot", "-i", "hi"]

    def test_psmux_runs_command_directly_no_identity_prefix(self):
        argv = sessions.build_mux_new_window_argv(
            "id2", "C:/w", ["pwsh.exe", "-File", "s.ps1", "-i", "seed"], None,
            mux="psmux",
        )
        assert argv[:2] == ["psmux", "new-window"]
        # psmux target has NO '=' prefix
        i = argv.index("-t")
        assert argv[i + 1] == "wt-id2"
        # no identity-strip prefix on Windows
        assert "env" not in argv
        assert argv[-5:] == ["pwsh.exe", "-File", "s.ps1", "-i", "seed"]

    def test_psmux_quotes_multiword_args(self):
        # psmux space-joins the pane argv without re-quoting, so a multi-word
        # arg (the seed prompt) must be pre-quoted or Copilot word-splits it.
        argv = sessions.build_mux_new_window_argv(
            "id2", "C:/w",
            ["pwsh.exe", "--allow-all-tools", "--interactive", "three word seed"],
            None, mux="psmux",
        )
        # The seed is wrapped; single-word tokens are left untouched.
        assert argv[-4:] == [
            "pwsh.exe", "--allow-all-tools", "--interactive", '"three word seed"',
        ]

    def test_psmux_quote_escapes_embedded_quote(self):
        argv = sessions.build_mux_new_window_argv(
            "id2", "C:/w", ["copilot", "--interactive", 'say "hi" now'],
            None, mux="psmux",
        )
        assert argv[-1] == '"say ""hi"" now"'

    def test_empty_work_dir_omits_c_flag(self):
        argv = sessions.build_mux_new_window_argv(
            "id3", "", ["copilot"], None, mux="tmux", pane_wrapper="/nope",
        )
        assert "-c" not in argv


# â”€â”€ mux_new_window / mux_retire_pane (subprocess mocked) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class TestMuxNewWindow:
    def test_success_returns_new_pane(self, monkeypatch):
        class R:
            returncode = 0
            stdout = "%7\n"
            stderr = ""

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        out = sessions.mux_new_window("id", "/w", ["copilot"], None, mux="tmux")
        assert out["ok"] is True
        assert out["new_pane"] == "%7"

    def test_failure_returns_error(self, monkeypatch):
        class R:
            returncode = 1
            stdout = ""
            stderr = "no such session"

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        out = sessions.mux_new_window("id", "/w", ["copilot"], None, mux="tmux")
        assert out["ok"] is False
        assert "no such session" in out["error"]


class TestMuxRetirePane:
    def test_already_gone(self, monkeypatch):
        monkeypatch.setattr(sessions, "_mux_pane_alive", lambda p, b: False)
        out = sessions.mux_retire_pane("%3", mux="tmux")
        assert out == {"ok": True, "pane": "%3", "gone": True,
                       "method": "already-gone"}

    def test_graceful_quit(self, monkeypatch):
        # alive once (initial check), then gone after the double Ctrl-C
        states = iter([True, False])
        monkeypatch.setattr(sessions, "_mux_pane_alive",
                            lambda p, b: next(states))
        import subprocess
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 0})())
        out = sessions.mux_retire_pane("%3", mux="tmux", ctrl_c_gap=0,
                                       poll_interval=0, settle_timeout=1)
        assert out["gone"] is True
        assert out["method"] == "graceful"

    def test_hard_kill_fallback(self, monkeypatch):
        # never gone via graceful; kill-pane also fails to remove it
        monkeypatch.setattr(sessions, "_mux_pane_alive", lambda p, b: True)
        import subprocess
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 0})())
        out = sessions.mux_retire_pane("%3", mux="tmux", ctrl_c_gap=0,
                                       poll_interval=0, settle_timeout=0)
        assert out["gone"] is False
        assert out["method"] == "failed"


# â”€â”€ cmd_handoff_cutover control flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _ns(**kw):
    base = dict(seed=None, worktree_id=None, old_pane=None, retire_pane=None,
                dry_run=False, copilot_args=[], recovery=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestCmdHandoffCutover:
    def test_retire_mode(self, monkeypatch, capfd):
        monkeypatch.setattr(sessions, "mux_retire_pane",
                            lambda p, **k: {"ok": True, "pane": p, "gone": True,
                                            "method": "graceful"})
        rc = m.cmd_handoff_cutover(_ns(retire_pane="%9"))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["pane"] == "%9" and out["gone"] is True

    def test_spawn_requires_seed(self, capfd):
        rc = m.cmd_handoff_cutover(_ns())
        assert rc == 1
        assert "requires --seed" in capfd.readouterr().out

    def test_spawn_no_mux_session_exits_3(self, monkeypatch, capfd):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtX")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        rc = m.cmd_handoff_cutover(_ns(seed="go"))
        assert rc == 3
        assert "not under mux" in capfd.readouterr().out

    def test_spawn_unresolvable_worktree_exits_2(self, monkeypatch, capfd):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: None)
        rc = m.cmd_handoff_cutover(_ns(seed="go"))
        assert rc == 2
        assert "could not resolve" in capfd.readouterr().out

    def test_spawn_dry_run_appends_seed_and_reports_old_pane(
        self, monkeypatch, capfd, tmp_path,
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtY")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        monkeypatch.setattr(sessions, "mux_active_pane", lambda w: "%1")

        # Fake config + record + launch cmd
        yaml_path = tmp_path / "wtY.yaml"
        yaml_path.write_text("x")

        class _Cfg:
            pass

        monkeypatch.setattr(m.cfg, "load_config", lambda: _Cfg())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_build_launch_cmd",
            lambda cfg_, args, wd: ["bash", "setup.sh", "--allow-all-tools"],
        )
        monkeypatch.setattr(m, "_build_env", lambda p, s: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})

        # Guard: a real window must NOT be created in dry-run.
        monkeypatch.setattr(sessions, "mux_new_window",
                            lambda *a, **k: pytest.fail("should not spawn"))

        rc = m.cmd_handoff_cutover(_ns(seed="continue the work", dry_run=True))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["dry_run"] is True
        assert out["old_pane"] == "%1"
        assert out["session"] == "wt-wtY"
        assert out["cmd"][-2:] == ["--interactive", "continue the work"]

    def test_spawn_success_opens_window(self, monkeypatch, capfd, tmp_path):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtZ")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        monkeypatch.setattr(sessions, "mux_active_pane", lambda w: "%2")
        (tmp_path / "wtZ.yaml").write_text("x")
        monkeypatch.setattr(m.cfg, "load_config", lambda: object())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(m, "_build_launch_cmd",
                            lambda c, a, wd: ["copilot"])
        monkeypatch.setattr(m, "_build_env", lambda p, s: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})

        captured = {}

        def _fake_new_window(wt, wd, cmd, env, **k):
            captured["cmd"] = cmd
            return {"ok": True, "new_pane": "%5", "error": None}

        monkeypatch.setattr(sessions, "mux_new_window", _fake_new_window)

        rc = m.cmd_handoff_cutover(_ns(seed="resume", old_pane="%2"))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["ok"] is True
        assert out["old_pane"] == "%2"
        assert out["new_pane"] == "%5"
        assert out["seed_len"] == len("resume")
        # --interactive seed appended after the resolved launch cmd
        assert captured["cmd"][-2:] == ["--interactive", "resume"]
