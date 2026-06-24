"""Tests for CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_codespaces.__main__ import main


class TestCLI:
    def test_no_args_shows_help(self, capsys):
        rc = main([])
        assert rc == 1

    def test_version(self, capsys):
        rc = main(["version"])
        assert rc == 0
        assert "0.1.0" in capsys.readouterr().out

    def test_config_validate_no_repos(self, tmp_path, monkeypatch, capsys):
        runtime = tmp_path / ".agent-codespaces"
        runtime.mkdir()
        monkeypatch.setattr("agent_codespaces.config.RUNTIME_DIR", runtime)
        monkeypatch.setattr(
            "agent_codespaces.config.ADOPTED_REPOS_FILE",
            runtime / "adopted-repos.yaml",
        )
        # Also patch in __main__ which imports from config
        monkeypatch.setattr(
            "agent_codespaces.__main__.load_merged_config",
            lambda: __import__("agent_codespaces.config", fromlist=["load_merged_config"]).load_merged_config(),
        )
        rc = main(["config", "validate"])
        assert rc == 1
        assert "No adopted repos" in capsys.readouterr().out

    def test_list_json_empty(self, capsys):
        with patch("agent_codespaces.__main__.list_codespaces", return_value=[]):
            rc = main(["list", "--json"])
        assert rc == 0
        assert "[]" in capsys.readouterr().out

    def test_status_runs(self, tmp_path, monkeypatch, capsys):
        runtime = tmp_path / ".agent-codespaces"
        runtime.mkdir()
        monkeypatch.setattr("agent_codespaces.config.RUNTIME_DIR", runtime)
        monkeypatch.setattr(
            "agent_codespaces.config.ADOPTED_REPOS_FILE",
            runtime / "adopted-repos.yaml",
        )
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", runtime)
        monkeypatch.setattr(
            "agent_codespaces.__main__.ADOPTED_REPOS_FILE",
            runtime / "adopted-repos.yaml",
        )
        rc = main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "agent-codespaces status" in out


class TestDeleteSyncHook:
    def test_delete_syncs_then_deletes(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": True, "session_count": 3, "detail": "-> hub"}) as sync, \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["delete", "cs-1"])
        assert rc == 0
        sync.assert_called_once()
        delete.assert_called_once_with("cs-1", force=False)
        assert "Recovered 3 session(s)" in capsys.readouterr().out

    def test_delete_no_sync_skips_recovery(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions") as sync, \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["delete", "cs-1", "--no-sync"])
        assert rc == 0
        sync.assert_not_called()
        delete.assert_called_once_with("cs-1", force=False)

    def test_delete_continues_when_sync_fails(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": False, "detail": "could not connect"}), \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["delete", "cs-1", "--force"])
        assert rc == 0
        delete.assert_called_once_with("cs-1", force=True)
        assert "Pre-delete session recovery failed" in capsys.readouterr().err


class TestFinalize:
    def test_finalize_sync_only(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": True, "session_count": 5, "detail": "-> hub"}) as sync, \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["finalize", "cs-1"])
        assert rc == 0
        sync.assert_called_once()
        delete.assert_not_called()
        assert "Recovered 5 session(s)" in capsys.readouterr().out

    def test_finalize_delete_after_success(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": True, "session_count": 1, "detail": "ok"}), \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["finalize", "cs-1", "--delete"])
        assert rc == 0
        delete.assert_called_once_with("cs-1", force=False)

    def test_finalize_refuses_delete_on_failed_sync(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": False, "detail": "could not connect"}), \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["finalize", "cs-1", "--delete"])
        assert rc == 1
        delete.assert_not_called()
        assert "Refusing to delete" in capsys.readouterr().err

    def test_finalize_force_delete_on_failed_sync(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": False, "detail": "could not connect"}), \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["finalize", "cs-1", "--delete", "--force"])
        assert rc == 1  # sync failed, but delete still forced
        delete.assert_called_once_with("cs-1", force=True)
