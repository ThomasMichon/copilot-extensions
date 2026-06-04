"""Tests for CodespaceSource SSH config provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_codespaces.codespace_config import CodespaceSource

# Sample output from `gh codespace ssh --config`
SAMPLE_GH_CONFIG = """\
Host cs.fluffy-parakeet-abc123.org-my--repo
    User codespace
    ProxyCommand gh cs ssh -c fluffy-parakeet-abc123 --stdio -- -i /tmp/key
    UserKnownHostsFile=/dev/null
    StrictHostKeyChecking no
    LogLevel quiet
    ControlMaster auto
    IdentityFile /tmp/key
"""


class TestCodespaceSource:
    def test_implements_protocol(self):
        from ssh_manager import ConfigSource
        source = CodespaceSource("test-cs")
        assert isinstance(source, ConfigSource)

    @patch("agent_codespaces.codespace_config.subprocess.run")
    def test_refresh_parses_gh_output(self, mock_run, tmp_path, monkeypatch):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=SAMPLE_GH_CONFIG,
            stderr="",
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.SSH_CONFIG_DIR", tmp_path
        )

        source = CodespaceSource("fluffy-parakeet-abc123")
        config = source.refresh()

        assert config.host_alias == "cs.fluffy-parakeet-abc123.org-my--repo"
        assert config.user == "codespace"
        assert "gh cs ssh" in (config.proxy_command or "")
        assert config.config_file is not None

    @patch("agent_codespaces.codespace_config.subprocess.run")
    def test_get_ssh_config_caches(self, mock_run, tmp_path, monkeypatch):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=SAMPLE_GH_CONFIG,
            stderr="",
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.SSH_CONFIG_DIR", tmp_path
        )

        source = CodespaceSource("fluffy-parakeet-abc123")
        c1 = source.get_ssh_config()
        c2 = source.get_ssh_config()

        # Should call gh only once
        assert mock_run.call_count == 1
        assert c1 is c2

    @patch("agent_codespaces.codespace_config.subprocess.run")
    def test_gh_not_found_raises(self, mock_run):
        mock_run.side_effect = FileNotFoundError("gh not found")
        source = CodespaceSource("test-cs")
        with pytest.raises(RuntimeError, match="gh CLI not found"):
            source.refresh()

    @patch("agent_codespaces.codespace_config.subprocess.run")
    def test_gh_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="not authenticated",
        )
        source = CodespaceSource("test-cs")
        with pytest.raises(RuntimeError, match="not authenticated"):
            source.refresh()

    def test_parse_skips_control_master_options(self):
        """ControlMaster/ControlPath from gh output should be skipped."""
        source = CodespaceSource("test")
        parsed = source._parse_ssh_config(SAMPLE_GH_CONFIG)
        extra = parsed.get("extra_options", {})
        assert "ControlMaster" not in extra
        assert "ControlPath" not in extra


class TestParseSSHConfig:
    """Edge cases in SSH config parsing."""

    def test_key_equals_value_format(self):
        config = "Host test\n    UserKnownHostsFile=/dev/null\n"
        source = CodespaceSource("test")
        parsed = source._parse_ssh_config(config)
        assert parsed["host_alias"] == "test"
        assert "UserKnownHostsFile" in parsed["extra_options"]

    def test_missing_host_raises(self):
        source = CodespaceSource("test")
        with pytest.raises(RuntimeError, match="Could not parse Host"):
            source._parse_ssh_config("User nobody\n")
