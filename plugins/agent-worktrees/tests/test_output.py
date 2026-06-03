"""Tests for agent_worktrees.output — formatting helpers."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

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
