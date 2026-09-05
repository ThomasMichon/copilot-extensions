"""Tests for the live-cutover handoff mux primitives + ``handoff-cutover`` cmd.

These cover the *pure* argv construction and the command's control flow
(mode selection, arg validation, plan reconstruction) with the mux
subprocess boundary mocked -- no real tmux/psmux is invoked.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import activity, locks, procs, reclaim, sessions


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
        assert argv[e:e + 5] == [
            "env", "-u", "WORKTREE_PROJECT", "-u", "WORKTREE_ID",
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
        # env -u ... bash <wrapper> --aw-wt <id> copilot --interactive hi
        assert "bash" in argv
        b = argv.index("bash")
        assert argv[b + 1] == str(wrapper)
        assert argv[b + 2:] == ["--aw-wt", "id1", "copilot", "-i", "hi"]

    def test_psmux_runs_command_directly_no_identity_prefix(self):
        argv = sessions.build_mux_new_window_argv(
            "id2", "C:/w", ["pwsh.exe", "-File", "s.ps1", "-i", "seed"], None,
            mux="psmux", pane_wrapper="/does/not/exist",
        )
        assert argv[:2] == ["psmux", "new-window"]
        # psmux target has NO '=' prefix
        i = argv.index("-t")
        assert argv[i + 1] == "wt-id2"
        # no identity-strip prefix on Windows
        assert "env" not in argv
        # psmux runs the command verbatim -- `pwsh -File <script>` passes its
        # args literally so `--`-prefixed passthrough (e.g. --allow-all) reaches
        # Copilot; the pane command is NOT collapsed to `& '<script>'` (#102).
        assert argv[-5:] == ["pwsh.exe", "-File", "s.ps1", "-i", "seed"]

    def test_psmux_runs_command_verbatim_no_quoting(self):
        # Without an initial-prompt transport or wrapper, the psmux branch runs
        # the command verbatim -- no quoting layer that could break the spawn.
        argv = sessions.build_mux_new_window_argv(
            "id2", "C:/w",
            ["pwsh.exe", "--allow-all-tools"], None, mux="psmux",
            pane_wrapper="/does/not/exist",
        )
        assert argv[-2:] == ["pwsh.exe", "--allow-all-tools"]

    def test_psmux_prompt_transport_requires_and_uses_wrapper(self, tmp_path):
        wrapper = tmp_path / "wrapper with spaces" / "pane-wrapper.ps1"
        wrapper.parent.mkdir()
        wrapper.write_text("# test wrapper\n")
        receipt = tmp_path / "receipt path" / "receipt123"
        argv = sessions.build_mux_new_window_argv(
            "id2",
            "C:/w",
            ["pwsh.exe", "-File", "s.ps1"],
            None,
            mux="psmux",
            pane_wrapper=str(wrapper),
            initial_prompt="three word seed",
            prompt_receipt=str(receipt),
        )
        assert argv[-5:-1] == [
            "pwsh.exe", "-NoProfile", "-NoLogo", "-EncodedCommand",
        ]
        assert "three word seed" not in argv
        assert str(wrapper) not in argv
        encoded_script = argv[-1]
        script = base64.b64decode(encoded_script).decode("utf-16-le")
        assert "FromBase64String" in script
        assert base64.b64encode(str(wrapper).encode()).decode() in script
        args_b64 = script.split("FromBase64String('")[2].split("'")[0]
        wrapper_args = json.loads(base64.b64decode(args_b64).decode("utf-8"))
        receipt_flag = wrapper_args.index("--aw-prompt-receipt-b64")
        decoded_receipt = base64.b64decode(
            wrapper_args[receipt_flag + 1]
        ).decode("utf-8")
        assert decoded_receipt == str(receipt)

    def test_explicit_mux_session_targets_adopted_anchor_session(self):
        argv = sessions.build_mux_new_window_argv(
            "@anchor",
            "C:/repo",
            ["pwsh.exe", "-File", "setup.ps1"],
            None,
            mux="psmux",
            pane_wrapper="/does/not/exist",
            session_name="caller-owned-session",
        )
        i = argv.index("-t")
        assert argv[i + 1] == "caller-owned-session"

    def test_prompt_transport_fails_closed_without_wrapper(self):
        with pytest.raises(RuntimeError, match="pane wrapper is required"):
            sessions.build_mux_new_window_argv(
                "id2", "C:/w", ["copilot"], None,
                mux="psmux", pane_wrapper="/does/not/exist",
                initial_prompt="three word seed",
                prompt_receipt="C:/receipt path/receipt123",
            )

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

    def test_required_prompt_wrapper_failure_is_structured(self, monkeypatch):
        monkeypatch.setattr(
            sessions,
            "build_mux_new_window_argv",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("wrapper missing")),
        )
        out = sessions.mux_new_window(
            "id", "/w", ["copilot"], None,
            mux="psmux", initial_prompt="continue",
        )
        assert out == {
            "ok": False,
            "new_pane": None,
            "error": "wrapper missing",
        }

    def test_missing_prompt_receipt_retires_successor(self, monkeypatch, tmp_path):
        class R:
            returncode = 0
            stdout = "%9\n"
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(
            sessions, "_initial_prompt_receipt_path",
            lambda token: tmp_path / token,
        )
        retired = {}
        monkeypatch.setattr(
            sessions,
            "mux_retire_pane",
            lambda pane, **k: retired.update(pane=pane)
            or {"ok": True, "method": "test-retire"},
        )
        monkeypatch.setattr(
            sessions,
            "build_mux_new_window_argv",
            lambda *a, **k: ["psmux", "new-window"],
        )

        out = sessions.mux_new_window(
            "id", "/w", ["copilot"], None,
            mux="psmux", initial_prompt="continue",
            prompt_receipt_timeout=0,
        )

        assert out["ok"] is False
        assert out["prompt_received"] is False
        assert retired["pane"] == "%9"

    def test_failed_prompt_receipt_rejects_successor(self, monkeypatch, tmp_path):
        receipt = tmp_path / "receipt"

        class R:
            returncode = 0
            stdout = "%9\n"
            stderr = ""

        def _spawn(*args, **kwargs):
            receipt.write_text("failed:2", encoding="utf-8")
            return R()

        monkeypatch.setattr(subprocess, "run", _spawn)
        monkeypatch.setattr(
            sessions, "_initial_prompt_receipt_path", lambda token: receipt,
        )
        monkeypatch.setattr(
            sessions, "_mux_pane_process_tree", lambda *a, **k: {100, 101},
        )
        cleaned = {}
        monkeypatch.setattr(
            sessions,
            "_retire_failed_successor",
            lambda pane, tree, **k: cleaned.update(pane=pane, tree=tree)
            or {"ok": True},
        )
        monkeypatch.setattr(
            sessions,
            "build_mux_new_window_argv",
            lambda *a, **k: ["psmux", "new-window"],
        )

        out = sessions.mux_new_window(
            "id", "/w", ["copilot"], None,
            mux="psmux", initial_prompt="continue",
            prompt_startup_grace=0,
        )

        assert out["ok"] is False
        assert out["prompt_status"] == "failed:2"
        assert cleaned == {"pane": "%9", "tree": {100, 101}}

    def test_child_exit_during_startup_grace_preserves_receipt_failure(
        self, monkeypatch, tmp_path,
    ):
        receipt = tmp_path / "receipt"

        class R:
            returncode = 0
            stdout = "%9\n"
            stderr = ""

        def _spawn(*args, **kwargs):
            receipt.write_text("launching", encoding="utf-8")
            return R()

        monkeypatch.setattr(subprocess, "run", _spawn)
        monkeypatch.setattr(
            sessions, "_initial_prompt_receipt_path", lambda token: receipt,
        )
        monkeypatch.setattr(sessions, "_mux_pane_alive", lambda *a, **k: False)
        monkeypatch.setattr(
            sessions, "_mux_pane_process_tree", lambda *a, **k: {100},
        )
        monkeypatch.setattr(
            sessions,
            "_retire_failed_successor",
            lambda *a, **k: {"ok": True},
        )
        monkeypatch.setattr(
            sessions,
            "build_mux_new_window_argv",
            lambda *a, **k: ["psmux", "new-window"],
        )

        out = sessions.mux_new_window(
            "id",
            "/w",
            ["copilot"],
            None,
            mux="psmux",
            initial_prompt="continue",
        )

        assert out["ok"] is False
        assert out["prompt_received"] is False
        assert out["prompt_status"] == "failed:pane-exited"
        assert out["error"].endswith("(status: failed:pane-exited)")

    def test_parent_transports_exact_watched_receipt_path(
        self, monkeypatch, tmp_path,
    ):
        receipt = tmp_path / "receipt path" / "receipt"
        captured = {}

        class R:
            returncode = 0
            stdout = "%9\n"
            stderr = ""

        def _build(*args, **kwargs):
            captured.update(kwargs)
            receipt.parent.mkdir(parents=True)
            receipt.write_text("launching", encoding="utf-8")
            return ["psmux", "new-window"]

        monkeypatch.setattr(
            sessions, "_initial_prompt_receipt_path", lambda token: receipt,
        )
        monkeypatch.setattr(
            sessions, "build_mux_new_window_argv", _build,
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(sessions, "_mux_pane_alive", lambda *a: True)

        out = sessions.mux_new_window(
            "id", "/w", ["copilot"], None,
            mux="psmux", initial_prompt="continue",
            prompt_startup_grace=0,
        )

        assert out["ok"] is True
        assert captured["prompt_receipt"] == str(receipt)

    def test_failed_successor_cleanup_terminates_exact_tree(self, monkeypatch):
        alive = {100, 101, 102}
        monkeypatch.setattr(
            sessions,
            "mux_retire_pane",
            lambda pane, **k: {"ok": True, "gone": True, "method": "hard"},
        )
        monkeypatch.setattr(locks, "pid_alive", lambda pid: pid in alive)

        def _terminate(pid):
            alive.discard(pid)
            return True

        monkeypatch.setattr(procs, "terminate_pid", _terminate)

        out = sessions._retire_failed_successor(
            "%9", {100, 101, 102}, mux="psmux",
        )

        assert out["ok"] is True
        assert out["process_tree"] == [100, 101, 102]
        assert out["terminated"] == [102, 101, 100]
        assert out["survivors"] == []


class TestPaneWrapperInitialPrompt:
    def test_wrapper_appends_native_interactive_prompt(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        wrapper_dir = tmp_path / "wrapper with spaces"
        wrapper_dir.mkdir()
        capture = tmp_path / "capture.py"
        output = tmp_path / "args.json"
        capture.write_text(
            "import json, os, sys\n"
            "with open(os.environ['PROMPT_ARGS_OUT'], 'w', encoding='utf-8') as f:\n"
            "    json.dump(sys.argv[1:], f)\n",
            encoding="utf-8",
        )
        prompt = 'continue the "multi word" work\n\n'
        receipt = tmp_path / "receipt path" / "wrappertest"
        env = os.environ.copy()
        env["PROMPT_ARGS_OUT"] = str(output)
        env["WORKTREE_PANE_MIN_RUNTIME"] = "0"
        env["WORKTREE_PANE_WAIT_TIMEOUT"] = "0"
        env["WORKTREE_PROMPT_STARTUP_GRACE"] = "0"

        if platform.system() == "Windows":
            pwsh = shutil.which("pwsh")
            if not pwsh:
                pytest.skip("pwsh is required for the Windows pane wrapper")
            wrapper = wrapper_dir / "pane-wrapper.ps1"
            shutil.copy2(root / "bin" / "pane-wrapper.ps1", wrapper)
            cmd = sessions._mux_pane_cmd(
                "id",
                [sys.executable, str(capture)],
                is_tmux=False,
                pane_wrapper=str(wrapper),
                initial_prompt=prompt,
                prompt_receipt=str(receipt),
            )
        else:
            bash = shutil.which("bash")
            if not bash:
                pytest.skip("bash is required for the Unix pane wrapper")
            wrapper = wrapper_dir / "pane-wrapper.sh"
            shutil.copy2(root / "bin" / "pane-wrapper.sh", wrapper)
            cmd = sessions._mux_pane_cmd(
                "id",
                [sys.executable, str(capture)],
                is_tmux=True,
                pane_wrapper=str(wrapper),
                initial_prompt=prompt,
                prompt_receipt=str(receipt),
            )

        receipt.unlink(missing_ok=True)
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(output.read_text("utf-8")) == [
            "--interactive", prompt,
        ]
        assert receipt.read_text("utf-8") == "launching"
        receipt.unlink()


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
                                       poll_interval=0, settle_timeout=0,
                                       hard_kill_settle=0)
        assert out["gone"] is False
        assert out["method"] == "failed"

    def test_hard_kill_waits_for_mux_to_drop_pane(self, monkeypatch):
        states = iter([True, True, True, True, False])
        monkeypatch.setattr(
            sessions, "_mux_pane_alive", lambda p, b: next(states),
        )
        import subprocess
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: type("R", (), {"returncode": 0})(),
        )
        out = sessions.mux_retire_pane(
            "%3", mux="tmux", ctrl_c_gap=0, poll_interval=0,
            settle_timeout=0, hard_kill_settle=1,
        )
        assert out["gone"] is True
        assert out["method"] == "hard"

    def test_last_window_guard_skips_retire(self, monkeypatch):
        calls: list[list[str]] = []
        monkeypatch.setattr(sessions, "_mux_pane_alive", lambda p, b: True)
        monkeypatch.setattr(sessions, "_mux_last_window_guard",
                            lambda p, b: {"session": "wt-demo", "window_count": 1})
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        import subprocess

        def _fake_run(*a, **k):
            calls.append(list(a[0]))
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        out = sessions.mux_retire_pane("%3", mux="tmux")
        assert out["gone"] is False
        assert out["method"] == "last-window-skip"
        assert not any(c[1] in ("send-keys", "kill-pane") for c in calls)

    def test_third_ctrl_c_when_two_dont_land(self, monkeypatch):
        # Alive through the double-interrupt escalate window, gone only after
        # the conditional third Ctrl-C -- verifies three C-c are sent (#3946).
        calls: list[list[str]] = []

        # alive for: initial check + escalate poll (still up), then gone.
        states = iter([True, True, False])
        monkeypatch.setattr(sessions, "_mux_pane_alive",
                            lambda p, b: next(states))
        import subprocess

        def _fake_run(*a, **k):
            calls.append(list(a[0]))
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        out = sessions.mux_retire_pane(
            "%3", mux="tmux", ctrl_c_gap=0, poll_interval=0,
            settle_timeout=2, escalate_after=0,
        )
        assert out["gone"] is True
        assert out["method"] == "graceful"
        ctrl_c = [c for c in calls if c[1] == "send-keys" and c[-1] == "C-c"]
        assert len(ctrl_c) == 3
        assert not any(c[1] == "kill-pane" for c in calls)


# â”€â”€ cmd_handoff_cutover control flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _ns(**kw):
    base = dict(seed=None, worktree_id=None, session_id=None, old_pane=None,
                retire_pane=None, mux_session=None, require_mux_identity=False,
                expected_copilot_pid=None, expected_copilot_start_time=None,
                dry_run=False,
                copilot_args=[], recovery=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestCmdHandoffCutover:
    def test_wait_for_handoff_candidate_observes_session_start(
        self, monkeypatch, tmp_path,
    ):
        handoff = type("_Handoff", (), {
            "token": "task-123",
            "candidate": "successor-session",
        })()
        record = type("_Record", (), {"handoffs": [handoff]})()
        monkeypatch.setattr(m.tracking, "load_record", lambda path: record)

        assert m._wait_for_handoff_candidate(
            tmp_path / "wt.yaml", "task-123", "%5", timeout=0.1,
        ) == ("successor-session", "session-associated")

    def test_wait_for_handoff_candidate_rejects_exited_pane(
        self, monkeypatch, tmp_path,
    ):
        record = type("_Record", (), {"handoffs": []})()
        monkeypatch.setattr(m.tracking, "load_record", lambda path: record)
        monkeypatch.setattr(sessions, "_mux_bin", lambda: "psmux")
        monkeypatch.setattr(sessions, "_mux_pane_alive", lambda *a: False)

        assert m._wait_for_handoff_candidate(
            tmp_path / "wt.yaml", "task-123", "%5", timeout=0.1,
        ) == (None, "pane-exited-before-session")
    @pytest.fixture(autouse=True)
    def _use_local_session_backend(self, monkeypatch):
        monkeypatch.setattr(
            m,
            "_unsupported_hosted_launch",
            lambda config, record, operation: "",
        )

    def test_parser_accepts_retire_mux_identity(self):
        args = m.build_parser().parse_args([
            "handoff-cutover",
            "--retire-pane", "%9",
            "--require-mux-identity",
            "--mux-session", "caller-session",
        ])
        assert args.mux_session == "caller-session"
        assert args.require_mux_identity is True

    def test_retire_rejects_reused_predecessor_pid(self, monkeypatch, capfd):
        monkeypatch.setattr(
            sessions,
            "mux_binding_for_session",
            lambda sid: {
                "pane_id": "%9",
                "copilot_pid": 77,
                "copilot_start_time": "new-process",
            },
        )
        monkeypatch.setattr(
            sessions, "mux_retire_pane",
            lambda *a, **k: pytest.fail("must not retire an unverified process"),
        )
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)

        rc = m.cmd_handoff_cutover(_ns(
            retire_pane="%9",
            session_id="old-sess",
            expected_copilot_pid=77,
            expected_copilot_start_time="old-process",
        ))

        assert rc == 1
        out = json.loads(capfd.readouterr().out)
        assert out["method"] == "process-identity-mismatch"

    def test_retire_retry_accepts_already_gone_predecessor(
        self, monkeypatch, capfd,
    ):
        monkeypatch.setattr(
            sessions, "mux_session_for_pane", lambda pane: None,
        )
        monkeypatch.setattr(
            sessions, "mux_binding_for_session",
            lambda sid: pytest.fail("gone pane needs no live mux binding"),
        )
        monkeypatch.setattr(
            sessions, "mux_retire_pane",
            lambda *a, **k: pytest.fail("gone pane must not be signaled"),
        )
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        seen = {}

        def _ensure(sid, **kwargs):
            seen.update(session=sid, **kwargs)
            return {
                "checked": True,
                "identity_verified": True,
                "found": 0,
                "reaped": 0,
                "survivors": 0,
                "pids": [],
            }

        monkeypatch.setattr(reclaim, "ensure_session_copilot_reaped", _ensure)
        rc = m.cmd_handoff_cutover(_ns(
            retire_pane="%9",
            session_id="old-sess",
            mux_session="original-session",
            require_mux_identity=True,
            expected_copilot_pid=77,
            expected_copilot_start_time="old-process",
        ))

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["method"] == "identity-unresolved-skip"
        assert out["ok"] is True
        assert seen == {
            "session": "old-sess",
            "expected_pid": 77,
            "expected_start_time": "old-process",
        }

    def test_retire_retry_skips_reused_pane_without_live_binding(
        self, monkeypatch, capfd,
    ):
        monkeypatch.setattr(
            sessions, "mux_session_for_pane",
            lambda pane: "different-session",
        )
        monkeypatch.setattr(
            sessions, "mux_binding_for_session",
            lambda sid: pytest.fail("reused pane needs no predecessor binding"),
        )
        monkeypatch.setattr(
            sessions, "mux_retire_pane",
            lambda *a, **k: pytest.fail("reused pane must not be signaled"),
        )
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(
            reclaim, "ensure_session_copilot_reaped",
            lambda sid, **kwargs: {
                "checked": True,
                "identity_verified": True,
                "found": 0,
                "reaped": 0,
                "survivors": 0,
                "pids": [],
            },
        )
        rc = m.cmd_handoff_cutover(_ns(
            retire_pane="%9",
            session_id="old-sess",
            mux_session="original-session",
            require_mux_identity=True,
            expected_copilot_pid=77,
            expected_copilot_start_time="old-process",
        ))

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["method"] == "identity-mismatch-skip"
        assert out["current_mux_session"] == "different-session"
        assert out["ok"] is True

    def test_retire_mode(self, monkeypatch, capfd):
        monkeypatch.setattr(sessions, "mux_retire_pane",
                            lambda p, **k: {"ok": True, "pane": p, "gone": True,
                                            "method": "graceful"})
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        rc = m.cmd_handoff_cutover(_ns(retire_pane="%9"))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["pane"] == "%9" and out["gone"] is True

    def test_retire_reaps_old_copilot_before_success(self, monkeypatch, capfd):
        # A hard pane-kill left the pane gone; the OLD Copilot process is then
        # reaped, and only then is success declared.
        monkeypatch.setattr(sessions, "mux_retire_pane",
                            lambda p, **k: {"ok": True, "pane": p, "gone": True,
                                            "method": "hard"})
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        seen = {}

        def _ensure(sid, **k):
            seen["sid"] = sid
            return {"checked": True, "found": 1, "reaped": 1,
                    "survivors": 0, "pids": [7]}

        monkeypatch.setattr(reclaim, "ensure_session_copilot_reaped", _ensure)
        rc = m.cmd_handoff_cutover(_ns(retire_pane="%9", session_id="old-sess"))
        assert rc == 0
        assert seen["sid"] == "old-sess"
        out = json.loads(capfd.readouterr().out)
        assert out["ok"] is True and out["copilot"]["reaped"] == 1

    def test_retire_fails_when_old_copilot_survives(self, monkeypatch, capfd):
        # Pane retired but the old Copilot process survived the reap -> the retire
        # must NOT declare success (a lingering parallel session remains).
        monkeypatch.setattr(sessions, "mux_retire_pane",
                            lambda p, **k: {"ok": True, "pane": p, "gone": True,
                                            "method": "hard"})
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(
            reclaim, "ensure_session_copilot_reaped",
            lambda sid, **k: {"checked": True, "found": 1, "reaped": 0,
                              "survivors": 1, "pids": [7]})
        rc = m.cmd_handoff_cutover(_ns(retire_pane="%9", session_id="old-sess"))
        assert rc == 1
        out = json.loads(capfd.readouterr().out)
        assert out["ok"] is False and out["copilot"]["survivors"] == 1

    def test_retire_last_window_skip_does_not_reap(self, monkeypatch, capfd):
        # The last-window guard deliberately keeps the pane + session alive, so
        # the process reap must be skipped (never kill the session we're keeping).
        monkeypatch.setattr(sessions, "mux_retire_pane",
                            lambda p, **k: {"ok": True, "pane": p, "gone": False,
                                            "method": "last-window-skip"})
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        called = {"n": 0}

        def _ensure(sid, **k):
            called["n"] += 1
            return {"checked": True}

        monkeypatch.setattr(reclaim, "ensure_session_copilot_reaped", _ensure)
        rc = m.cmd_handoff_cutover(_ns(retire_pane="%9", session_id="old-sess"))
        assert rc == 0
        assert called["n"] == 0
        out = json.loads(capfd.readouterr().out)
        assert "copilot" not in out

    def test_retire_mux_identity_mismatch_skips_unrelated_pane(
        self, monkeypatch, capfd,
    ):
        monkeypatch.setattr(
            sessions, "mux_session_for_pane",
            lambda pane: "new-server-session",
        )
        monkeypatch.setattr(
            sessions, "mux_retire_pane",
            lambda *a, **k: pytest.fail("must not retire a reused pane"),
        )
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        reaped = {}
        monkeypatch.setattr(
            reclaim, "ensure_session_copilot_reaped",
            lambda sid: reaped.update(session=sid) or {
                "checked": True, "found": 1, "reaped": 1,
                "survivors": 0, "pids": [7],
            },
        )

        rc = m.cmd_handoff_cutover(_ns(
            retire_pane="%9",
            mux_session="original-session",
            session_id="old-sess",
        ))

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["method"] == "identity-mismatch-skip"
        assert out["current_mux_session"] == "new-server-session"
        assert reaped["session"] == "old-sess"

    def test_retire_unresolved_mux_identity_requires_session_reap(
        self, monkeypatch, capfd,
    ):
        monkeypatch.setattr(sessions, "mux_session_for_pane", lambda pane: None)
        monkeypatch.setattr(
            sessions, "mux_retire_pane",
            lambda *a, **k: pytest.fail("must not retire an unverified pane"),
        )
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(
            reclaim, "ensure_session_copilot_reaped",
            lambda sid: {
                "checked": True, "found": 1, "reaped": 0,
                "survivors": 1, "pids": [7],
            },
        )

        rc = m.cmd_handoff_cutover(_ns(
            retire_pane="%9",
            mux_session="original-session",
            session_id="old-sess",
        ))

        assert rc == 1
        out = json.loads(capfd.readouterr().out)
        assert out["method"] == "identity-unresolved-skip"
        assert out["copilot"]["survivors"] == 1

    def test_retire_missing_mux_identity_reaps_without_signaling_pane(
        self, monkeypatch, capfd,
    ):
        monkeypatch.setattr(
            sessions, "mux_retire_pane",
            lambda *a, **k: pytest.fail("must not retire without mux identity"),
        )
        monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(
            reclaim, "ensure_session_copilot_reaped",
            lambda sid: {
                "checked": True, "found": 1, "reaped": 1,
                "survivors": 0, "pids": [7],
            },
        )

        rc = m.cmd_handoff_cutover(_ns(
            retire_pane="%9",
            require_mux_identity=True,
            session_id="old-sess",
        ))

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["method"] == "identity-unavailable-skip"
        assert out["copilot"]["reaped"] == 1

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

    def test_spawn_bare_resume_resolves_worktree_from_session_id(
        self, monkeypatch, capfd,
    ):
        """#4098: cwd is HOME (inference returns None), but the resumed session
        id resolves the worktree authoritatively via the registry -- so the
        cutover proceeds instead of failing with exit 2."""
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: None)
        monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
        monkeypatch.setattr(
            m.tracking, "find_worktree_id_by_session",
            lambda sid: "wtBARE" if sid == "sess-xyz" else None)
        # Proceed far enough to prove the worktree resolved: a no-mux check now
        # keys off the SESSION-resolved id, so exit 3 (not 2) proves resolution.
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        rc = m.cmd_handoff_cutover(_ns(seed="go", session_id="sess-xyz"))
        out = capfd.readouterr().out
        assert rc == 3
        assert "wt-wtBARE" in out and "not under mux" in out

    def test_spawn_bare_resume_prefers_scoped_binding(self, monkeypatch, capfd):
        """The scoped bare-resume binding (AGENT_WORKTREES_BIND_*) wins as the
        first authoritative fallback before the registry scan."""
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: None)
        monkeypatch.setattr(m, "_activate_session_binding",
                            lambda sid: "wtBOUND")
        # Registry scan must NOT be needed when the binding resolves.
        def _boom(sid):  # pragma: no cover - must not be called
            raise AssertionError("registry scan should not run")
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_session", _boom)
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: False)
        rc = m.cmd_handoff_cutover(_ns(seed="go", session_id="sess-xyz"))
        out = capfd.readouterr().out
        assert rc == 3 and "wt-wtBOUND" in out

    def test_spawn_session_id_unresolvable_still_exits_2(
        self, monkeypatch, capfd,
    ):
        """When neither cwd nor the session id resolves a worktree, still exit 2
        -- and the message mentions the --session-id fallback."""
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: None)
        monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_session",
                            lambda sid: None)
        rc = m.cmd_handoff_cutover(_ns(seed="go", session_id="ghost"))
        out = capfd.readouterr().out
        assert rc == 2
        assert "could not resolve" in out and "session-id" in out

    def test_spawn_adopted_anchor_opens_successor_in_current_mux(
        self, monkeypatch, capfd, tmp_path,
    ):
        anchor = tmp_path / "repo"
        anchor.mkdir()
        monkeypatch.chdir(anchor)
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: None)
        monkeypatch.setattr(m, "_cwd_is_inside_project", lambda p: True)
        monkeypatch.setattr(
            sessions, "current_mux_session",
            lambda pane_id=None: "caller-session",
        )
        monkeypatch.setattr(
            sessions, "has_mux_session_named",
            lambda name: name == "caller-session",
        )

        config = type(
            "_Cfg",
            (),
            {"default_repo": type("_Repo", (), {"anchor": str(anchor)})()},
        )()
        monkeypatch.setattr(m.cfg, "load_config", lambda: config)
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight())
        launch = {}

        def _build_launch(config, args, work_dir, **kwargs):
            launch.update(kwargs)
            return ["copilot"]

        monkeypatch.setattr(
            m, "_build_launch_cmd", _build_launch)
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
        monkeypatch.setattr(
            sessions,
            "mux_binding_for_session",
            lambda sid, expected_session_name=None: {
                "session_name": expected_session_name,
                "pane_id": "%4",
                "copilot_pid": 4242,
                "copilot_start_time": "created-1",
            },
        )
        captured = {}

        def _new_window(wt, wd, cmd, env, **kwargs):
            captured.update(
                worktree=wt,
                work_dir=wd,
                cmd=cmd,
                session_name=kwargs.get("session_name"),
                initial_prompt=kwargs.get("initial_prompt"),
            )
            return {
                "ok": True,
                "new_pane": "%5",
                "prompt_received": True,
            }

        monkeypatch.setattr(sessions, "mux_new_window", _new_window)
        monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)

        rc = m.cmd_handoff_cutover(
            _ns(
                seed="continue",
                old_pane="%4",
                session_id="anchor-session",
            )
        )

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["session"] == "caller-session"
        assert out["old_pane"] == "%4"
        assert out["new_pane"] == "%5"
        assert captured == {
            "worktree": "@anchor",
            "work_dir": str(anchor),
            "cmd": ["copilot"],
            "session_name": "caller-session",
            "initial_prompt": "continue",
        }
        assert launch["fallback_copilot_path"] is None

    def test_spawn_dry_run_reports_plan_and_old_pane(
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
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight())
        monkeypatch.setattr(
            m, "_build_launch_cmd",
            lambda cfg_, args, wd, **k: ["bash", "setup.sh", "--allow-all-tools"],
        )
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
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
        # The seed is NOT a launch arg -- the plain launch cmd is reported as-is.
        assert out["cmd"] == ["bash", "setup.sh", "--allow-all-tools"]
        assert out["seed_len"] == len("continue the work")

    def test_spawn_dry_run_prefers_recorded_copilot_pane(
        self, monkeypatch, capfd, tmp_path
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtY")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        monkeypatch.setattr(
            sessions, "mux_copilot_pane", lambda w, session_id=None: "%bound"
        )
        monkeypatch.setattr(
            sessions, "mux_active_pane", lambda w: pytest.fail("active fallback used")
        )
        (tmp_path / "wtY.yaml").write_text("x")

        class _Cfg:
            pass

        monkeypatch.setattr(m.cfg, "load_config", lambda: _Cfg())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight())
        monkeypatch.setattr(
            m, "_build_launch_cmd", lambda cfg_, args, wd, **k: ["copilot"])
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})

        rc = m.cmd_handoff_cutover(
            _ns(seed="continue", dry_run=True, session_id="sess-head")
        )

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["old_pane"] == "%bound"

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
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight())
        monkeypatch.setattr(m, "_build_launch_cmd",
                            lambda c, a, wd, **k: ["copilot"])
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})

        captured = {}

        def _fake_new_window(wt, wd, cmd, env, **k):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["kwargs"] = k
            return {
                "ok": True,
                "new_pane": "%5",
                "prompt_received": True,
                "error": None,
            }

        monkeypatch.setattr(sessions, "mux_new_window", _fake_new_window)
        monkeypatch.setattr(
            m,
            "_wait_for_handoff_candidate",
            lambda *a, **k: ("successor-session", "session-associated"),
        )
        rc = m.cmd_handoff_cutover(_ns(
            seed="resume the multi word work",
            old_pane="%2",
            handoff_token="task-123",
        ))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["ok"] is True
        assert out["old_pane"] == "%2"
        assert out["new_pane"] == "%5"
        assert out["seed_len"] == len("resume the multi word work")
        assert out["seeded"] is True
        # The launch cmd carries NO seed arg; the wrapper receives base64 through
        # the mux window environment and appends native --interactive afterward.
        assert captured["cmd"] == ["copilot"]
        assert captured["kwargs"]["initial_prompt"] == (
            "resume the multi word work"
        )
        assert captured["env"]["AGENT_WORKTREES_HANDOFF_TOKEN"] == "task-123"
        assert out["seed_method"] == "interactive-argv"

    def test_spawn_threads_verified_predecessor_executable(
        self, monkeypatch, capfd, tmp_path,
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtZ")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        (tmp_path / "wtZ.yaml").write_text("x", encoding="utf-8")
        monkeypatch.setattr(m.cfg, "load_config", lambda: object())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight()
        )
        monkeypatch.setattr(
            sessions,
            "mux_binding_for_session",
            lambda sid: {
                "session_name": "wt-wtZ",
                "pane_id": "%2",
                "copilot_pid": 4242,
                "copilot_start_time": "created-1",
            },
        )
        monkeypatch.setattr(
            procs,
            "process_executable_path",
            lambda pid: r"C:\Programs\Copilot\copilot.exe.old-123",
        )
        monkeypatch.setattr(
            procs,
            "copilot_relaunch_path",
            lambda path: r"C:\Programs\Copilot\copilot.exe",
        )
        monkeypatch.setattr(
            locks, "process_start_time", lambda pid: "created-1"
        )
        captured = {}

        def _build(config, args, work_dir, **kwargs):
            captured.update(kwargs)
            return ["copilot"]

        monkeypatch.setattr(m, "_build_launch_cmd", _build)
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
        monkeypatch.setattr(
            sessions,
            "mux_new_window",
            lambda *a, **k: {
                "ok": True,
                "new_pane": "%5",
                "prompt_received": True,
            },
        )

        rc = m.cmd_handoff_cutover(
            _ns(seed="continue", session_id="session-1")
        )

        assert rc == 0
        assert captured["fallback_copilot_path"] == (
            r"C:\Programs\Copilot\copilot.exe"
        )
        assert json.loads(capfd.readouterr().out)["old_pane"] == "%2"

    def test_spawn_drops_executable_when_process_identity_changes(
        self, monkeypatch, capfd, tmp_path,
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtZ")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        monkeypatch.setattr(sessions, "mux_active_pane", lambda w: "%2")
        (tmp_path / "wtZ.yaml").write_text("x", encoding="utf-8")
        monkeypatch.setattr(m.cfg, "load_config", lambda: object())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight()
        )
        monkeypatch.setattr(
            sessions,
            "mux_binding_for_session",
            lambda sid: {
                "session_name": "wt-wtZ",
                "pane_id": "%2",
                "copilot_pid": 4242,
                "copilot_start_time": "created-1",
            },
        )
        monkeypatch.setattr(
            procs, "process_executable_path", lambda pid: "/opt/copilot"
        )
        monkeypatch.setattr(
            procs, "copilot_relaunch_path", lambda path: path
        )
        monkeypatch.setattr(
            locks, "process_start_time", lambda pid: "created-2"
        )
        captured = {}

        def _build(config, args, work_dir, **kwargs):
            captured.update(kwargs)
            return ["copilot"]

        monkeypatch.setattr(m, "_build_launch_cmd", _build)
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
        monkeypatch.setattr(
            sessions,
            "mux_new_window",
            lambda *a, **k: {
                "ok": True,
                "new_pane": "%5",
                "prompt_received": True,
            },
        )

        rc = m.cmd_handoff_cutover(
            _ns(seed="continue", session_id="session-1")
        )

        assert rc == 0
        assert captured["fallback_copilot_path"] is None
        capfd.readouterr()

    def test_token_launch_requires_associated_successor_session(
        self, monkeypatch, capfd, tmp_path,
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtZ")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        record_path = tmp_path / "wtZ.yaml"
        record_path.write_text("x", encoding="utf-8")
        monkeypatch.setattr(m.cfg, "load_config", lambda: object())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight()
        )
        monkeypatch.setattr(m, "_build_launch_cmd", lambda *a, **k: ["copilot"])
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
        monkeypatch.setattr(
            sessions,
            "mux_new_window",
            lambda *a, **k: {
                "ok": True,
                "new_pane": "%5",
                "prompt_received": True,
            },
        )
        monkeypatch.setattr(
            m,
            "_wait_for_handoff_candidate",
            lambda *a, **k: (None, "pane-exited-before-session"),
        )
        monkeypatch.setattr(
            sessions, "_mux_pane_process_tree", lambda pane: {100, 101}
        )
        monkeypatch.setattr(
            sessions,
            "_retire_failed_successor",
            lambda pane, tree: {"ok": True, "pane": pane, "tree": sorted(tree)},
        )

        rc = m.cmd_handoff_cutover(
            _ns(
                seed="continue",
                session_id="session-1",
                handoff_token="task-123",
            )
        )

        assert rc == 4
        out = json.loads(capfd.readouterr().out)
        assert out["ok"] is False
        assert out["candidate_status"] == "pane-exited-before-session"
        assert out["cleanup"]["tree"] == [100, 101]

    def test_token_launch_returns_associated_successor_session(
        self, monkeypatch, capfd, tmp_path,
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtZ")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        record_path = tmp_path / "wtZ.yaml"
        record_path.write_text("x", encoding="utf-8")
        monkeypatch.setattr(m.cfg, "load_config", lambda: object())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight()
        )
        monkeypatch.setattr(m, "_build_launch_cmd", lambda *a, **k: ["copilot"])
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
        monkeypatch.setattr(
            sessions,
            "mux_new_window",
            lambda *a, **k: {
                "ok": True,
                "new_pane": "%5",
                "prompt_received": True,
            },
        )
        monkeypatch.setattr(
            m,
            "_wait_for_handoff_candidate",
            lambda *a, **k: ("successor-session", "session-associated"),
        )

        rc = m.cmd_handoff_cutover(
            _ns(
                seed="continue",
                session_id="session-1",
                handoff_token="task-123",
            )
        )

        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["candidate_session"] == "successor-session"

    def test_spawn_failure_preserves_prompt_receipt_details(
        self, monkeypatch, capfd, tmp_path,
    ):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wtZ")
        monkeypatch.setattr(sessions, "has_mux_session", lambda w: True)
        monkeypatch.setattr(sessions, "mux_active_pane", lambda w: "%2")
        (tmp_path / "wtZ.yaml").write_text("x", encoding="utf-8")
        monkeypatch.setattr(m.cfg, "load_config", lambda: object())
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)

        class _Rec:
            worktree_path = str(tmp_path / "w")

        monkeypatch.setattr(m.tracking, "load_record", lambda p: _Rec())
        monkeypatch.setattr(
            m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight(),
        )
        monkeypatch.setattr(
            m, "_build_launch_cmd", lambda *a, **k: ["copilot"],
        )
        monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
        monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
        monkeypatch.setattr(
            sessions,
            "mux_new_window",
            lambda *a, **k: {
                "ok": False,
                "new_pane": "%5",
                "prompt_received": False,
                "prompt_status": "failed:pane-exited",
                "error": "successor exited during startup",
            },
        )

        rc = m.cmd_handoff_cutover(_ns(seed="continue"))

        assert rc == 4
        out = json.loads(capfd.readouterr().out)
        assert out["ok"] is False
        assert out["prompt_received"] is False
        assert out["prompt_status"] == "failed:pane-exited"
        assert out["error"] == (
            "failed to open successor window: "
            "successor exited during startup"
        )
