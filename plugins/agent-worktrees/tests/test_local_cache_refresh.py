"""Tests for ``local_cache_refresh`` and its wiring into the worktree
lifecycle boundaries worktree-scoped-dynamic-guidance depends on: create,
resume, and ``sessionStart`` (see ``test_hook_ipc.py`` for the sessionStart
coverage). See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/active/ambient-guidance-navigability`` Phase 7.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import local_cache_refresh as lcr


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


class TestLoadInstructionProjections:
    def test_returns_none_when_not_installed(self, tmp_path: Path) -> None:
        assert lcr._load_instruction_projections(tmp_path) is None

    def test_loads_the_marketplace_install(self, tmp_path: Path) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "instruction_projections.py").write_text(
            "SENTINEL = 'loaded'\n", encoding="utf-8"
        )

        try:
            module = lcr._load_instruction_projections(tmp_path)
            assert module is not None
            assert module.SENTINEL == "loaded"
        finally:
            import sys

            sys.modules.pop(lcr._MODULE_NAME, None)

    def test_loads_a_module_whose_classes_need_sys_modules_during_exec(
        self, tmp_path: Path
    ) -> None:
        """Regression test: the real shipped ``instruction_projections.py``
        declares postponed-annotation dataclasses, whose decorator looks up
        ``sys.modules[cls.__module__]`` while the class body executes. The
        module must be registered in ``sys.modules`` *before*
        ``exec_module`` runs -- registering it only after (as this loader
        originally did) makes that lookup fail, the ``AttributeError`` is
        swallowed, and the module never loads at all."""
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "instruction_projections.py").write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class Spec:\n"
            "    name: str\n",
            encoding="utf-8",
        )

        try:
            module = lcr._load_instruction_projections(tmp_path)
            assert module is not None
            assert module.Spec(name="x").name == "x"
        finally:
            import sys

            sys.modules.pop(lcr._MODULE_NAME, None)

    def test_a_failed_load_leaves_no_partial_module_cached(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        scripts_dir = (
            tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "customizing-copilot" / "skills" / "reviewing-customizations"
            / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "instruction_projections.py").write_text(
            "raise RuntimeError('boom')\n", encoding="utf-8"
        )

        import sys

        assert lcr._load_instruction_projections(tmp_path) is None
        assert lcr._MODULE_NAME not in sys.modules


class TestRefreshLocalCache:
    def test_never_raises_when_not_installed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_never_raises_on_an_internal_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def _boom(home):
            raise RuntimeError("boom")

        monkeypatch.setattr(lcr, "_load_instruction_projections", _boom)
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_calls_render_local_cache_with_discovered_sources(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        calls = []
        discover_calls = []

        def _render_local_cache(root, discover_sources):
            calls.append(root)
            discover_calls.append(list(discover_sources()))

        def _discover_enabled_sources(root, *, require_trust):
            assert require_trust is False
            return ["a-source"]

        fake_module = SimpleNamespace(
            render_local_cache=_render_local_cache,
            discover_enabled_sources=_discover_enabled_sources,
        )
        monkeypatch.setattr(
            lcr, "_load_instruction_projections", lambda home: fake_module
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        lcr.refresh_local_cache(repo, home=tmp_path)

        assert calls == [repo]
        assert discover_calls == [["a-source"]]


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

    def test_resume_refreshes_the_local_cache(
        self, tmp_path: Path, monkeypatch
    ) -> None:
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

        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        args = argparse.Namespace(
            worktree_id="wt-1", dry_run=False, no_mux=False, no_resume=True,
            restore=False, json=True, bare_resume=False, no_fast_forward=True,
        )
        m._resolve_resume(record, config, args)

        assert calls == [str(worktree_path)]

    def test_dry_run_resume_never_refreshes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
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

        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        args = argparse.Namespace(
            worktree_id="wt-1", dry_run=True, no_mux=False, no_resume=True,
            restore=False, json=True, bare_resume=False, no_fast_forward=True,
        )
        m._resolve_resume(record, config, args)

        assert calls == []
