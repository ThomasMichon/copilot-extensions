"""Tests for the file-based --remote-cmd dispatch + version-stable binstub routing.

Covers the fix that stops baking a pruned versioned interpreter into a codespace
target's persisted spawn_command (see dotfiles #1724): the ACP launch payload is
written to a durable file and passed by path, so the spawn can route through the
version-stable binstub instead of ``sys.executable``.
"""

from __future__ import annotations

import argparse

import pytest

from agent_codespaces import _invoke, resolver
from agent_codespaces.__main__ import _normalize_remote_cmd_file


class TestDispatchArgv:
    def test_prefers_binstub_when_present(self, monkeypatch):
        monkeypatch.setattr(_invoke, "binstub", lambda: "/x/.local/bin/agent-codespaces")
        assert _invoke.dispatch_argv() == ["/x/.local/bin/agent-codespaces"]

    def test_falls_back_to_module_argv_when_absent(self, monkeypatch):
        monkeypatch.setattr(_invoke, "binstub", lambda: None)
        monkeypatch.setattr(_invoke, "module_argv", lambda: ["py", "-m", "agent_codespaces"])
        assert _invoke.dispatch_argv() == ["py", "-m", "agent_codespaces"]

    def test_binstub_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_invoke, "_BIN_DIR", tmp_path / "bin")
        assert _invoke.binstub() is None


class TestWriteRemoteCmdFile:
    def test_durable_path_not_temp(self):
        # The real dispatch dir lives under ~/.agent-codespaces, never the OS
        # temp dir (which is swept -- the whole point of a durable path).
        p = str(resolver._DISPATCH_DIR).lower()
        assert ".agent-codespaces" in p
        assert "temp" not in p and "\\tmp" not in p and "/tmp" not in p

    def test_writes_content_and_is_deterministic(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resolver, "_DISPATCH_DIR", tmp_path / "dispatch")
        payload = "cd /workspaces/x && copilot --acp --stdio --allow-all-tools"

        p1 = resolver._write_remote_cmd_file("cs-abc", payload)
        p2 = resolver._write_remote_cmd_file("cs-abc", payload)
        from pathlib import Path

        assert p1 == p2  # same codespace + content -> same file
        assert Path(p1).read_text(encoding="utf-8") == payload

    def test_distinct_payloads_do_not_collide(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resolver, "_DISPATCH_DIR", tmp_path / "dispatch")
        a = resolver._write_remote_cmd_file("cs-abc", "payload-A")
        b = resolver._write_remote_cmd_file("cs-abc", "payload-B")
        assert a != b


class TestBuildSpawnCommand:
    def test_uses_remote_cmd_file_not_string(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resolver, "_DISPATCH_DIR", tmp_path / "dispatch")
        monkeypatch.setattr(resolver, "dispatch_argv", lambda: ["agent-codespaces.cmd"])
        payload = "cd /workspaces/x && copilot --acp --stdio"

        cmd = resolver._build_spawn_command("cs-1", payload, stage_plugins=["p@m"])

        assert "--remote-cmd" not in cmd  # never the raw string form
        assert "--remote-cmd-file" in cmd
        idx = cmd.index("--remote-cmd-file")
        from pathlib import Path

        assert Path(cmd[idx + 1]).read_text(encoding="utf-8") == payload
        # routes through the (stubbed) version-stable dispatcher + keeps flags
        assert cmd[0] == "agent-codespaces.cmd"
        assert cmd[1:5] == ["ssh", "cs-1", "--stdio", "--force"]
        assert "--stage-plugin" in cmd and "p@m" in cmd


class TestNormalizeRemoteCmdFile:
    def _parser(self):
        return argparse.ArgumentParser()

    def test_reads_file_into_remote_cmd(self, tmp_path):
        f = tmp_path / "payload.remotecmd"
        f.write_text("  the-remote-command  \n", encoding="utf-8")
        args = argparse.Namespace(remote_cmd_file=str(f), remote_cmd=None)

        _normalize_remote_cmd_file(self._parser(), args)

        assert args.remote_cmd == "the-remote-command"

    def test_noop_when_absent(self):
        args = argparse.Namespace(remote_cmd_file=None, remote_cmd=None)
        _normalize_remote_cmd_file(self._parser(), args)
        assert args.remote_cmd is None

    def test_mutually_exclusive(self, tmp_path):
        f = tmp_path / "p"
        f.write_text("x", encoding="utf-8")
        args = argparse.Namespace(remote_cmd_file=str(f), remote_cmd="already-set")
        with pytest.raises(SystemExit):
            _normalize_remote_cmd_file(self._parser(), args)

    def test_missing_file_errors(self, tmp_path):
        args = argparse.Namespace(
            remote_cmd_file=str(tmp_path / "gone.remotecmd"), remote_cmd=None
        )
        with pytest.raises(SystemExit):
            _normalize_remote_cmd_file(self._parser(), args)
