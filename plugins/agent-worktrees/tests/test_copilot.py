"""Tests for `copilot`: deliver a TTY Copilot session in this terminal.

`cmd_copilot` reuses `cmd_embody`'s full create-or-resume logic in-process
(only its JSON result matters here, not its stdout) and then hands the
terminal to the resulting mux session with a foreground Windows wait or
POSIX `os.execvp`. These tests mock
`cmd_embody` and the terminal client boundary so no real worktree or user
mux session is changed. The native control below uses an isolated fake mux.

Fakes emit their JSON via `output._json_output` (not a bare
`print(json.dumps(...))`) to match `cmd_embody`'s real contract: its JSON
envelope is written through `_json_output`, which deliberately targets
`sys.__stdout__` (not `sys.stdout`) so it still reaches the real terminal
from inside an `output.stdout_to_stderr()` block elsewhere in `cmd_embody`.
A fake using plain `print()` only exercises `sys.stdout` and would silently
mask the real bug this module's fix addresses: a `contextlib.redirect_stdout`
capture (which only swaps `sys.stdout`) never sees anything `_json_output`
writes, so `cmd_copilot` always failed to parse a genuine `cmd_embody` result
-- confirmed live (agent-bridge-cli-mode-sessions Phase 4 validation), 100%
reproducible both locally and over a remote venue SSH session, meaning
`copilot` never actually attached to the very mux session it had just
created. Fixed via `output.capture_json_output()`, which swaps
`sys.__stdout__` itself.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import copilot_cli as m
from agent_worktrees import output


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "worktree_id": None, "new": False, "codename": None, "seed": None,
        "seed_ready_timeout": 180.0, "driver": None, "recovery": False,
        "ensure_mux": False, "mux": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestCmdCopilot:
    @pytest.fixture(autouse=True)
    def posix_attachment(self, monkeypatch):
        monkeypatch.setattr(m.sys, "platform", "linux")

    def test_requires_a_controlling_terminal(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: False)
        rc = m.cmd_copilot(_ns(worktree_id="wt1"))
        assert rc == 2
        assert "controlling terminal" in capfd.readouterr().out

    def test_execs_attach_on_success(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            output._json_output({"ok": True, "session": "wt-abc", "worktree_id": "abc"})
            return 0

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "tmux")
        execed = {}

        def fake_execvp(bin_, argv):
            execed["bin"] = bin_
            execed["argv"] = argv

        monkeypatch.setattr(m.os, "execvp", fake_execvp)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 0
        assert execed == {
            "bin": "tmux", "argv": ["tmux", "attach-session", "-t", "wt-abc"],
        }
        # embody's own JSON never leaked to this process's real stdout.
        assert capfd.readouterr().out == ""

    def test_propagates_embody_failure(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            output._json_output({"ok": False, "error": "Worktree not found: nope"})
            return 1

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        rc = m.cmd_copilot(_ns(worktree_id="nope"))
        assert rc == 1
        assert "Worktree not found: nope" in capfd.readouterr().err

    def test_missing_session_key_is_an_error(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            output._json_output({"ok": True})  # malformed: no "session" key
            return 0

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 1
        assert "no session name" in capfd.readouterr().out

    def test_unparseable_embody_output_is_an_error(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            m.sys.__stdout__.write("not json\n")
            return 0

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 1
        assert "could not parse" in capfd.readouterr().out

    def test_execvp_failure_is_reported_not_raised(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            output._json_output({"ok": True, "session": "wt-abc"})
            return 0

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "tmux")

        def fake_execvp(bin_, argv):
            raise OSError("tmux not found")

        monkeypatch.setattr(m.os, "execvp", fake_execvp)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 1
        assert "could not attach" in capfd.readouterr().out

    def test_progress_noise_on_stdout_does_not_corrupt_captured_json(
        self, monkeypatch, capfd,
    ):
        # Regression guard for the actual bug shape: `cmd_embody`'s `--new`
        # path wraps progress logging in `output.stdout_to_stderr()`, which
        # only swaps `sys.stdout` -- ordinary `print()` calls made under it
        # still land wherever `sys.stdout` currently points (the real
        # terminal here, since nothing swaps it in this path) while the
        # final `_json_output` call goes to `sys.__stdout__` regardless.
        # `capture_json_output()` must only capture the latter.
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            print("Fetching latest from origin for repository...")
            output._json_output({"ok": True, "session": "wt-abc"})
            return 0

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "tmux")
        execed = {}
        monkeypatch.setattr(
            m.os, "execvp", lambda bin_, argv: execed.update(bin_=bin_, argv=argv)
        )
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 0
        assert execed["argv"] == ["tmux", "attach-session", "-t", "wt-abc"]
        out = capfd.readouterr().out
        assert "Fetching latest" in out
        assert "worktree_id" not in out  # the JSON envelope was captured, not printed

    def test_mux_override_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            output._json_output({"ok": True, "session": "wt-abc"})
            return 0

        monkeypatch.setattr(m.handoff_cli, "cmd_embody", fake_embody)
        seen = {}
        monkeypatch.setattr(
            m.sessions, "_mux_bin", lambda mux=None: seen.setdefault("mux", mux) or "psmux"
        )
        execed = {}
        monkeypatch.setattr(
            m.os, "execvp", lambda bin_, argv: execed.update(bin_=bin_, argv=argv)
        )
        rc = m.cmd_copilot(_ns(worktree_id="abc", mux="psmux"))
        assert rc == 0
        assert seen["mux"] == "psmux"
        assert execed["argv"][0] == "psmux"

    @pytest.mark.parametrize("exit_code", [0, 7])
    def test_windows_keeps_foreground_custody_until_client_exits(self, monkeypatch, exit_code):
        monkeypatch.setattr(m.sys, "platform", "win32")
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "psmux")
        monkeypatch.setattr(
            m.handoff_cli, "cmd_embody",
            lambda args: output._json_output({"ok": True, "session": "wt-abc"}) or 0,
        )
        monkeypatch.setattr(
            m.os, "execvp",
            lambda *_: pytest.fail("Windows overlay releases the backing console"),
        )
        calls = []

        class Client:
            def wait(self):
                calls.append("wait")
                return exit_code

        def popen(argv, **kwargs):
            assert argv == ["psmux", "attach-session", "-t", "wt-abc"]
            assert kwargs == {}
            calls.append("spawn")
            return Client()

        monkeypatch.setattr(subprocess, "Popen", popen)
        assert m.cmd_copilot(_ns(worktree_id="abc")) == exit_code
        assert calls == ["spawn", "wait"]

    def test_windows_attachment_failure_is_reported(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys, "platform", "win32")
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "psmux")
        monkeypatch.setattr(
            m.handoff_cli, "cmd_embody",
            lambda args: output._json_output({"ok": True, "session": "wt-abc"}) or 0,
        )
        monkeypatch.setattr(
            m.os, "execvp",
            lambda *_: pytest.fail("Windows attach must not use an overlay"),
        )

        def popen(*args, **kwargs):
            raise OSError("attach client refused")

        monkeypatch.setattr(subprocess, "Popen", popen)
        assert m.cmd_copilot(_ns(worktree_id="abc")) == 1
        assert "attach client refused" in capfd.readouterr().out

    @pytest.mark.parametrize("exits_after_interrupt", [True, False])
    def test_windows_interrupt_waits_for_or_reaps_only_its_attach_client(
        self, monkeypatch, exits_after_interrupt,
    ):
        monkeypatch.setattr(m.sys, "platform", "win32")
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "psmux")
        monkeypatch.setattr(
            m.handoff_cli, "cmd_embody",
            lambda args: output._json_output({"ok": True, "session": "wt-abc"}) or 0,
        )
        calls = []

        class Client:
            def wait(self, timeout=None):
                calls.append(("wait", timeout))
                if len(calls) == 1:
                    raise KeyboardInterrupt
                if timeout is not None and not exits_after_interrupt:
                    raise subprocess.TimeoutExpired("attach", timeout)
                return 11

            def kill(self):
                calls.append(("kill", None))

        monkeypatch.setattr(subprocess, "Popen", lambda argv: Client())
        assert m.cmd_copilot(_ns(worktree_id="abc")) == (11 if exits_after_interrupt else 130)
        assert calls[:2] == [("wait", None), ("wait", 30)]
        assert (("kill", None) in calls) is not exits_after_interrupt

    @pytest.mark.parametrize("interrupt_kill", [False, True])
    def test_repeated_interrupts_cannot_release_terminal_before_reaping(
        self, monkeypatch, interrupt_kill,
    ):
        monkeypatch.setattr(m.sys, "platform", "win32")
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "psmux")
        monkeypatch.setattr(
            m.handoff_cli, "cmd_embody",
            lambda args: output._json_output({"ok": True, "session": "wt-abc"}) or 0,
        )
        waits = iter([KeyboardInterrupt(), KeyboardInterrupt(), KeyboardInterrupt(), 7])
        events = []

        class Client:
            def wait(self, timeout=None):
                events.append(("wait", timeout))
                result = next(waits)
                if isinstance(result, BaseException):
                    raise result
                events.append(("reaped", result))
                return result

            def kill(self):
                events.append(("kill", None))
                if interrupt_kill and events.count(("kill", None)) == 1:
                    raise KeyboardInterrupt

        monkeypatch.setattr(subprocess, "Popen", lambda argv: Client())
        try:
            result = m.cmd_copilot(_ns(worktree_id="abc"))
        except KeyboardInterrupt:
            pytest.fail("an interrupt escaped while the attach client still owned the terminal")
        assert result == 130
        assert events[-1] == ("reaped", 7)
        assert events.count(("kill", None)) == (2 if interrupt_kill else 1)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows foreground process contract")
def test_native_windows_attach_retains_inherited_input_and_waits(tmp_path):
    from agent_procutil import no_window_kwargs

    (tmp_path / "attach-session").write_text(
        "import sys\n"
        "print('MUX_INPUT=' + sys.stdin.readline().strip(), flush=True)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import argparse, sys\n"
        "from agent_worktrees import copilot_cli, output\n"
        "sys.stdin.isatty = lambda: True\n"
        "copilot_cli.sessions._mux_bin = lambda mux=None: sys.executable\n"
        "copilot_cli.handoff_cli.cmd_embody = lambda args: "
        "output._json_output({'ok': True, 'session': 'wt-synthetic'}) or 0\n"
        "result = copilot_cli.cmd_copilot(argparse.Namespace(mux=None))\n"
        "print('WRAPPER_EXIT=' + str(result), flush=True)\n"
        "raise SystemExit(result)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path(m.__file__).parents[1])}
    completed = subprocess.run(
        [sys.executable, str(driver)], cwd=tmp_path, env=env,
        input="mux-owned-input\n", capture_output=True, text=True, timeout=20,
        **no_window_kwargs(),
    )
    assert completed.returncode == 7, completed.stderr
    assert completed.stdout.splitlines() == ["MUX_INPUT=mux-owned-input", "WRAPPER_EXIT=7"]
