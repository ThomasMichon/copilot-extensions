"""Tests for CodeSpace lifecycle management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_codespaces.lifecycle import (
    CodespaceInfo,
    cleanup_stale,
    create_codespace,
    delete_codespace,
    list_codespaces,
)
from agent_codespaces.config import CodespacesConfig, RepoConfig


class TestListCodespaces:
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_parses_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "name": "fluffy-parakeet-abc",
                "displayName": "My CS",
                "repository": "org/repo",
                "gitStatus": {"ref": "main"},
                "state": "Available",
                "machine": "largePremiumLinux",
            }]),
        )
        result = list_codespaces()
        assert len(result) == 1
        assert result[0].name == "fluffy-parakeet-abc"
        assert result[0].branch == "main"
        assert result[0].state == "Available"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_empty_list(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        result = list_codespaces()
        assert result == []

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_gh_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth failed")
        with pytest.raises(RuntimeError, match="auth failed"):
            list_codespaces()


class TestCreateCodespace:
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_uses_config_defaults(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="new-codespace-name\n"
        )
        config = CodespacesConfig(
            default_machine_type="bigMachine",
            default_location="WestUs2",
        )
        result = create_codespace("org/repo", config)
        assert result.name == "new-codespace-name"

        call_args = mock_run.call_args[0][0]
        assert "--machine" in call_args
        idx = call_args.index("--machine")
        assert call_args[idx + 1] == "bigMachine"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_per_repo_overrides(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="cs-name\n"
        )
        config = CodespacesConfig(
            default_machine_type="small",
            repos={"org/repo": RepoConfig(machine_type="huge")},
        )
        create_codespace("org/repo", config)

        call_args = mock_run.call_args[0][0]
        idx = call_args.index("--machine")
        assert call_args[idx + 1] == "huge"


class TestDeleteCodespace:
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_delete_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        delete_codespace("my-cs")  # should not raise

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_delete_force(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        delete_codespace("my-cs", force=True)
        call_args = mock_run.call_args[0][0]
        assert "--force" in call_args


class TestCleanupStale:
    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_removes_stale_ssh_configs(self, mock_list, tmp_path):
        """SSH configs for deleted codespaces are removed."""
        mock_list.return_value = [
            CodespaceInfo(
                name="live-cs-abc",
                display_name="live",
                repository="org/repo",
                branch="main",
                state="Available",
                machine="large",
            ),
        ]

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir()
        live_config = ssh_dir / "live-cs-abc.config"
        live_config.write_text("Host live")
        stale_config = ssh_dir / "deleted-cs-xyz.config"
        stale_config.write_text("Host stale")

        with patch("agent_codespaces.lifecycle.RUNTIME_DIR", tmp_path):
                result = cleanup_stale()

        assert len(result["ssh_configs"]) == 1
        assert "deleted-cs-xyz" in result["ssh_configs"][0]
        assert not stale_config.exists()
        assert live_config.exists()

    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_dry_run_does_not_remove(self, mock_list, tmp_path):
        """Dry run reports but does not delete."""
        mock_list.return_value = []

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir()
        stale_config = ssh_dir / "old-cs.config"
        stale_config.write_text("Host old")

        with patch("agent_codespaces.lifecycle.RUNTIME_DIR", tmp_path):
                result = cleanup_stale(dry_run=True)

        assert len(result["ssh_configs"]) == 1
        assert stale_config.exists()  # Not removed

    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_no_stale_state(self, mock_list, tmp_path):
        """Clean state returns empty results."""
        mock_list.return_value = [
            CodespaceInfo(
                name="my-cs",
                display_name="my",
                repository="org/repo",
                branch="main",
                state="Available",
                machine="large",
            ),
        ]

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir()
        (ssh_dir / "my-cs.config").write_text("Host mine")

        with patch("agent_codespaces.lifecycle.RUNTIME_DIR", tmp_path):
                result = cleanup_stale()

        assert result["ssh_configs"] == []
        assert result["sockets"] == []

    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_handles_list_failure_gracefully(self, mock_list):
        """If gh codespace list fails, cleanup skips without error."""
        mock_list.side_effect = RuntimeError("auth expired")
        result = cleanup_stale()
        assert result["ssh_configs"] == []
        assert result["sockets"] == []
