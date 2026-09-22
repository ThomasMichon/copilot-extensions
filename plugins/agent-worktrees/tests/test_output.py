"""Tests for agent_worktrees.output — formatting helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

from agent_worktrees import output


class TestSupportsColor:
    def test_no_color_env(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert output._supports_color() is False

    def test_force_color_env(self, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert output._supports_color() is True


class TestStdoutToStderr:
    def test_redirects_stdout(self):
        original = sys.stdout
        with output.stdout_to_stderr():
            assert sys.stdout is sys.stderr
        assert sys.stdout is original


class TestCaptureJsonOutput:
    def test_captures_json_output_envelope(self):
        with output.capture_json_output() as buf:
            output._json_output({"ok": True, "value": 1})
        assert '"ok": true' in buf.getvalue()
        assert '"value": 1' in buf.getvalue()

    def test_restores_real_stdout_on_exit(self):
        original = sys.__stdout__
        with output.capture_json_output():
            assert sys.__stdout__ is not original
        assert sys.__stdout__ is original

    def test_restores_real_stdout_on_exception(self):
        original = sys.__stdout__
        try:
            with output.capture_json_output():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert sys.__stdout__ is original

    def test_plain_stdout_write_is_not_captured(self, capfd):
        # A plain `contextlib.redirect_stdout` would swap `sys.stdout` only,
        # which `_json_output` deliberately bypasses (writing to
        # `sys.__stdout__` so it survives `output.stdout_to_stderr()`
        # elsewhere) -- confirmed live as the actual root cause of
        # `cmd_copilot` never parsing a genuine `cmd_embody` result
        # (agent-bridge-cli-mode-sessions Phase 4 validation).
        # `capture_json_output()` swaps `sys.__stdout__` itself, so an
        # ordinary `print()` (still targeting `sys.stdout`) is untouched.
        with output.capture_json_output() as buf:
            print("ordinary progress noise")
        assert buf.getvalue() == ""
        assert "ordinary progress noise" in capfd.readouterr().out


class TestFormatters:
    """Test output formatting functions produce expected text."""

    def test_ok(self, capsys):
        with patch.object(output, "_COLOR", False):
            output.ok("test message")
        captured = capsys.readouterr()
        assert "test message" in captured.out

    def test_err(self, capsys):
        with patch.object(output, "_COLOR", False):
            output.err("error message")
        captured = capsys.readouterr()
        assert "error message" in captured.out

    def test_warn(self, capsys):
        with patch.object(output, "_COLOR", False):
            output.warn("warning")
        captured = capsys.readouterr()
        assert "warning" in captured.out

    def test_header(self, capsys):
        with patch.object(output, "_COLOR", False):
            output.header("Test Section")
        captured = capsys.readouterr()
        assert "Test Section" in captured.out

    def test_dry_run(self, capsys):
        with patch.object(output, "_COLOR", False):
            output.dry_run("would do thing")
        captured = capsys.readouterr()
        assert "dry-run" in captured.out
        assert "would do thing" in captured.out

    def test_info(self, capsys):
        output.info("just info")
        captured = capsys.readouterr()
        assert "just info" in captured.out
