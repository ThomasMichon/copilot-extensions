"""Tests for ``local_cache_refresh`` and its wiring into the worktree
lifecycle boundaries worktree-scoped-dynamic-guidance depends on: create,
resume, and ``sessionStart`` (see ``test_hook_ipc.py`` for the sessionStart
coverage). See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/active/ambient-guidance-navigability`` Phase 7.

This module invokes customizing-copilot's own declared, versioned
``render-local-cache`` CLI (``manage-instruction-projections.py``) across a
process boundary -- never importing that plugin's Python package -- per
``docs/patterns/a-la-carte-independence.md``'s "no cross-plugin
reach-around" rule.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import local_cache_refresh as lcr

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TestCandidatePluginRoots:
    def test_marketplace_layout_is_the_first_candidate(self, tmp_path: Path) -> None:
        roots = lcr._candidate_plugin_roots(tmp_path)
        assert roots[0] == (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot"
        )

    def test_direct_install_layout_is_found_by_manifest_name(
        self, tmp_path: Path
    ) -> None:
        direct_root = tmp_path / ".copilot" / "installed-plugins" / "_direct"
        plugin_dir = direct_root / "some-hash"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "customizing-copilot", "version": "1.0.0"}),
            encoding="utf-8",
        )
        # An unrelated direct install must never match.
        other_dir = direct_root / "unrelated"
        other_dir.mkdir()
        (other_dir / "plugin.json").write_text(
            json.dumps({"name": "some-other-plugin", "version": "1.0.0"}),
            encoding="utf-8",
        )

        roots = lcr._candidate_plugin_roots(tmp_path)

        assert plugin_dir in roots
        assert other_dir not in roots

    def test_direct_install_with_malformed_manifest_is_skipped(
        self, tmp_path: Path
    ) -> None:
        direct_root = tmp_path / ".copilot" / "installed-plugins" / "_direct"
        plugin_dir = direct_root / "broken"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text("{not json", encoding="utf-8")

        # Must not raise.
        roots = lcr._candidate_plugin_roots(tmp_path)
        assert plugin_dir not in roots


class TestResolveCliScript:
    def test_returns_none_when_not_installed(self, tmp_path: Path) -> None:
        assert lcr._resolve_cli_script(tmp_path) is None

    def test_finds_the_marketplace_installed_cli(self, tmp_path: Path) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")

        assert lcr._resolve_cli_script(tmp_path) == script


class TestResolveOwnAgentWorktreesCommand:
    def test_returns_none_when_no_payload_command_deployed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agent_worktrees import installer

        monkeypatch.setattr(installer, "_payload_root", lambda: tmp_path / "payload")
        assert lcr._resolve_own_agent_worktrees_command() is None

    def test_returns_the_payload_pinned_command_when_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agent_worktrees import installer

        payload = tmp_path / "payload"
        payload_bin = payload / "bin" / "payload"
        payload_bin.mkdir(parents=True)
        name = "agent-worktrees.cmd" if os.name == "nt" else "agent-worktrees"
        (payload_bin / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(installer, "_payload_root", lambda: payload)

        assert lcr._resolve_own_agent_worktrees_command() == str(payload_bin / name)

    def test_never_raises_when_payload_root_itself_fails(self, monkeypatch) -> None:
        from agent_worktrees import installer

        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(installer, "_payload_root", _boom)
        assert lcr._resolve_own_agent_worktrees_command() is None

    def test_resolves_against_a_real_deployed_plugin_layout(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """End-to-end against the real layout ``installer.deploy_binstubs``
        itself writes a project binstub to exec into -- not just a
        monkeypatched ``_payload_root`` stand-in."""
        from agent_worktrees import installer

        plugin_root = tmp_path / "plugins" / "agent-worktrees"
        (plugin_root / "src" / "agent_worktrees").mkdir(parents=True)
        (plugin_root / "plugin.json").write_text(
            '{"name": "agent-worktrees", "version": "1.0.0"}', encoding="utf-8"
        )
        payload_bin = plugin_root / "bin" / "payload"
        payload_bin.mkdir(parents=True)
        name = "agent-worktrees.cmd" if os.name == "nt" else "agent-worktrees"
        (payload_bin / name).write_text("", encoding="utf-8")
        monkeypatch.delenv("AGENT_WORKTREES_PAYLOAD_ROOT", raising=False)
        original_payload_root = installer._payload_root
        monkeypatch.setattr(
            installer, "_payload_root", lambda: original_payload_root(tmp_path)
        )

        resolved = lcr._resolve_own_agent_worktrees_command()

        assert resolved == str(payload_bin / name)


class TestRefreshLocalCache:
    def test_never_raises_when_not_installed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_never_raises_on_a_subprocess_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "manage-instruction-projections.py").write_text(
            "", encoding="utf-8"
        )

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(lcr.subprocess, "run", _boom)
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_never_raises_on_a_subprocess_timeout(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "manage-instruction-projections.py").write_text(
            "", encoding="utf-8"
        )

        import subprocess as subprocess_mod

        def _timeout(*a, **k):
            raise subprocess_mod.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(lcr.subprocess, "run", _timeout)
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_invokes_the_cli_with_the_expected_argv(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            lcr, "_resolve_own_agent_worktrees_command", lambda: "/bin/agent-worktrees"
        )

        calls = []

        def _fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(lcr.subprocess, "run", _fake_run)

        repo = tmp_path / "repo"
        repo.mkdir()
        lcr.refresh_local_cache(repo, home=tmp_path, timeout=12.0)

        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[0] == sys.executable
        assert argv[1] == str(script)
        assert argv[2:6] == [
            "render-local-cache", str(repo), "--json", "--installed-root",
        ]
        assert argv[6] == str(tmp_path / ".copilot" / "installed-plugins")
        assert argv[7:] == ["--agent-worktrees-path", "/bin/agent-worktrees"]
        assert kwargs["timeout"] == 12.0
        assert kwargs["check"] is False

    def test_omits_agent_worktrees_path_when_unresolved(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "manage-instruction-projections.py").write_text(
            "", encoding="utf-8"
        )
        monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)

        calls = []

        def _fake_run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(lcr.subprocess, "run", _fake_run)

        repo = tmp_path / "repo"
        repo.mkdir()
        lcr.refresh_local_cache(repo, home=tmp_path)

        assert "--agent-worktrees-path" not in calls[0]

    def test_real_cli_round_trip(self, tmp_path: Path) -> None:
        """End-to-end against the real, shipped ``manage-instruction-
        projections.py`` CLI -- not a stub -- proving the argv shape this
        module builds actually runs against an empty, source-free repo."""
        import subprocess

        import pytest

        real_cli = (
            _REPO_ROOT
            / "plugins"
            / "customizing-copilot"
            / "skills"
            / "reviewing-customizations"
            / "scripts"
            / "manage-instruction-projections.py"
        )
        if not real_cli.is_file():
            pytest.skip("customizing-copilot sibling plugin not checked out here")

        repo = tmp_path / "repo"
        repo.mkdir()
        result = subprocess.run(
            [sys.executable, str(real_cli), "render-local-cache", str(repo), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["operation"] == "render-local-cache"


class TestSessionstartDiagnostic:
    """``sessionstart_diagnostic`` is the backup refresh ``__main__.py``'s
    ``_run_session_lifecycle`` calls at the end of ``sessionStart``; see
    ``test_hook_ipc.py`` for coverage of its wiring into that lifecycle."""

    def test_caps_timeout_at_the_sessionstart_max(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Plenty of deadline budget remaining still bounds the timeout at
        ``SESSIONSTART_MAX_TIMEOUT_S`` -- never the refresh's own
        unbounded worst case."""
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "refresh_local_cache",
            lambda repo_root, **k: calls.append(k.get("timeout")),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 200.0)

        assert calls == [lcr.SESSIONSTART_MAX_TIMEOUT_S]

    def test_shrinks_timeout_to_remaining_budget(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Less deadline budget than the max timeout shrinks the bound to
        what actually remains, rather than risking the shared lifecycle
        deadline."""
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "refresh_local_cache",
            lambda repo_root, **k: calls.append(k.get("timeout")),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 3.5)

        assert len(calls) == 1
        assert 2.0 <= calls[0] < lcr.SESSIONSTART_MAX_TIMEOUT_S

    def test_skips_when_budget_is_too_tight(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Too little deadline budget remaining skips the refresh
        entirely -- attempting it would only risk the shared lifecycle
        deadline for a call that could never complete in time anyway."""
        import time

        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 1.0)

        assert calls == []

    def test_uses_the_max_timeout_with_no_deadline(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        calls = []
        monkeypatch.setattr(
            lcr,
            "refresh_local_cache",
            lambda repo_root, **k: calls.append(k.get("timeout")),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=None)

        assert calls == [lcr.SESSIONSTART_MAX_TIMEOUT_S]


class TestCreateWiring:
    def _harness_config(self, tmp_path: Path):
        from agent_worktrees import config as cfg_mod

        anchor = tmp_path / "anchor"
        anchor.mkdir()
        return cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="demo-repo",
            repos={
                "demo-repo": cfg_mod.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "worktrees"),
                )
            },
        )

    def test_create_refreshes_the_local_cache_for_the_new_worktree(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
        monkeypatch.setattr(m.permissions, "clone_permissions", lambda *a: False)
        monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *a: False)
        monkeypatch.setattr(
            m.permissions, "ensure_extension_permission_approvals", lambda *a: False
        )
        monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(m.git_ops, "create_worktree", lambda *a, **k: None)
        from agent_worktrees.codename_config import CodenameConfig
        monkeypatch.setattr(
            m.cfg, "load_config",
            lambda project=None: SimpleNamespace(
                default_repo=SimpleNamespace(
                    codename=CodenameConfig(),
                    pr=SimpleNamespace(
                        enabled=False, source_attribution_configured=False,
                    ),
                )
            ),
        )

        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        result = m._create_worktree_core(
            self._harness_config(tmp_path), kind="system", no_pair=True,
        )

        assert calls == [result["worktree"]["path"]]


class TestResumeWiring:
    def _config(self, tmp_path: Path):
        from agent_worktrees import config as cfg_mod

        anchor = tmp_path / "anchor"
        anchor.mkdir()
        return cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="windows",
            repo_name="demo-repo",
            auto_fast_forward=False,
            repos={
                "demo-repo": cfg_mod.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "worktrees"),
                )
            },
        )

    def _patched_resume(self, tmp_path: Path, monkeypatch, *, dry_run: bool):
        import argparse

        config = self._config(tmp_path)
        worktree_path = tmp_path / "worktrees" / "wt-1"
        worktree_path.mkdir(parents=True)
        record = SimpleNamespace(
            worktree_id="wt-1",
            worktree_path=str(worktree_path),
            branch="worktree/wt-1",
            sessions=[],
            yaml_path=tmp_path / "tracking" / "wt-1.yaml",
            resume_count=0,
            last_resumed_at=None,
        )

        class _NullLock:
            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(m.tracking, "_RecordLock", lambda *_a, **_k: _NullLock())
        monkeypatch.setattr(m.tracking, "load_record", lambda *_a, **_k: record)
        monkeypatch.setattr(m.tracking, "mark_resumed", lambda *_a, **_k: None)
        monkeypatch.setattr(m.tracking, "save_record", lambda *_a, **_k: None)
        monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
        monkeypatch.setattr(
            m, "_preflight_launch", lambda *_a, **_k: SimpleNamespace(error=None)
        )
        monkeypatch.setattr(
            m,
            "_launch_profile_selection",
            lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
        )
        monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
        monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
        monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
        monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
        monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _s: env)
        monkeypatch.setattr(m, "_emit_parent_context_hint", lambda *_a, **_k: None)
        monkeypatch.setattr(m, "_emit_plan", lambda plan: None)

        args = argparse.Namespace(
            worktree_id="wt-1", dry_run=dry_run, no_mux=False, no_resume=True,
            restore=False, json=True, bare_resume=False, no_fast_forward=True,
        )
        return record, config, args, worktree_path

    def test_resume_refreshes_the_local_cache(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        record, config, args, worktree_path = self._patched_resume(
            tmp_path, monkeypatch, dry_run=False
        )
        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        m._resolve_resume(record, config, args)

        assert calls == [str(worktree_path)]

    def test_dry_run_resume_never_refreshes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        record, config, args, _worktree_path = self._patched_resume(
            tmp_path, monkeypatch, dry_run=True
        )
        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        m._resolve_resume(record, config, args)

        assert calls == []
