"""Tests for CLI entry point."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


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
        canonical = repo / ".agent-codespaces" / "config.yaml"
        assert canonical.exists()
        assert "machine_type: big" in canonical.read_text()
        assert not (repo / "codespaces.yaml").exists()
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
             patch.object(m, "_gh_auth_preflight",
                          return_value=["gh token is missing the 'codespace' "
                                        "scope -- run: gh auth refresh -h "
                                        "github.com -s codespace"]):
            rc = main(["pool", "--picker-json"])
        assert rc == 0
        summary = self._payload(capsys)["summary"]
        assert "codespace" in summary["banner_text"]
        assert summary["banner_level"] == "warn"

    def test_scope_ok_emits_no_banner(self, capsys):
        import agent_codespaces.__main__ as m
        with patch.object(m.pool_mod, "build_pool", return_value=self._empty_pool()), \
             patch.object(m, "_gh_auth_preflight", return_value=[]):
            rc = main(["pool", "--picker-json"])
        assert rc == 0
        assert "banner_text" not in self._payload(capsys)["summary"]


class TestCreateScopeGate:
    """`create` fails fast (#980) when the ambient gh token lacks the
    `codespace` scope, before any create work -- rather than 403/404-ing minutes
    in after provisioning starts."""

    def test_create_blocked_when_scope_missing(self, capsys):
        import agent_codespaces.__main__ as m
        with patch.object(m, "_ambient_codespace_scope",
                          return_value=(False, "run: gh auth refresh -s codespace")), \
             patch.object(m, "create_codespace") as create:
            rc = main(["create", "owner/repo"])
        assert rc == 3
        create.assert_not_called()          # gated BEFORE any create work
        assert "Refusing to create" in capsys.readouterr().err

    def test_create_proceeds_when_scope_ok(self):
        import agent_codespaces.__main__ as m
        from agent_codespaces.pool import build_pool
        # Scope OK + an empty pool -> the planner returns CREATE, so `create`
        # proceeds. Stub create so we only prove neither gate blocks (--no-wait).
        info = type("I", (), {"name": "cs-new"})()
        empty = build_pool(budget_cores=64, codespaces=[], leases=[], markers={})
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", return_value=empty), \
             patch.object(m, "create_codespace", return_value=info) as create:
            rc = main(["create", "owner/repo", "--no-wait"])
        assert rc == 0
        create.assert_called_once()


class TestCreateReuseGuard:
    """`create` consults the reuse-before-create / budget planner (Phase 2 /
    #708) before spending an account slot: it declines to create when a suitable
    idle box exists or the pool is at budget, and `--force-create` bypasses it."""

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

    def test_planner_failure_degrades_to_plain_create(self):
        import agent_codespaces.__main__ as m
        # Any planner error must not wedge a legit create -- it proceeds.
        info = type("I", (), {"name": "cs-new"})()
        with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")), \
             patch.object(m, "load_merged_config", return_value={}), \
             patch.object(m.pool_mod, "build_pool", side_effect=RuntimeError("gh down")), \
             patch.object(m, "create_codespace", return_value=info) as create:
            rc = main(["create", "owner/repo", "--no-wait"])
        assert rc == 0
        create.assert_called_once()


class TestAllocateCommand:
    """`agent-codespaces allocate <repo>` surfaces the venue-request decision
    (Phase 2 / #708) -- advisory, JSON or human, exit 4 on pressure."""

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
