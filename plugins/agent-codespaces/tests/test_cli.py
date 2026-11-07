"""Tests for CLI entry point."""

from __future__ import annotations

import importlib
import json
import types
from unittest.mock import patch

import pytest


from agent_codespaces.__main__ import main


@pytest.fixture(scope="module", autouse=True)
def _load_cli_parser_dependencies():
    # Keyring's lazy imports may probe Windows before a command runs.
    importlib.import_module("agent_codespaces.authorize")


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
    @pytest.mark.parametrize("outcome", ["started", "owner-unavailable", "refused", "api-failed", "timeout"])
    def test_start_dispatches_to_owning_account_without_guest_provisioning(
        self, monkeypatch, capsys, outcome,
    ):
        from agent_codespaces import __main__ as cli, activation_cli, lifecycle

        calls = []
        monkeypatch.setattr(cli, "validate_context", lambda: None)
        monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
        monkeypatch.setattr(
            activation_cli.launch_policy, "refused_exit_code",
            lambda name: 79 if outcome == "refused" else None,
        )
        monkeypatch.setattr(
            lifecycle, "get_codespace_status_with_account", lambda name: (True, "Shutdown", "owner"),
        )
        monkeypatch.setattr(
            "agent_codespaces.gh_account.token_for_account",
            lambda account: None if outcome == "owner-unavailable" else "fixture-token",
        )

        def run(argv, **kwargs):
            calls.append(("start", argv, kwargs))
            return types.SimpleNamespace(
                returncode=1 if outcome == "api-failed" else 0, stderr="activation unavailable",
            )

        def wait(name, **kwargs):
            calls.append(("wait", name, kwargs))
            return outcome != "timeout"

        monkeypatch.setattr(activation_cli.subprocess, "run", run)
        monkeypatch.setattr(lifecycle, "wait_for_available", wait)
        assert main(["start", "selected"]) == (0 if outcome == "started" else 79 if outcome == "refused" else 1)
        if outcome in ("owner-unavailable", "refused"):
            assert calls == []
        else:
            assert calls[0][1] == [
                "gh", "api", "--method", "POST", "/user/codespaces/selected/start",
            ]
            assert calls[0][2]["env"]["GH_TOKEN"] == "fixture-token"
            assert "GITHUB_TOKEN" not in calls[0][2]["env"]
            if outcome == "api-failed":
                assert len(calls) == 1
            else:
                assert calls[1] == ("wait", "selected", {"timeout": 180, "interval": 2, "account": "owner"})

    @pytest.mark.parametrize("lookup", ["collision", "missing", "failed"])
    def test_start_keeps_exact_account_binding_through_activation_and_polling(
        self, monkeypatch, lookup,
    ):
        from agent_codespaces import __main__ as cli, account_binding, activation_cli, lifecycle

        calls = []
        monkeypatch.setattr(cli, "validate_context", lambda: None)
        monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
        monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
        monkeypatch.setattr(activation_cli.launch_policy, "refused_exit_code", lambda name: None)
        monkeypatch.setattr(account_binding, "bound_account_or_raise", lambda name: "exact-owner")
        monkeypatch.setattr(lifecycle, "list_codespaces", lambda: pytest.fail("unscoped listing selected"))
        monkeypatch.setattr(
            "agent_codespaces.gh_account.token_for_account",
            lambda account: "fixture-token" if account == "exact-owner" else pytest.fail("wrong account"),
        )

        def status(name, account, **kwargs):
            assert name == "selected" and account == "exact-owner"
            calls.append(("status", account))
            if lookup == "failed":
                raise RuntimeError("bound lookup failed")
            return (False, None) if lookup == "missing" else (
                True, "Shutdown" if len(calls) == 1 else "Available",
            )

        def run(argv, **kwargs):
            assert argv[-1] == "/user/codespaces/selected/start"
            assert kwargs["env"]["GH_TOKEN"] == "fixture-token"
            calls.append(("start", "exact-owner"))
            return types.SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", status)
        monkeypatch.setattr(activation_cli.subprocess, "run", run)
        assert main(["start", "selected"]) == (0 if lookup == "collision" else 1)
        assert calls == (
            [("status", "exact-owner"), ("start", "exact-owner"), ("status", "exact-owner")]
            if lookup == "collision" else [("status", "exact-owner")]
        )

    @pytest.mark.parametrize("outcome", ["started", "identity-failed", "lookup-failed", "missing", "token-failed"])
    def test_start_revalidates_unmapped_venue_under_authenticated_login(
        self, monkeypatch, outcome,
    ):
        from agent_codespaces import __main__ as cli, account_binding, activation_cli, lifecycle

        calls = []
        commands = []
        monkeypatch.setattr(cli, "validate_context", lambda: None)
        monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
        monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
        monkeypatch.setattr(activation_cli.launch_policy, "refused_exit_code", lambda name: None)
        monkeypatch.setattr(account_binding, "bound_account_or_raise", lambda name: None)
        monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
        monkeypatch.setattr("agent_codespaces.gh_account.mapped_accounts", lambda: ())
        monkeypatch.setattr(
            "agent_codespaces.gh_account.token_for_account",
            lambda account: None if outcome == "token-failed" else "fixture-token",
        )

        def status(name, account, **kwargs):
            assert name == "selected"
            calls.append(account)
            if account is None:
                return True, "Shutdown"
            assert account == "authenticated-owner"
            if outcome == "lookup-failed":
                raise RuntimeError("explicit owner lookup failed")
            return (False, None) if outcome == "missing" else (
                True, "Shutdown" if len(calls) == 2 else "Available",
            )

        def run(argv, **kwargs):
            commands.append(argv)
            if argv == ["gh", "api", "user", "--jq", ".login"]:
                return types.SimpleNamespace(
                    returncode=1 if outcome == "identity-failed" else 0,
                    stdout="authenticated-owner\n", stderr="",
                )
            assert argv == ["gh", "api", "--method", "POST", "/user/codespaces/selected/start"]
            assert kwargs["env"]["GH_TOKEN"] == "fixture-token"
            return types.SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", status)
        monkeypatch.setattr(activation_cli.subprocess, "run", run)
        assert main(["start", "selected"]) == (0 if outcome == "started" else 1)
        if outcome == "started":
            assert calls == [None, "authenticated-owner", "authenticated-owner"]
            assert len(commands) == 2
        else:
            assert calls == ([None] if outcome == "identity-failed" else [None, "authenticated-owner"])
            assert commands == [["gh", "api", "user", "--jq", ".login"]]

    def test_no_args_shows_help(self, capsys):
        rc = main([])
        assert rc == 1

    def test_version(self, capsys):
        import agent_codespaces

        rc = main(["version"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == (
            f"agent-codespaces {agent_codespaces.__version__}"
        )

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
        assert "No CodeSpace config found" in capsys.readouterr().out

    def test_list_json_empty(self, capsys):
        with patch("agent_codespaces.__main__._gh_binary_available",
                   return_value=True), \
             patch("agent_codespaces.__main__.list_codespaces", return_value=[]):
            rc = main(["list", "--json"])
        assert rc == 0
        assert "[]" in capsys.readouterr().out

    def test_gh_required_verb_balks_when_gh_missing(self, capsys):
        """dotfiles#1462: a CodeSpace-operating verb must balk with an actionable
        'install gh' message (not an opaque FileNotFoundError) when gh is absent.
        """
        with patch("agent_codespaces.__main__._gh_binary_available",
                   return_value=False):
            rc = main(["list", "--json"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "gh" in err.lower()
        assert "cli.github.com" in err
        # It names the specific verb and points at doctor.
        assert "list" in err
        assert "doctor" in err

    def test_gh_required_verb_proceeds_when_gh_present(self, capsys):
        """The balk must NOT fire when gh is on PATH -- the verb runs normally."""
        with patch("agent_codespaces.__main__._gh_binary_available",
                   return_value=True), \
             patch("agent_codespaces.__main__.list_codespaces", return_value=[]):
            rc = main(["list", "--json"])
        assert rc == 0

    def test_sync_sessions_balks_when_gh_missing(self, capsys):
        """sync-sessions is a gh-dependent verb (status lookup + SSH connect) --
        it must balk the same as every other CodeSpace-operating verb rather
        than fall through to an opaque runtime failure."""
        with patch("agent_codespaces.__main__._gh_binary_available",
                   return_value=False):
            rc = main(["sync-sessions", "cs-a"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "gh" in err.lower()
        assert "sync-sessions" in err

    def test_local_verb_does_not_balk_without_gh(self, capsys):
        """A local/report-only verb (version) must keep working with no gh."""
        with patch("agent_codespaces.__main__._gh_binary_available",
                   return_value=False):
            rc = main(["version"])
        assert rc == 0

    def test_config_migrate_relocates_legacy(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "codespaces.yaml").write_text("defaults:\n  machine_type: big\n")
        monkeypatch.setattr(
            "agent_codespaces.__main__._resolve_repo_root", lambda: repo
        )
        rc = main(["config", "migrate"])
        assert rc == 0
        canonical = repo / ".copilot-extensions" / "agent-codespaces" / "config.yaml"
        assert canonical.exists()
        assert "machine_type: big" in canonical.read_text()
        assert not (repo / "codespaces.yaml").exists()
        assert "Migrated" in capsys.readouterr().out

    def test_config_migrate_relocates_legacy_directory_config(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = tmp_path / "repo"
        legacy = repo / ".agent-codespaces"
        legacy.mkdir(parents=True)
        (legacy / "config.yaml").write_text("defaults:\n  machine_type: big\n")
        monkeypatch.setattr(
            "agent_codespaces.__main__._resolve_repo_root", lambda: repo
        )
        rc = main(["config", "migrate"])
        assert rc == 0
        canonical = repo / ".copilot-extensions" / "agent-codespaces" / "config.yaml"
        assert canonical.exists()
        assert "machine_type: big" in canonical.read_text()
        assert not (legacy / "config.yaml").exists()
        assert "Migrated" in capsys.readouterr().out

    def test_config_migrate_noop_without_legacy(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setattr(
            "agent_codespaces.__main__._resolve_repo_root", lambda: repo
        )
        rc = main(["config", "migrate"])
        assert rc == 0
        assert "nothing to do" in capsys.readouterr().out.lower()

    def test_status_runs(self, tmp_path, monkeypatch, capsys):
        runtime = tmp_path / ".agent-codespaces"
        runtime.mkdir()
        monkeypatch.setattr("agent_codespaces.config.RUNTIME_DIR", runtime)
        monkeypatch.setattr(
            "agent_codespaces.config.ADOPTED_REPOS_FILE",
            runtime / "adopted-repos.yaml",
        )
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", runtime)
        rc = main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "agent-codespaces status" in out

    def test_install_path_config_migrate_does_not_touch_repo_config(
        self, tmp_path, monkeypatch, capsys
    ):
        runtime = tmp_path / ".agent-codespaces"
        runtime.mkdir()
        manifest = runtime / "adopted-repos.yaml"
        manifest.write_text("# adopted repos\nrepos:\n  - path: /tmp/legacy\n", encoding="utf-8")
        repo = tmp_path / "repo"
        repo_config = repo / ".copilot-extensions" / "agent-codespaces" / "config.yaml"
        repo_config.parent.mkdir(parents=True)
        repo_config.write_text("defaults:\n  machine_type: repo-owned\n", encoding="utf-8")

        monkeypatch.setattr("agent_codespaces.config.RUNTIME_DIR", runtime)
        monkeypatch.setattr("agent_codespaces.config.ADOPTED_REPOS_FILE", manifest)

        rc = main(["config-migrate"])

        assert rc == 0
        assert "config-migrate:" in capsys.readouterr().out
        assert repo_config.read_text(encoding="utf-8") == (
            "defaults:\n  machine_type: repo-owned\n"
        )


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


class TestLockWidening:
    """session-rescue-parity Phase 1's recorded lock-widening decision: a
    destructive caller must hold the SSH target lock across its FULL
    sync-then-act sequence, not only the sync sub-step -- these tests
    exercise the real ``ssh_manager.TargetLock`` file (not a synthetic
    stand-in) through the actual `_cmd_delete` dispatch path, proving the
    lock file is still present (held) when the destructive action itself
    runs, and gone (released) only once the whole command completes."""

    def test_delete_holds_lock_across_sync_and_delete(self, capsys):
        seen = {}

        def fake_sync(name, verbose=False, lock=None):
            assert lock is not None, "sync_codespace_sessions must receive the widened lock"
            seen["path"] = lock.path
            assert lock.path.exists(), "the lock must already be held during sync"
            return {"ok": True, "session_count": 1, "detail": "ok"}

        def fake_delete(name, force=False):
            assert seen["path"].exists(), (
                "the lock must still be held during delete_codespace -- "
                "it must not be released between sync and the destructive action"
            )

        with patch("agent_codespaces.__main__.sync_codespace_sessions", side_effect=fake_sync), \
             patch("agent_codespaces.__main__.delete_codespace", side_effect=fake_delete):
            rc = main(["delete", "cs-lockcheck"])

        assert rc == 0
        assert not seen["path"].exists(), "the lock must be released once the command completes"

    def test_delete_balks_when_lock_is_busy(self, capsys):
        import os

        import ssh_manager

        # TargetLock is re-entrant for the SAME pid, so a same-process
        # acquire() would not exercise the busy path at all -- write the
        # lock file directly with a genuinely different, live pid (this
        # process's own parent), exactly as a real second process would.
        holder_lock = ssh_manager.TargetLock("cs-busycheck", op="codespace-lifecycle")
        holder_lock.path.parent.mkdir(parents=True, exist_ok=True)
        holder_lock.path.write_text(
            f'{{"pid": {os.getppid()}, "op": "codespace-lifecycle", '
            f'"target": "cs-busycheck", "started_at": 0.0, "host": ""}}',
            encoding="utf-8",
        )
        try:
            with patch("agent_codespaces.__main__.sync_codespace_sessions") as sync, \
                 patch("agent_codespaces.__main__.delete_codespace") as delete:
                rc = main(["delete", "cs-busycheck"])
        finally:
            holder_lock.path.unlink(missing_ok=True)

        assert rc == 1
        sync.assert_not_called()
        delete.assert_not_called()
        assert "BUSY" in capsys.readouterr().err


class TestFinalize:
    def test_finalize_sync_only(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": True, "session_count": 5, "detail": "-> hub"}) as sync, \
             patch("agent_codespaces.__main__.stop_codespace",
                   return_value=True) as stop, \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["finalize", "cs-1"])
        assert rc == 0
        sync.assert_called_once()
        stop.assert_called_once_with("cs-1")
        delete.assert_not_called()
        assert "Recovered 5 session(s)" in capsys.readouterr().out

    def test_finalize_delete_after_success(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": True, "session_count": 1, "detail": "ok"}), \
             patch("agent_codespaces.__main__._recycle_safety_block",
                   return_value=None), \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc = main(["finalize", "cs-1", "--delete"])
        assert rc == 0
        delete.assert_called_once_with("cs-1", force=False)

    def test_finalize_refuses_delete_on_failed_sync(self, capsys):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": False, "detail": "could not connect"}), \
             patch("agent_codespaces.__main__._recycle_safety_block",
                   return_value=None), \
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


class TestFinalizeProgress:
    """The D4 progress-streaming mode (``finalize --picker-progress``) backing
    the Worktree Picker CodeSpaces **Recycle** verb: stdout is the NDJSON
    progress envelope, and the recover-first safety contract is preserved."""

    def _run(self, argv: list[str]):
        """Run ``main(argv)`` capturing the raw ``sys.__stdout__`` envelope
        (the progress path writes to the real stdout fd, which capsys does not
        intercept) and return ``(rc, frames)``."""
        import io
        import sys

        buf = io.StringIO()
        with patch.object(sys, "__stdout__", buf):
            rc = main(argv)
        frames = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
        return rc, frames

    def test_recycle_streams_envelope_and_deletes(self):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": True, "session_count": 2, "detail": "ok"}), \
             patch("agent_codespaces.__main__._recycle_safety_block",
                   return_value=None), \
             patch("agent_codespaces.__main__.delete_codespace") as delete, \
             patch("agent_codespaces.__main__._release_lease_quietly"), \
             patch("agent_codespaces.__main__._clear_status_quietly"):
            rc, frames = self._run(["finalize", "cs-1", "--delete",
                                    "--picker-progress"])
        assert rc == 0
        delete.assert_called_once_with("cs-1", force=False)
        # begins with progress, ends with a single terminal `done`.
        assert frames[0]["type"] == "progress"
        assert frames[-1] == {"type": "done",
                              "message": "Recycled cs-1 (recovered + deleted)"}
        assert not any(f["type"] == "error" for f in frames)

    def test_recycle_aborts_on_failed_recovery_without_force(self):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": False, "detail": "ssh timeout"}), \
             patch("agent_codespaces.__main__._recycle_safety_block",
                   return_value=None), \
             patch("agent_codespaces.__main__.delete_codespace") as delete:
            rc, frames = self._run(["finalize", "cs-1", "--delete",
                                    "--picker-progress"])
        assert rc == 1
        delete.assert_not_called()  # recycle-rescues-first: never destroy unrecovered
        assert frames[-1]["type"] == "error"
        assert "Not deleting" in frames[-1]["message"]

    def test_recycle_force_deletes_despite_failed_recovery(self):
        with patch("agent_codespaces.__main__.sync_codespace_sessions",
                   return_value={"ok": False, "detail": "unbootable"}), \
             patch("agent_codespaces.__main__.delete_codespace") as delete, \
             patch("agent_codespaces.__main__._release_lease_quietly"), \
             patch("agent_codespaces.__main__._clear_status_quietly"):
            rc, frames = self._run(["finalize", "cs-1", "--delete", "--force",
                                    "--picker-progress"])
        assert rc == 1  # recovery failed, but forced retirement still deletes
        delete.assert_called_once_with("cs-1", force=True)
        assert frames[-1]["type"] == "done"


class TestPoolScopeBanner:
    """`pool --picker-json` surfaces the missing-`codespace`-scope remedy as a
    structured **banner** in the summary payload (#980), so the Picker CodeSpaces
    tab renders an actionable alert instead of an opaque empty list."""

    def _empty_pool(self):
        from agent_codespaces.pool import build_pool
        return build_pool(budget_cores=64, codespaces=[], leases=[], markers={})

    def _payload(self, capsys):
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    def test_scope_missing_emits_banner(self, capsys):
        import agent_codespaces.__main__ as m
        with patch.object(m.pool_mod, "build_pool", return_value=self._empty_pool()), \
             patch("agent_codespaces.picker_auth.probe", return_value={
                 "status": "scope_blocked", "account": "example-user",
                 "message": "Authorize the codespace scope.",
             }):
            rc = main(["pool", "--picker-json"])
        assert rc == 0
        summary = self._payload(capsys)["summary"]
        assert "codespace" in summary["banner_text"]
        assert summary["banner_level"] == "warn"

    def test_scope_ok_emits_no_banner(self, capsys):
        import agent_codespaces.__main__ as m
        with patch.object(m.pool_mod, "build_pool", return_value=self._empty_pool()), \
             patch("agent_codespaces.picker_auth.probe", return_value={
                 "status": "ready", "account": "example-user", "message": "",
             }):
            rc = main(["pool", "--picker-json"])
        assert rc == 0
        assert not self._payload(capsys)["summary"].get("banner_text")


class TestCreateScopeGate:
    """`create` fails fast (#980) when the ambient gh token lacks the
    `codespace` scope, before any create work -- rather than 403/404-ing minutes
    in after provisioning starts."""

    @pytest.fixture(autouse=True)
    def selected_account(self, monkeypatch):
        monkeypatch.setattr(
            "agent_codespaces.allocation.allocation_context",
            lambda repo: ("selected-owner", {"GH_TOKEN": "selected-fixture-token"}),
        )

    def test_create_blocked_when_scope_missing(self, capsys):
        import agent_codespaces.__main__ as m
        with patch.object(m, "_ambient_codespace_scope",
                          return_value=(False, "run: gh auth refresh -s codespace")), \
             patch.object(m, "create_codespace") as create:
            rc = main(["create", "owner/repo", "--force-create"])
        assert rc == 3
        create.assert_not_called()          # gated BEFORE any create work
        assert "Refusing to create" in capsys.readouterr().err

    def test_create_proceeds_when_scope_ok(self):
        import agent_codespaces.__main__ as m
        from agent_codespaces.config import CodespacesConfig
        from agent_codespaces.pool import build_pool
        # Scope OK + an empty pool -> the planner returns CREATE, so `create`
        # proceeds. Stub create so we only prove neither gate blocks (--no-wait).
        info = type("I", (), {"name": "cs-new"})()
        empty = build_pool(budget_cores=64, codespaces=[], leases=[], markers={})
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value=CodespacesConfig()), \
             patch.object(m.pool_mod, "build_pool", return_value=empty), \
             patch.object(m, "create_codespace", return_value=info) as create:
            rc = main(["create", "owner/repo", "--no-wait"])
        assert rc == 0
        create.assert_called_once()

    @pytest.mark.parametrize("allowed", [True, False])
    def test_creation_scope_uses_selected_not_ambient_credential(self, monkeypatch, allowed):
        from types import SimpleNamespace
        from agent_codespaces import __main__ as cli
        from agent_codespaces.config import CodespacesConfig

        monkeypatch.delenv("AGENT_CODESPACES_SKIP_SCOPE_CHECK", raising=False)
        monkeypatch.setenv("GH_TOKEN", "ambient-without-scope")
        monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
        observed = []

        def run(argv, **kwargs):
            assert argv == ["gh", "auth", "status", "--active", "--hostname", "github.com"]
            observed.append(kwargs.get("env"))
            return SimpleNamespace(
                returncode=0, stdout="codespace" if allowed else "repo", stderr="",
            )

        monkeypatch.setattr("subprocess.run", run)
        with patch.object(cli, "create_codespace", return_value=SimpleNamespace(name="new-venue")) as create:
            assert main(["create", "example/project", "--no-wait", "--force-create"]) == (0 if allowed else 3)
        assert observed == [{"GH_TOKEN": "selected-fixture-token"}]
        assert create.call_count == int(allowed)

    def test_create_provisioning_shares_launch_environment(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        import agent_codespaces.__main__ as m

        config = SimpleNamespace(credentials=SimpleNamespace(relay_port=19857))
        manager = SimpleNamespace(
            ensure_connected=AsyncMock(return_value=SimpleNamespace(config=object())),
            disconnect=AsyncMock(),
        )
        relay = SimpleNamespace(stop=AsyncMock())
        start_relay = AsyncMock(return_value=relay)
        prelude = "export SYNTHETIC_RELAY_CONTEXT=launch-only; "
        environments = {}

        async def record(kind, *args, **kwargs):
            environments[kind] = kwargs

        monkeypatch.setattr(m, "_require_codespace_scope", lambda *a, **kw: None)
        monkeypatch.setattr(m, "load_merged_config", lambda: config)
        monkeypatch.setattr(m, "create_codespace", lambda *a, **kw: SimpleNamespace(
            name="synthetic-created", account="synthetic-owner",
        ))
        monkeypatch.setattr(m, "wait_for_available", lambda *a, **kw: True)
        monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
        monkeypatch.setattr(m, "CodespaceSource", lambda *a, **kw: object())
        monkeypatch.setattr(m.relay_launch, "effective_relay_port", lambda cfg: 19857)
        launch = []
        monkeypatch.setattr(m.relay_launch, "build_relay_launch_env",
                            lambda name, port: launch.append((name, port)) or (prelude, port))
        monkeypatch.setattr(m, "_start_supervised_relay", start_relay)
        monkeypatch.setattr(m, "_provision_relay_helpers", AsyncMock())
        monkeypatch.setattr(m, "_provision_dotfiles",
                            lambda *a, **kw: record("dotfiles", *a, **kw))
        monkeypatch.setattr(m, "_provision_harness",
                            lambda *a, **kw: record("harness", *a, **kw))
        monkeypatch.setattr(m, "_provision_repo_hooks",
                            lambda *a, **kw: record("hooks", *a, **kw))
        assert m._cmd_create(SimpleNamespace(
            repo="example/project", branch=None, force_create=True, no_wait=False, timeout=30,
        )) == 0
        assert launch == [("synthetic-created", 19857)]
        assert start_relay.await_args.args[2] == 19857
        assert environments == {
            "dotfiles": {"relay_env": prelude},
            "harness": {"relay_env": prelude},
            "hooks": {"include_on_create": True, "relay_env": prelude},
        }
        relay.stop.assert_awaited_once()
        manager.disconnect.assert_awaited_once()


class TestCreateReuseGuard:
    """`create` consults the reuse-before-create / budget planner (Phase 2 /
    #708) before spending an account slot: it declines to create when a suitable
    idle box exists or the pool is at budget, and `--force-create` bypasses it."""

    @pytest.fixture(autouse=True)
    def selected_account(self, monkeypatch):
        monkeypatch.setattr(
            "agent_codespaces.allocation.allocation_context",
            lambda repo: ("selected-owner", {"GH_TOKEN": "selected-fixture-token"}),
        )

    def _decision(self, **kw):
        from agent_codespaces.pool import AllocationDecision
        return AllocationDecision(**kw)

    def test_reuse_decision_declines_to_create(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.pool import ALLOC_REUSE
        d = self._decision(action=ALLOC_REUSE, codespace="web1",
                           reason="reusing running idle CodeSpace 'web1'")
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", return_value=([], None)), \
             patch.object(m.pool_mod, "plan_allocation", return_value=d), \
             patch.object(m, "create_codespace") as create:
            rc = main(["create", "owner/repo"])
        assert rc == 0
        create.assert_not_called()                    # reuse -> no new box
        assert capsys.readouterr().out.strip().endswith("web1")  # box to reuse

    def test_pressure_decision_refuses_with_exit_4(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.pool import ALLOC_PRESSURE
        d = self._decision(action=ALLOC_PRESSURE, codespace=None,
                           reason="pool full: 64/64 cores in use")
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", return_value=([], None)), \
             patch.object(m.pool_mod, "plan_allocation", return_value=d), \
             patch.object(m, "create_codespace") as create:
            rc = main(["create", "owner/repo"])
        assert rc == 4
        create.assert_not_called()
        assert "pool full" in capsys.readouterr().err

    def test_recycle_advice_cannot_spend_cores_before_recycling(self, monkeypatch):
        from types import SimpleNamespace
        from agent_codespaces import __main__ as cli
        from agent_codespaces.config import CodespacesConfig
        from agent_codespaces.lifecycle import CodespaceInfo
        from agent_codespaces.pool import build_pool
        from agent_codespaces.status import STATE_PRUNABLE

        view = build_pool(
            codespaces=[CodespaceInfo(
                "old", "old", "other/project", "main", "Available", "custom64core", "selected-owner",
            )],
            leases=[], markers={"old": STATE_PRUNABLE}, l2_leases={}, clean_records={}, live={},
        )
        monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
        monkeypatch.setattr(cli.pool_mod, "build_pool", lambda **kw: view)
        monkeypatch.setattr("agent_codespaces.driving_worktrees.resolve_current_workstream_box", lambda *a: None)
        monkeypatch.setattr(cli, "_require_codespace_scope", lambda *a, **kw: None)
        with patch.object(cli, "create_codespace", return_value=SimpleNamespace(name="over-budget")) as create:
            assert main(["create", "example/project", "--no-wait"]) == 4
        create.assert_not_called()

    def test_force_create_bypasses_the_guard(self):
        import agent_codespaces.__main__ as m
        from agent_codespaces.pool import ALLOC_REUSE
        # A REUSE decision would normally block -- but --force-create never even
        # consults the planner, so create runs.
        info = type("I", (), {"name": "cs-new"})()
        d = self._decision(action=ALLOC_REUSE, codespace="web1", reason="reuse")
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "plan_allocation", return_value=d) as plan, \
             patch.object(m, "create_codespace", return_value=info) as create:
            rc = main(["create", "owner/repo", "--no-wait", "--force-create"])
        assert rc == 0
        create.assert_called_once()
        plan.assert_not_called()                      # guard skipped entirely

    def test_planner_failure_refuses_plain_create(self, capsys):
        import agent_codespaces.__main__ as m
        info = type("I", (), {"name": "cs-new"})()
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", side_effect=RuntimeError("gh down")), \
             patch.object(m, "create_codespace", return_value=info) as create:
            rc = main(["create", "owner/repo", "--no-wait"])
        assert rc == 4
        create.assert_not_called()
        assert "gh down" in capsys.readouterr().err


    @pytest.mark.parametrize("command", ["create", "allocate"])
    def test_capacity_read_preserves_context_refusal(self, command, monkeypatch, capsys):
        from agent_codespaces import __main__ as cli
        from agent_codespaces.worktrees import ContextRefused

        monkeypatch.setattr(cli, "load_merged_config", lambda: {})
        monkeypatch.setattr(cli, "_require_codespace_scope", lambda operation, **kwargs: None)
        monkeypatch.setattr(
            cli.pool_mod, "build_pool",
            lambda **kwargs: (_ for _ in ()).throw(ContextRefused("fixture context refused")),
        )
        assert main([command, "example/project"]) == cli._COORDINATION_EXIT
        assert "context refused" in capsys.readouterr().err


class TestAllocateCommand:
    """`agent-codespaces allocate <repo>` surfaces the venue-request decision
    (Phase 2 / #708) -- advisory, JSON or human, exit 4 on pressure."""

    @pytest.fixture(autouse=True)
    def selected_account(self, monkeypatch):
        monkeypatch.setattr(
            "agent_codespaces.allocation.allocation_context",
            lambda repo: ("selected-owner", {"GH_TOKEN": "selected-fixture-token"}),
        )

    def test_allocate_json_emits_decision(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.pool import ALLOC_REUSE, AllocationDecision
        d = AllocationDecision(action=ALLOC_REUSE, codespace="web1",
                               reason="reusing 'web1'")
        with patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", return_value=([], None)), \
             patch.object(m.pool_mod, "plan_allocation", return_value=d):
            rc = main(["allocate", "owner/repo", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["action"] == "reuse"
        assert out["codespace"] == "web1"

    def test_allocate_pressure_exits_4(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.pool import ALLOC_PRESSURE, AllocationDecision
        d = AllocationDecision(action=ALLOC_PRESSURE, codespace=None,
                               reason="pool full")
        with patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", return_value=([], None)), \
             patch.object(m.pool_mod, "plan_allocation", return_value=d):
            rc = main(["allocate", "owner/repo"])
        assert rc == 4
        assert "pressure" in capsys.readouterr().out


    def test_allocate_unknown_inventory_is_actionable_json(self, monkeypatch, capsys):
        from agent_codespaces import __main__ as cli

        monkeypatch.setattr(cli, "load_merged_config", lambda: {})
        monkeypatch.setattr(
            cli.pool_mod, "build_pool",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("selected account listing failed")),
        )
        assert main(["allocate", "owner/repo", "--json"]) == 4
        decision = json.loads(capsys.readouterr().out)
        assert decision["action"] == "pressure"
        assert decision["reason"] == "selected account listing failed"


@pytest.mark.parametrize("variable", ["GH_TOKEN", "GITHUB_TOKEN"])
@pytest.mark.parametrize("bound", [None, "environment-owner"])
def test_environment_only_credentials_reach_inventory_and_creation(monkeypatch, capsys, variable, bound):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent_codespaces import __main__ as cli, allocation, coordination, gh_account, lifecycle
    from agent_codespaces.config import CodespacesConfig

    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "environment-fixture-token")
    monkeypatch.setenv("GH_HOST", "github.example.com")
    monkeypatch.setattr(gh_account, "account_for_repo", lambda repo: None)
    monkeypatch.setattr("agent_codespaces.driving_worktrees.current_workstream_account", lambda repo: bound)
    monkeypatch.setattr(
        gh_account, "active_account",
        lambda: pytest.fail("environment credential used a saved active identity"),
    )
    monkeypatch.setattr(
        "agent_codespaces.owner_inventory.qualified_environment",
        lambda account: pytest.fail("environment credential became a named saved-token lookup"),
    )
    monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
    monkeypatch.setattr(cli, "_require_codespace_scope", lambda operation, **kwargs: None)
    monkeypatch.setattr("agent_codespaces.activation_cli.launch_policy.refused_exit_code", lambda name: None)
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "validate_context", lambda: None)
    monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
    monkeypatch.setattr(lifecycle, "list_codespaces", lambda: pytest.fail("qualified consumer used merged discovery"))
    monkeypatch.setattr(cli.pool_mod, "list_leases", lambda: [])
    monkeypatch.setattr(cli.pool_mod, "list_status", lambda: [])
    monkeypatch.setattr(cli.pool_mod, "codespaces_in_use", lambda names: {})
    monkeypatch.setattr(coordination, "list_leases", lambda **kwargs: {})
    monkeypatch.setattr(coordination, "list_cleanliness", lambda: {})
    monkeypatch.setattr("agent_codespaces.driving_worktrees.resolve_current_workstream_box", lambda *args: None)
    bindings = []
    bound_accounts = {}
    def bind(name, account, repo):
        bindings.append(account)
        bound_accounts[name] = account
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bind",
        bind,
    )
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account_or_raise",
        lambda name: bound_accounts.get(name),
    )
    monkeypatch.setattr(gh_account, "token_for_account", lambda account: None)
    observed = []
    state = "Available"
    connections = []

    async def connect(name, source, forwards):
        assert source.gh_env["GH_HOST"] == "github.com"
        assert source.gh_env["GH_TOKEN"] == "environment-fixture-token"
        connections.append(name)
        return SimpleNamespace(config=object())

    manager = SimpleNamespace(ensure_connected=AsyncMock(side_effect=connect), disconnect=AsyncMock())
    relay = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr(cli.relay_launch, "effective_relay_port", lambda config: 19857)
    monkeypatch.setattr(cli.relay_launch, "build_relay_launch_env", lambda *a: ("", 19857))
    monkeypatch.setattr(cli, "_start_supervised_relay", AsyncMock(return_value=relay))
    for helper in ("_provision_relay_helpers", "_provision_dotfiles", "_provision_harness", "_provision_repo_hooks"):
        monkeypatch.setattr(cli, helper, AsyncMock())

    def run(argv, **kwargs):
        nonlocal state
        environment = kwargs["env"]
        observed.append((argv[1:3], environment.get("GH_TOKEN"), environment.get("GITHUB_TOKEN")))
        assert environment.get("GH_HOST") == "github.com"
        if argv[1:3] == ["api", "user"]:
            value = json.dumps({"login": "environment-owner"})
        elif argv[1:3] == ["codespace", "list"]:
            value = "[]"
        elif argv[1:3] == ["codespace", "create"]:
            assert argv[argv.index("--devcontainer-path") + 1] == ".devcontainer/devcontainer.json"
            value = "fixture-created"
        elif argv[1:3] == ["api", "repos/example/project/codespaces/devcontainers"]:
            value = ".devcontainer/devcontainer.json\n.devcontainer/alternate/devcontainer.json"
        elif argv[1:4] == ["api", "--method", "POST"]:
            state = "Available"
            value = "{}"
        elif argv[1:3] == ["api", "/user/codespaces/fixture-created"]:
            value = json.dumps({"state": state})
        else:
            pytest.fail(f"unexpected credential rediscovery or provider operation: {argv[:3]}")
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    monkeypatch.setattr(allocation.subprocess, "run", run)
    assert main(["allocate", "example/project", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "create"
    assert main(["create", "example/project"]) == 0
    assert main(["create", "example/project", "--no-wait", "--force-create"]) == 0
    assert connections == ["fixture-created"]
    relay.stop.assert_awaited_once()
    manager.disconnect.assert_awaited_once()
    state = "Shutdown"
    capsys.readouterr()
    assert main(["claim-status", "fixture-created"]) == 0
    assert json.loads(capsys.readouterr().out) == {"exists": True, "state": "Shutdown"}
    assert main(["start", "fixture-created"]) == 0
    assert state == "Available"
    assert bindings == ["environment-owner", "environment-owner"]
    assert observed[0][0] == ["api", "user"]
    assert all(token == "environment-fixture-token" and alternate is None for _, token, alternate in observed)


@pytest.mark.parametrize("command,extra,expected", [
    ("create", [], 4), ("allocate", [], 4), ("create", ["--force-create"], 1),
])
def test_account_resolution_failure_uses_admission_exit(monkeypatch, command, extra, expected):
    def unavailable(repo):
        raise RuntimeError("selected credentials unavailable")
    monkeypatch.setattr("agent_codespaces.allocation.allocation_context", unavailable)
    assert main([command, "example/project", *extra]) == expected


@pytest.mark.parametrize("command", ["create", "allocate"])
@pytest.mark.parametrize("inventory_count", [1, 50])
def test_unmapped_workstream_reuses_bound_account_after_active_switch(monkeypatch, capsys, command, inventory_count):
    import sys
    import time
    from types import ModuleType, SimpleNamespace

    from agent_codespaces import __main__ as cli, account_binding, coordination, gh_account, lifecycle
    from agent_codespaces.config import CodespacesConfig
    from agent_codespaces import lease, reuse

    claims = SimpleNamespace(claims_of_owner=lambda owner, kind: ["retained-venue"])
    peer = ModuleType("agent_worktrees")
    peer.claims_owner = claims
    monkeypatch.setitem(sys.modules, "agent_worktrees", peer)
    monkeypatch.setattr("agent_codespaces.lease.resolve_owner_worktree", lambda: "fixture-worktree")
    now = time.time()
    monkeypatch.setattr(reuse, "local_snapshot", lambda: {
        "retained-venue": lease.Lease(
            "retained-venue", "", 1, lease._this_host(), now, now,
            worktree="fixture-worktree",
        ),
    })
    monkeypatch.setattr(account_binding, "list_bindings", lambda **kw: [
        account_binding.AccountBinding("retained-venue", "original-owner", 0, "example/project"),
    ])
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gh_account, "account_for_repo", lambda repo: None)
    monkeypatch.setattr(gh_account, "active_account", lambda: "new-active-owner")
    monkeypatch.setattr(
        "agent_codespaces.owner_inventory.qualified_environment",
        lambda account: {"GH_TOKEN": f"fixture-{account}"},
    )
    monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
    monkeypatch.delenv("AGENT_CODESPACES_SKIP_SCOPE_CHECK", raising=False)
    monkeypatch.setattr(cli, "_gh_auth_status", lambda *a, **kw: pytest.fail("reuse checked creation scope"))
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "validate_context", lambda: None)
    monkeypatch.setattr(cli.pool_mod, "list_leases", lambda: [])
    monkeypatch.setattr(cli.pool_mod, "list_status", lambda: [])
    monkeypatch.setattr(cli.pool_mod, "codespaces_in_use", lambda names: {})
    monkeypatch.setattr(coordination, "list_leases", lambda **kwargs: {})
    monkeypatch.setattr(coordination, "list_cleanliness", lambda: {})
    selected = []

    def listing(account, *, qualified, environment):
        selected.append(account)
        assert environment == {"GH_TOKEN": f"fixture-{account}"}
        return [
            lifecycle.CodespaceInfo(
                name="retained-venue", repository="example/project",
                display_name="", branch="main",
                state="Available", machine="basicLinux32gb", account=account,
            ),
            *[
                lifecycle.CodespaceInfo(
                    name=f"stopped-{index}", display_name="", repository="other/project", branch="main",
                    state="Shutdown", machine="basicLinux32gb", account=account,
                ) for index in range(inventory_count - 1)
            ],
        ] if account == "original-owner" else []

    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    monkeypatch.setattr(cli, "create_codespace", lambda *a, **kw: pytest.fail("lost own venue"))
    args = ["--json"] if command == "allocate" else ["--no-wait"]
    assert main([command, "example/project", *args]) == 0
    output = capsys.readouterr().out
    if command == "allocate":
        assert json.loads(output)["action"] == "reuse"
    assert "retained-venue" in output
    assert selected == ["original-owner"]


def test_owned_capacity_real_cli_process_preserves_override(tmp_path):
    import subprocess
    import sys

    from agent_procutil import no_window_kwargs

    script = r'''
import contextlib
import io
import json
import os
import subprocess
import sys
import builtins
from pathlib import Path
from agent_codespaces import __main__ as cli, lifecycle, pool, coordination, allocation, lease
from agent_codespaces import account_binding, driving_worktrees, status, worktrees
from agent_codespaces.config import CodespacesConfig
from agent_codespaces.lifecycle import CodespaceInfo

native_list = coordination.list_leases
native_context = allocation.allocation_context
original_import = builtins.__import__
def without_worktrees_package(name, *args, **kwargs):
    if name == "agent_worktrees" or name.startswith("agent_worktrees."):
        raise ImportError("separate runtime")
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_worktrees_package
cli.load_merged_config = CodespacesConfig
cli._require_codespace_scope = lambda operation, **kwargs: None
cli.validate_context = lambda: None
cli._gh_binary_available = lambda: True
allocation.allocation_context = lambda repo: ("fixture-owner", {"GH_TOKEN": "fixture-token"})
pool.list_leases = lambda: []
pool.list_status = lambda: []
pool.codespaces_in_use = lambda names: {}
coordination.list_leases = lambda **kwargs: {}
coordination.list_cleanliness = lambda: {}
coordination.owner_ref = lambda: None
lease.resolve_owner_worktree = lambda: None
created = []
cli.create_codespace = lambda *args, **kwargs: (
    created.append(kwargs["account"]) or
    type("Created", (), {"name": "fixture-created"})()
)
results = []
for count, force, failed in ((29, False, False), (30, False, False), (31, False, False),
                             (30, True, False), (0, False, True)):
    def listing(account, **kwargs):
        if failed:
            raise RuntimeError("fixture inventory unavailable")
        return [CodespaceInfo(
            name=f"venue-{index}", display_name="", repository="example/project",
            branch="main", state="Shutdown", machine="premiumLinux", account=account,
        ) for index in range(count)]
    allocation.list_account_codespaces = listing
    argv = ["create", "example/project", "--no-wait"]
    if force:
        argv.append("--force-create")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        create_result = cli.main(argv)
        allocate_result = cli.main(["allocate", "example/project", "--json"])
    results.append([create_result, allocate_result])
root = Path(sys.argv[1])
from ssh_manager import locks
locks.locks_dir = lambda: root / "target-locks"
lease.LEASE_FILE = root / "leases.json"
lease._LOCK_FILE = root / "leases.lock"
lease._DEPLOY_HOLDS_FILE = root / "holds.json"
lease.ensure_runtime_dir = lambda: None
lease.resolve_owner_worktree = lambda: "current-worktree"
account_binding.BINDINGS_FILE = root / "bindings.json"
account_binding._LOCK_FILE = root / "bindings.lock"
account_binding.ensure_runtime_dir = lambda: None
worktrees.validate_context = lambda: None
status.clear_status = lambda name: None
os.environ.pop("AGENT_CODESPACES_DISABLE_CLAIM", None)
coordination.owner_ref = lambda: "machine/project/current-worktree"
coordination.list_leases = native_list
cli.load_merged_config = lambda: CodespacesConfig(default_machine_type="premiumLinux")
pool_rows = [CodespaceInfo(
    name=f"venue-{index}", display_name="", repository="example/project",
    branch="retained-feature", state="Shutdown", machine="premiumLinux", account="fixture-owner",
) for index in range(30)]
allocation.list_account_codespaces = lambda *args, **kwargs: pool_rows
record = {"resource": {"kind": "codespace", "key": "venue-0"},
          "holder": "machine/project/previous", "token": "a" * 40,
          "state": "released", "live": False}
calls = []
obligations = []
uncertain = False
def transport(args, **kwargs):
    calls.append(args)
    if args == ["--project", "project", "coordination-readiness"]:
        data = {"version": 1, "ready": True, "code": "ready",
                "capabilities": [coordination.RELEASED_ONLY_CAPABILITY]}
    elif args == ["--project", "project", "claims", "current-worktree", "--json"]:
        data = {"version": 1, "machine": "machine", "repo": "project",
                "worktree_id": "current-worktree", "outbound": obligations}
    elif args[:2] == ["lease", "list"]:
        data = [record]
    elif args[:2] == ["lease", "inspect"]:
        data = record
    elif args[:2] == ["lease", "acquire"]:
        assert "--released-only" in args and record["state"] == "released"
        record.update(state="leased", live=True, token="b" * 40,
                      holder="machine/project/current-worktree")
        if uncertain:
            return subprocess.CompletedProcess(args, 4, "", "response lost")
        data = record
    elif args[:2] == ["lease", "renew"]:
        data = record
    elif args[:2] == ["lease", "release"]:
        record.update(state="released", live=False, token="c" * 40)
        data = record
    elif args[:2] == ["claims", "add"]:
        if not obligations:
            obligations.append({"kind": "codespace", "ref": "venue-0", "state": "active"})
        data = {"ok": True}
    else:
        raise AssertionError(args)
    return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
coordination._run = transport
from agent_codespaces import live_users
live_users.codespaces_in_use = lambda names: {}
retained = root / "retained.txt"
retained.write_bytes(b"prior effort work")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    reused = cli.main(["create", "example/project", "--no-wait"])
    connected = lease.claim("venue-0", "current-worktree", holder_ref="machine/project/current-worktree")
    returned = lease.release_claim("venue-0", "current-worktree")
assert reused == 0 and connected.lease_token == "b" * 40 and returned
assert retained.read_bytes() == b"prior effort work"
assert pool_rows[0].branch == "retained-feature"
assert account_binding.bound_account("venue-0") == "fixture-owner"
assert len([args for args in calls if args[:2] == ["lease", "acquire"]]) == 1
uncertain = True
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
assert record["state"] == "leased" and lease.get_lease("venue-0") is None
allocation.allocation_context = native_context
from agent_codespaces import gh_account, owner_inventory
gh_account.account_for_repo = lambda repo: None
gh_account.active_account = lambda: "new-active-owner"
for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
    os.environ.pop(variable, None)
selected = []
owner_inventory.qualified_environment = lambda account: (
    selected.append(account) or {"GH_TOKEN": "fixture-" + account}
)
cli.load_merged_config = lambda: CodespacesConfig(
    default_machine_type="premiumLinux", max_owned_codespaces=31,
)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    retried = cli.main(["create", "example/project", "--no-wait"])
assert retried == 4 and selected == ["fixture-owner"]
assert len([args for args in calls if args[:2] == ["lease", "acquire"]]) == 2
print(json.dumps({"results": results, "created": created, "reused": reused,
                  "returned": returned, "uncertain_retry": retried}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True,
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["results"] == [[0, 0], [4, 4], [4, 4], [0, 4], [4, 4]]
    assert observed["created"] == ["fixture-owner", "fixture-owner"]
    assert observed["reused"] == 0 and observed["returned"] is True
    assert observed["uncertain_retry"] == 4


# --- Top-level --project (command-surface <repo> <slug> surface) ---


class TestProjectFlag:
    def test_project_flag_triggers_chdir_on_consuming_verb(self, monkeypatch):
        # --project applies (chdir) only for a project-consuming verb (config).
        import agent_codespaces.__main__ as cm

        monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
        seen = {}
        monkeypatch.setattr(
            cm, "_chdir_to_project",
            lambda p: seen.setdefault("project", p) or True,
        )
        monkeypatch.setattr(cm, "_cmd_config", lambda args: 0)
        rc = cm.main(["--project", "demo", "config", "show"])
        assert rc == 0
        assert seen["project"] == "demo"

    def test_explicit_project_on_name_addressed_verb_bounces(self, monkeypatch):
        # An explicit --project on a name-addressed verb (version) fails loud
        # instead of silently no-op'ing (#1080-style guard).
        import agent_codespaces.__main__ as cm

        monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
        calls = {"n": 0}
        monkeypatch.setattr(
            cm, "_chdir_to_project",
            lambda p: calls.__setitem__("n", calls["n"] + 1) or True,
        )
        with pytest.raises(SystemExit) as ei:
            cm.main(["--project", "demo", "version"])
        assert ei.value.code == 2
        assert calls["n"] == 0  # never chdir'd

    def test_routed_project_on_name_addressed_verb_is_silent(self, monkeypatch):
        # Router-injected --project on a name-addressed verb stays a silent
        # no-op (no bounce, no chdir) so `<repo> codespaces version` works.
        import agent_codespaces.__main__ as cm

        monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
        calls = {"n": 0}
        monkeypatch.setattr(
            cm, "_chdir_to_project",
            lambda p: calls.__setitem__("n", calls["n"] + 1) or True,
        )
        rc = cm.main(["--project", "demo", "version"])
        assert rc == 0
        assert calls["n"] == 0

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

    def test_provision_command_codespace_flag_reaches_dispatch_builder(self, capsys):
        with patch(
            "agent_codespaces.provision.dispatch_provision_command",
            return_value="echo relay\n( echo hooks )",
        ) as m:
            rc = main(["provision-command", "--codespace", "my-cs"])
        assert rc == 0
        assert m.call_args.args[1] == "my-cs"
        assert "echo hooks" in capsys.readouterr().out

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

    def test_namespace_list_tolerates_query_failure(self, capsys):
        """A CodeSpace-listing failure (missing `codespace` OAuth scope, no
        `gh` auth, network unreachable, etc.) must never be a hard
        requirement to resolve any agent -- CodeSpaces are optional, and a
        host that doesn't use them has no reason to carry that scope.
        An uncaught failure here propagated as a non-zero exit from this CLI,
        which the namespace-resolver consumer surfaced as 404s for unrelated
        static agents. This must report zero
        CodeSpaces (exit 0) instead of crashing."""
        import json as _json

        async def _list_specs(self):
            raise RuntimeError(
                "gh codespace list failed: HTTP 403: Must have admin rights "
                "to Repository."
            )

        with patch("agent_codespaces.resolver.CodespaceResolver.list_specs", _list_specs):
            rc = main(["namespace-list"])
        assert rc == 0
        assert _json.loads(capsys.readouterr().out) == []

    def test_namespace_list_retains_unknown_account_inventory(self, capsys):
        from agent_codespaces.gh_account import AccountInventoryError

        async def unavailable(self):
            raise AccountInventoryError("account snapshot unavailable")

        with patch("agent_codespaces.resolver.CodespaceResolver.list_specs", unavailable):
            assert main(["namespace-list"]) == 1
        output = capsys.readouterr()
        assert output.out == ""
        assert "account snapshot unavailable" in output.err

    @pytest.mark.parametrize("failure", ["startup", "timeout", "refused", "decoding"])
    def test_namespace_list_retains_explicit_peer_failure(self, tmp_path, monkeypatch, capsys, failure):
        import subprocess

        from agent_codespaces import __main__ as cli, gh_account, worktrees

        peer = tmp_path / "plugins" / "agent-worktrees"
        peer.mkdir(parents=True)
        own = {"cellRoot": str(tmp_path), "pluginRoot": str(tmp_path / "owner")}
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "validated-test-cell")
        monkeypatch.setattr(worktrees, "validate_context", lambda: own)
        monkeypatch.setattr(cli, "validate_context", lambda: own)
        monkeypatch.setattr(worktrees, "launch_prefix", lambda *args: ["same-cell-peer"])

        def run(*args, **kwargs):
            if failure == "startup":
                raise OSError("peer startup failed")
            if failure == "timeout":
                raise subprocess.TimeoutExpired("same-cell-peer", 10)
            if failure == "decoding":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid peer output")
            return subprocess.CompletedProcess([], 126, "", "peer receipt refused")

        monkeypatch.setattr(worktrees.subprocess, "run", run)

        async def list_specs(self):
            gh_account.account_inventory()
            return []

        monkeypatch.setattr("agent_codespaces.resolver.CodespaceResolver.list_specs", list_specs)
        assert main(["namespace-list"]) != 0
        output = capsys.readouterr()
        assert output.out == ""
        assert ("unavailable" if failure == "decoding" else "peer") in output.err
        assert "reporting zero" not in output.err

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

    def test_plugin_plan_forwards_explicit_controller_roots(self, capsys):
        seen = {}

        def plan(repo, config, *, repo_roots, sources):
            seen["repo"] = repo
            seen["roots"] = repo_roots
            seen["sources"] = sources
            return {"plugins": []}

        with (
            patch("agent_codespaces.plugin_preparation.plugin_plan", plan),
            patch("agent_codespaces.__main__.load_merged_config", return_value=object()),
        ):
            assert main([
                "plugin-plan", "synthetic", "--repo", "example/project",
                "--plugin-root", "controller with spaces", "--plugin-root", "secondary",
            ]) == 0
        assert seen == {
            "repo": "example/project", "roots": ["controller with spaces", "secondary"], "sources": [],
        }
        assert '"plugins": []' in capsys.readouterr().out

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

    @pytest.mark.asyncio
    async def test_namespace_ensure_ready_persists_discovered_account(self, monkeypatch):
        from agent_codespaces.resolver import CodespaceResolver

        bound = []
        monkeypatch.setattr(
            "agent_codespaces.resolver.list_codespaces",
            lambda: [
                types.SimpleNamespace(
                    name="cs-a",
                    display_name="cs-a",
                    repository="example-org/example",
                    branch="main",
                    state="Available",
                    account="alice",
                )
            ],
        )
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bind",
            lambda name, account, repo="": bound.append((name, account, repo)),
        )

        await CodespaceResolver().ensure_ready("cs-a")

        assert bound == [("cs-a", "alice", "example-org/example")]


    @pytest.mark.asyncio
    @pytest.mark.parametrize("already_bound, raises", [(None, True), ("alice", False)])
    async def test_namespace_ensure_ready_surfaces_a_lost_account_binding(
        self, monkeypatch, already_bound, raises,
    ):
        from agent_codespaces.resolver import CodespaceResolver

        monkeypatch.setattr(
            "agent_codespaces.resolver.list_codespaces",
            lambda: [types.SimpleNamespace(
                name="cs-a", display_name="cs-a", repository="example-org/example",
                branch="main", state="Available", account="alice")],
        )

        def _bind(*_a, **_k):
            raise RuntimeError("Could not acquire account binding lock")

        monkeypatch.setattr("agent_codespaces.account_binding.bind", _bind)
        monkeypatch.setattr("agent_codespaces.account_binding.bound_account",
                            lambda name: already_bound)
        if raises:
            with pytest.raises(RuntimeError, match="alice.*couldn't be recorded"):
                await CodespaceResolver().ensure_ready("cs-a")
        else:
            await CodespaceResolver().ensure_ready("cs-a")


class TestRecycleSafetyGate:
    """`finalize --delete` is gated on the cleanliness beacon (venue-pool
    Phase 3): a box is deletable only with a live, known, clean verdict; unknown
    or dirty is refused (rc 2); --force and a down beacon store both pass."""

    def _rec(self, **kw):
        from agent_codespaces.coordination import CleanRecord
        base = dict(key="cs-1", known=True, clean=True, dirty=False, ahead=0,
                    unpushed_branches=0, at="", by="", live=True)
        base.update(kw)
        return CleanRecord(**base)

    def test_block_none_when_safe(self):
        import agent_codespaces.__main__ as m
        with patch("agent_codespaces.coordination.list_cleanliness",
                   return_value={"cs-1": self._rec(clean=True)}):
            assert m._recycle_safety_block("cs-1", force=False) is None

    def test_block_message_when_dirty(self):
        import agent_codespaces.__main__ as m
        with patch("agent_codespaces.coordination.list_cleanliness",
                   return_value={"cs-1": self._rec(clean=False, dirty=True, ahead=2)}):
            msg = m._recycle_safety_block("cs-1", force=False)
        assert msg and "un-off-boxed" in msg

    def test_block_message_when_unknown(self):
        import agent_codespaces.__main__ as m
        with patch("agent_codespaces.coordination.list_cleanliness",
                   return_value={}):
            msg = m._recycle_safety_block("cs-1", force=False)
        assert msg and "UNKNOWN" in msg and "verify" in msg

    def test_force_bypasses_gate(self):
        import agent_codespaces.__main__ as m
        # Force short-circuits before any store read.
        with patch("agent_codespaces.coordination.list_cleanliness") as lst:
            assert m._recycle_safety_block("cs-1", force=True) is None
        lst.assert_not_called()

    def test_store_unavailable_is_degrade_safe(self):
        import agent_codespaces.__main__ as m
        with patch("agent_codespaces.coordination.list_cleanliness",
                   return_value=None):
            assert m._recycle_safety_block("cs-1", force=False) is None

    def test_finalize_delete_blocked_when_unknown(self, capsys):
        import agent_codespaces.__main__ as m
        with patch("agent_codespaces.coordination.list_cleanliness",
                   return_value={}), \
             patch.object(m, "sync_codespace_sessions") as sync, \
             patch.object(m, "delete_codespace") as delete:
            rc = main(["finalize", "cs-1", "--delete"])
        assert rc == 2
        delete.assert_not_called()
        sync.assert_not_called()            # gated BEFORE any recover work
        assert "blocked" in capsys.readouterr().err.lower()

    def test_finalize_delete_allowed_when_safe(self):
        import agent_codespaces.__main__ as m
        with patch("agent_codespaces.coordination.list_cleanliness",
                   return_value={"cs-1": self._rec(clean=True)}), \
             patch.object(m, "sync_codespace_sessions",
                          return_value={"ok": True, "session_count": 0, "detail": "ok"}), \
             patch.object(m, "delete_codespace") as delete, \
             patch.object(m, "_release_lease_quietly"), \
             patch.object(m, "_clear_status_quietly"):
            rc = main(["finalize", "cs-1", "--delete"])
        assert rc == 0
        delete.assert_called_once_with("cs-1", force=False)


class TestVerifyCommand:
    """`agent-codespaces verify <name>` probes git-cleanliness over SSH and
    publishes the verdict to the beacon (backs the picker Verify action)."""

    def _wire(self, m, gc, published=True):
        from agent_codespaces import cleanliness

        class _FakeMgr:
            async def ensure_connected(self, *a, **k):
                return object()
            async def disconnect(self, *a, **k):
                return None

        async def _probe(manager, name, **kw):
            return gc

        return (
            patch("ssh_manager.ConnectionManager", _FakeMgr),
            patch.object(cleanliness, "probe_cleanliness", _probe),
            patch("agent_codespaces.coordination.publish_cleanliness",
                  return_value=published),
            patch("agent_codespaces.lifecycle.account_for_codespace",
                  return_value=""),
        )

    def test_verify_safe_publishes_and_exits_0(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.cleanliness import GitCleanliness
        gc = GitCleanliness(known=True, dirty=False, ahead=0, unpushed_branches=0)
        p1, p2, p3, p4 = self._wire(m, gc)
        with p1, p2, p3 as pub, p4:
            rc = main(["verify", "cs-1", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["verdict"] == "safe" and out["off_box_safe"] is True
        pub.assert_called_once()

    def test_verify_dirty_exits_3(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.cleanliness import GitCleanliness
        gc = GitCleanliness(known=True, dirty=True, ahead=1, unpushed_branches=0)
        p1, p2, p3, p4 = self._wire(m, gc)
        with p1, p2, p3, p4:
            rc = main(["verify", "cs-1", "--json"])
        assert rc == 3
        out = json.loads(capsys.readouterr().out.strip())
        assert out["verdict"] == "unsafe" and out["off_box_safe"] is False

    def test_verify_unknown_exits_3(self, capsys):
        import agent_codespaces.__main__ as m
        from agent_codespaces.cleanliness import GitCleanliness
        gc = GitCleanliness(known=False)
        p1, p2, p3, p4 = self._wire(m, gc)
        with p1, p2, p3, p4:
            rc = main(["verify", "cs-1", "--json"])
        assert rc == 3
        out = json.loads(capsys.readouterr().out.strip())
        assert out["verdict"] == "unknown" and out["off_box_safe"] is None
