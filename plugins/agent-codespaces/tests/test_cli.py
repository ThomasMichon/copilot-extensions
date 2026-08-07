"""Tests for CLI entry point."""

from __future__ import annotations

from unittest.mock import patch


from agent_codespaces.__main__ import main


def test_relay_listening_detects_open_and_closed_ports():
    """#122: _relay_listening must return True for a bound port and False for a
    closed one, so the ssh path can warn loudly when the relay is down."""
    import socket

    from agent_codespaces.__main__ import _relay_listening

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert _relay_listening(port) is True
    finally:
        srv.close()
    # Port is now closed -> not listening.
    assert _relay_listening(port) is False


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


# --- Top-level --project (command-surface <repo> <slug> surface) ---


class TestProjectFlag:
    def test_project_flag_triggers_chdir(self, monkeypatch):
        import agent_codespaces.__main__ as cm

        seen = {}
        monkeypatch.setattr(
            cm, "_chdir_to_project",
            lambda p: seen.setdefault("project", p) or True,
        )
        rc = cm.main(["--project", "demo", "version"])
        assert rc == 0
        assert seen["project"] == "demo"

    def test_no_project_flag_no_chdir(self, monkeypatch):
        import agent_codespaces.__main__ as cm

        calls = {"n": 0}
        monkeypatch.setattr(
            cm, "_chdir_to_project",
            lambda p: calls.__setitem__("n", calls["n"] + 1) or True,
        )
        rc = cm.main(["version"])
        assert rc == 0
        assert calls["n"] == 0

    def test_chdir_to_project_success(self, monkeypatch, tmp_path):
        import os
        import shutil
        import subprocess
        from pathlib import Path

        import agent_codespaces.__main__ as cm

        class _R:
            returncode = 0
            stdout = str(tmp_path) + "\n"

        monkeypatch.setattr(shutil, "which", lambda name: "agent-worktrees")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        orig = os.getcwd()
        try:
            assert cm._chdir_to_project("demo") is True
            assert Path(os.getcwd()).resolve() == tmp_path.resolve()
        finally:
            os.chdir(orig)

    def test_chdir_to_project_unresolvable_warns(self, monkeypatch, capsys):
        import os
        import shutil
        import subprocess

        import agent_codespaces.__main__ as cm

        class _R:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(shutil, "which", lambda name: "agent-worktrees")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        orig = os.getcwd()
        assert cm._chdir_to_project("nope") is False
        assert os.getcwd() == orig
        assert "nope" in capsys.readouterr().err

    def test_chdir_to_project_no_binstub_warns(self, monkeypatch, capsys):
        import os
        import shutil

        import agent_codespaces.__main__ as cm

        monkeypatch.setattr(shutil, "which", lambda name: None)
        orig = os.getcwd()
        assert cm._chdir_to_project("demo") is False
        assert os.getcwd() == orig
        assert "not found on PATH" in capsys.readouterr().err


# --- provision-command / relay-launch-env seams (dotfiles #892 Increment 1) ---

class TestDecouplingSeams:
    def test_provision_command_prints_bash(self, capsys):
        with patch(
            "agent_codespaces.codespace_assets.build_provision_command",
            return_value="echo provision",
        ):
            rc = main(["provision-command"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "echo provision"

    def test_relay_launch_env_prints_json(self, capsys):
        import json as _json

        with patch(
            "agent_codespaces.relay_launch.build_relay_launch_env",
            return_value=("export TOKEN=x", 50123),
        ) as m:
            rc = main(["relay-launch-env", "my-cs", "--relay-port", "50123"])
        assert rc == 0
        assert _json.loads(capsys.readouterr().out) == {
            "prelude": "export TOKEN=x", "port": 50123,
        }
        m.assert_called_once_with("my-cs", relay_port=50123)

    def test_relay_launch_env_defaults_port_none(self, capsys):
        with patch(
            "agent_codespaces.relay_launch.build_relay_launch_env",
            return_value=("", 9857),
        ) as m:
            rc = main(["relay-launch-env", "my-cs"])
        assert rc == 0
        m.assert_called_once_with("my-cs", relay_port=None)


# --- namespace-* resolver seam (dotfiles #892 Increment 3) ---------------

class TestNamespaceSeams:
    def test_namespace_list_json(self, capsys):
        import json as _json

        async def _list_specs(self):
            return [{"name": "cs-a", "display_name": "A", "description": "d",
                     "icon": "codespace", "state": "available", "aliases": []}]

        with patch("agent_codespaces.resolver.CodespaceResolver.list_specs", _list_specs):
            rc = main(["namespace-list"])
        assert rc == 0
        assert _json.loads(capsys.readouterr().out)[0]["name"] == "cs-a"

    def test_namespace_resolve_json_and_argv(self, capsys):
        import json as _json
        seen = {}

        async def _spec(self, name, *, extra_plugin_sources=(), repo=None, repo_remote=None):
            seen["args"] = (name, list(extra_plugin_sources), repo, repo_remote)
            return {"type": "command", "spawn_command": ["ssh", name], "user": "u"}

        with patch("agent_codespaces.resolver.CodespaceResolver.resolve_spec", _spec):
            rc = main(["namespace-resolve", "cs-a", "--repo", "o/r",
                       "--repo-remote", "https://x/r.git", "--stage-plugin", "/p/one"])
        assert rc == 0
        assert _json.loads(capsys.readouterr().out)["spawn_command"] == ["ssh", "cs-a"]
        assert seen["args"] == ("cs-a", ["/p/one"], "o/r", "https://x/r.git")

    def test_namespace_resolve_not_found_exit3(self, capsys):
        async def _spec(self, name, **kw):
            raise KeyError(name)

        with patch("agent_codespaces.resolver.CodespaceResolver.resolve_spec", _spec):
            assert main(["namespace-resolve", "nope"]) == 3

    def test_namespace_resolve_bad_state_exit4(self, capsys):
        async def _spec(self, name, **kw):
            raise ValueError("bad state")

        with patch("agent_codespaces.resolver.CodespaceResolver.resolve_spec", _spec):
            assert main(["namespace-resolve", "cs-a"]) == 4

    def test_namespace_target_repo(self, capsys):
        async def _tr(self, name):
            return "owner/name"

        with patch("agent_codespaces.resolver.CodespaceResolver.target_repo", _tr):
            rc = main(["namespace-target-repo", "cs-a"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "owner/name"

    def test_namespace_ensure_ready_ok_and_fail(self, capsys):
        async def _ok(self, name):
            return None

        with patch("agent_codespaces.resolver.CodespaceResolver.ensure_ready", _ok):
            assert main(["namespace-ensure-ready", "cs-a"]) == 0

        async def _fail(self, name):
            raise RuntimeError("nope")

        with patch("agent_codespaces.resolver.CodespaceResolver.ensure_ready", _fail):
            assert main(["namespace-ensure-ready", "cs-a"]) == 1
