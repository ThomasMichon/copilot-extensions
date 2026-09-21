"""Tests for `copilot`: deliver a TTY Copilot session in this terminal.

`cmd_copilot` reuses `cmd_embody`'s full create-or-resume logic in-process
(only its JSON result matters here, not its stdout) and then hands the
terminal to the resulting mux session via `os.execvp`. These tests mock
`cmd_embody` and `os.execvp` at the boundary so no real worktree, mux
session, or process replacement happens.
"""

from __future__ import annotations

import argparse
import json

from agent_worktrees import __main__ as m


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "worktree_id": None, "new": False, "codename": None, "seed": None,
        "seed_ready_timeout": 180.0, "driver": None, "recovery": False,
        "ensure_mux": False, "mux": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestCmdCopilot:
    def test_requires_a_controlling_terminal(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: False)
        rc = m.cmd_copilot(_ns(worktree_id="wt1"))
        assert rc == 2
        assert "controlling terminal" in capfd.readouterr().out

    def test_execs_attach_on_success(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            print(json.dumps({"ok": True, "session": "wt-abc", "worktree_id": "abc"}))
            return 0

        monkeypatch.setattr(m, "cmd_embody", fake_embody)
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
            print(json.dumps({"ok": False, "error": "Worktree not found: nope"}))
            return 1

        monkeypatch.setattr(m, "cmd_embody", fake_embody)
        rc = m.cmd_copilot(_ns(worktree_id="nope"))
        assert rc == 1
        assert "Worktree not found: nope" in capfd.readouterr().err

    def test_missing_session_key_is_an_error(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            print(json.dumps({"ok": True}))  # malformed: no "session" key
            return 0

        monkeypatch.setattr(m, "cmd_embody", fake_embody)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 1
        assert "no session name" in capfd.readouterr().out

    def test_unparseable_embody_output_is_an_error(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            print("not json")
            return 0

        monkeypatch.setattr(m, "cmd_embody", fake_embody)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 1
        assert "could not parse" in capfd.readouterr().out

    def test_execvp_failure_is_reported_not_raised(self, monkeypatch, capfd):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            print(json.dumps({"ok": True, "session": "wt-abc"}))
            return 0

        monkeypatch.setattr(m, "cmd_embody", fake_embody)
        monkeypatch.setattr(m.sessions, "_mux_bin", lambda mux=None: "tmux")

        def fake_execvp(bin_, argv):
            raise OSError("tmux not found")

        monkeypatch.setattr(m.os, "execvp", fake_execvp)
        rc = m.cmd_copilot(_ns(worktree_id="abc"))
        assert rc == 1
        assert "could not attach" in capfd.readouterr().out

    def test_mux_override_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(m.sys.stdin, "isatty", lambda: True)

        def fake_embody(args):
            print(json.dumps({"ok": True, "session": "wt-abc"}))
            return 0

        monkeypatch.setattr(m, "cmd_embody", fake_embody)
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
