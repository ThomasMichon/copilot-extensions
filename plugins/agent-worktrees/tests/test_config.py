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
        for name in ["my-project", "dotfiles", "sample_project", "test.123"]:
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

    def test_repo_config_pr_defaults_disabled(self):
        repo = cfg.RepoConfig(anchor="/tmp/repo", worktree_root="/tmp/wt")
        assert repo.pr.enabled is False
        assert repo.pr.provider == "gitea"
        assert repo.pr.strategy == "detach"
        assert repo.pr.branch_prefix == "feature"

    def test_pr_config_defaults(self):
        pr = cfg.PRConfig()
        assert pr.enabled is False
        assert pr.provider == "gitea"


# ---------------------------------------------------------------------------
# pr-workflow config parsing
# ---------------------------------------------------------------------------

class TestPRConfigParsing:
    def _write(self, path: Path, pr_block: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
            f"{pr_block}"
        )

    def test_pr_absent_defaults_disabled(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.repos["ext"].pr.enabled is False

    def test_pr_block_parsed(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      provider: github\n"
            "      strategy: keep-alive\n"
            "      branch_prefix: pr\n",
        )
        conf = cfg.load_config(cfgfile)
        pr = conf.repos["ext"].pr
        assert pr.enabled is True
        assert pr.provider == "github"
        assert pr.strategy == "keep-alive"
        assert pr.branch_prefix == "pr"


# ---------------------------------------------------------------------------
# worktree_root derivation (Copilot-aligned <anchor>.worktrees layout)
# ---------------------------------------------------------------------------

class TestWorktreeRootDerivation:
    def test_derive_helper_posix(self):
        assert cfg.derive_worktree_root("/tmp/src/ext") == "/tmp/src/ext.worktrees"

    def test_derive_helper_windows(self):
        assert (
            cfg.derive_worktree_root(r"D:\Src\dotfiles")
            == r"D:\Src\dotfiles.worktrees"
        )

    def test_derive_helper_strips_trailing_separator(self):
        assert cfg.derive_worktree_root("/tmp/src/ext/") == "/tmp/src/ext.worktrees"

    def _write(self, path: Path, worktree_root_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            f"{worktree_root_line}"
            "    default_branch: main\n"
            "    remote: origin\n"
        )

    def test_worktree_root_derived_when_absent(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.repos["ext"].worktree_root == "/tmp/src/ext.worktrees"

    def test_worktree_root_explicit_overrides(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "    worktree_root: /custom/wt/ext\n")
        conf = cfg.load_config(cfgfile)
        assert conf.repos["ext"].worktree_root == "/custom/wt/ext"


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


# ---------------------------------------------------------------------------
# auto_fast_forward parsing
# ---------------------------------------------------------------------------

class TestAutoFastForwardConfig:
    def _write(self, path: Path, extra_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
            "platform: wsl\n"
            f"{extra_line}"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
        )

    def test_defaults_true_when_absent(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.auto_fast_forward is True

    def test_opt_out_false(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "auto_fast_forward: false\n")
        conf = cfg.load_config(cfgfile)
        assert conf.auto_fast_forward is False


# ---------------------------------------------------------------------------
# find_machine_entry -- hostnames are case-insensitive
# ---------------------------------------------------------------------------

class TestFindMachineEntry:
    def _entries(self):
        return {
            "CPC-tmich-OIXUI": cfg.MachineEntry(
                key="CPC-tmich-OIXUI",
                display_name="Dev Box",
                environment="Windows 11",
            ),
        }

    def test_exact_key(self):
        e = self._entries()
        assert cfg.find_machine_entry(e, "CPC-tmich-OIXUI") is not None

    def test_lowercased_key_matches(self):
        # register probes the hostname lowercased; it must still match a
        # mixed-case machines.yaml key.
        e = self._entries()
        assert cfg.find_machine_entry(e, "cpc-tmich-oixui") is not None

    def test_alias_case_insensitive(self):
        e = {
            "host1": cfg.MachineEntry(
                key="host1", display_name="H1", environment="x",
                alias="MyBox",
            ),
        }
        assert cfg.find_machine_entry(e, "mybox") is not None

    def test_no_match_returns_none(self):
        assert cfg.find_machine_entry(self._entries(), "other") is None

