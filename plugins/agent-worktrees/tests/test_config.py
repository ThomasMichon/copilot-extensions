"""Tests for agent_worktrees.config — platform detection and path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    def test_returns_string(self):
        result = cfg.detect_platform()
        assert result in ("windows", "wsl", "linux")

    def test_wsl_detection(self, tmp_path: Path, monkeypatch):
        """If /proc/version contains 'microsoft', detect as WSL."""
        proc_version = tmp_path / "proc_version"
        proc_version.write_text("Linux version 5.15.0-microsoft-standard")

        import io
        real_open = open

        def fake_open(f, *args, **kwargs):
            if str(f) == "/proc/version":
                return io.StringIO(proc_version.read_text())
            return real_open(f, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        assert cfg.detect_platform() == "wsl"


# ---------------------------------------------------------------------------
# project_name
# ---------------------------------------------------------------------------

class TestProjectName:
    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("WORKTREE_PROJECT", "test-project")
        assert cfg.project_name() == "test-project"

    def test_raises_when_unset(self, monkeypatch):
        monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
        with pytest.raises(RuntimeError, match="WORKTREE_PROJECT"):
            cfg.project_name()

    def test_raises_on_invalid_name(self, monkeypatch):
        monkeypatch.setenv("WORKTREE_PROJECT", "invalid name with spaces!")
        with pytest.raises(ValueError, match="Invalid"):
            cfg.project_name()

    def test_accepts_valid_names(self, monkeypatch):
        for name in ["my-project", "dotfiles", "aperture_labs", "test.123"]:
            monkeypatch.setenv("WORKTREE_PROJECT", name)
            assert cfg.project_name() == name


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_install_dir(self):
        result = cfg.install_dir()
        assert result.name == ".agent-worktrees"

    def test_project_dir_with_name(self):
        result = cfg.project_dir("my-project")
        assert result.name == ".my-project"

    def test_tracking_dir(self, monkeypatch):
        monkeypatch.setenv("WORKTREE_PROJECT", "test-proj")
        result = cfg.tracking_dir()
        assert result.name == "worktrees"
        assert ".test-proj" in str(result)


# ---------------------------------------------------------------------------
# Data model basics
# ---------------------------------------------------------------------------

class TestDataModels:
    def test_copilot_profile_defaults(self):
        profile = cfg.CopilotProfile(name="test", label="Test")
        assert profile.name == "test"
        assert profile.label == "Test"

    def test_repo_config(self):
        repo = cfg.RepoConfig(
            anchor="/tmp/repo",
            worktree_root="/tmp/worktrees",
            remote="origin",
            default_branch="main",
        )
        assert repo.anchor == "/tmp/repo"
        assert repo.remote == "origin"


# ---------------------------------------------------------------------------
# headless project parsing
# ---------------------------------------------------------------------------

class TestHeadlessConfig:
    def _write(self, path: Path, headless_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
            "platform: wsl\n"
            f"{headless_line}"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
        )

    def test_headless_true(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "headless: true\n")
        conf = cfg.load_config(cfgfile)
        assert conf.headless is True

    def test_headless_absent_defaults_false(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.headless is False
