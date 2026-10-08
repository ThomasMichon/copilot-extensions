"""Tests for ``local_cache_refresh`` and its wiring into the real `start_
session` path: ``session_host_connection.py``'s ``_connect_via_session_
host`` method, right after ``resolve_local_launch`` resolves the
authoritative local ``work_dir`` and before ``spawner.spawn()`` actually
launches the Copilot CLI process.

See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/2026/10/03 local-cache-delivery-primacy`` Phase 2. This module invokes
customizing-copilot's own declared, versioned ``render-local-cache`` CLI
(``manage-instruction-projections.py``) across a process boundary -- never
importing that plugin's Python package -- per
``docs/patterns/a-la-carte-independence.md``'s "no cross-plugin reach-around"
rule.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge import local_cache_refresh as lcr

# `_process_start_time` (below) has a Windows and a Linux (`/proc`) backend
# only -- macOS has neither, so it returns `None` there, which would make
# these tests' own `finally`-block safety net (an identity-checked force-
# kill) silently skip cleanup if the tree-kill under test ever regressed,
# leaking a real 60-second-sleeping process into the suite. Gate to the
# two platforms this facility's own CI actually runs (Windows, Linux)
# rather than claim unverified macOS coverage.
_descendant_safety_net_supported = sys.platform == "win32" or sys.platform.startswith(
    "linux"
)
_requires_descendant_safety_net = pytest.mark.skipif(
    not _descendant_safety_net_supported,
    reason=(
        "_process_start_time has no macOS backend; skip rather than risk "
        "leaking the test's own grandchild process if cleanup regresses"
    ),
)


class TestSelectGlobalRoot:
    """Direct, in-process tests of the pure resolution/filtering logic
    ``_resolve_cli_script``'s subprocess entry point calls."""

    def test_returns_none_when_not_installed(self, tmp_path: Path) -> None:
        assert lcr._select_global_root(tmp_path) is None

    def _fake_plugin(self, name: str, *, global_root: Path | None = None) -> SimpleNamespace:
        def _root_for_scope(scope: str) -> Path | None:
            return global_root if scope == "global" and global_root is not None else None

        return SimpleNamespace(name=name, root_for_scope=_root_for_scope)

    def test_finds_the_global_scope_root(self, tmp_path: Path, monkeypatch) -> None:
        plugin_root = tmp_path / "customizing-copilot"
        fake_plugin = self._fake_plugin("customizing-copilot", global_root=plugin_root)
        fake_report = SimpleNamespace(active={"customizing-copilot@local": fake_plugin})
        monkeypatch.setattr(
            "plugin_activation.resolve_active_plugins", lambda **k: fake_report
        )

        assert lcr._select_global_root(tmp_path) == plugin_root

    def test_ignores_an_active_plugin_with_a_different_name(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_plugin = self._fake_plugin("some-other-plugin", global_root=tmp_path)
        fake_report = SimpleNamespace(active={"some-other-plugin@local": fake_plugin})
        monkeypatch.setattr(
            "plugin_activation.resolve_active_plugins", lambda **k: fake_report
        )

        assert lcr._select_global_root(tmp_path) is None

    def test_ignores_a_project_scoped_override_never_trusting_non_global_roots(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A matching-named plugin whose live roots are ONLY project-scoped
        must never be trusted -- a session in an unrelated repo must never
        execute that project's own local content."""
        fake_plugin = SimpleNamespace(
            name="customizing-copilot", root_for_scope=lambda scope: None,
        )
        fake_report = SimpleNamespace(active={"customizing-copilot@local": fake_plugin})
        monkeypatch.setattr(
            "plugin_activation.resolve_active_plugins", lambda **k: fake_report
        )

        assert lcr._select_global_root(tmp_path) is None

    def test_fails_closed_when_multiple_active_plugins_share_the_name(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        fake_report = SimpleNamespace(
            active={
                "customizing-copilot@marketplace-a": self._fake_plugin(
                    "customizing-copilot", global_root=root_a
                ),
                "customizing-copilot@marketplace-b": self._fake_plugin(
                    "customizing-copilot", global_root=root_b
                ),
            }
        )
        monkeypatch.setattr(
            "plugin_activation.resolve_active_plugins", lambda **k: fake_report
        )

        assert lcr._select_global_root(tmp_path) is None

    def test_never_raises_when_resolution_itself_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def _boom(**k):
            raise RuntimeError("boom")

        monkeypatch.setattr("plugin_activation.resolve_active_plugins", _boom)

        assert lcr._select_global_root(tmp_path) is None


class TestResolveCliScript:
    """Subprocess-wiring tests for ``_resolve_cli_script``: it runs
    ``_select_global_root`` out-of-process (see the module docstring for
    why), so these tests mock ``_run_bounded`` rather than
    ``plugin_activation.resolve_active_plugins`` directly -- that logic has
    its own direct coverage in ``TestSelectGlobalRoot``."""

    @pytest.mark.asyncio
    async def test_finds_the_script_when_the_subprocess_reports_a_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        plugin_root = tmp_path / "customizing-copilot"
        scripts_dir = (
            plugin_root / "skills" / "reviewing-customizations" / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")

        monkeypatch.setattr(
            lcr, "_run_bounded", AsyncMock(return_value=f"{plugin_root}\n")
        )

        assert await lcr._resolve_cli_script(tmp_path, timeout=5.0) == script

    @pytest.mark.asyncio
    async def test_fails_closed_when_the_subprocess_reports_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(lcr, "_run_bounded", AsyncMock(return_value=""))

        assert await lcr._resolve_cli_script(tmp_path, timeout=5.0) is None

    @pytest.mark.asyncio
    async def test_fails_closed_when_the_reported_root_has_no_script(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A reported root that doesn't actually contain the expected
        script (a stale or mismatched report) must never fall back to
        trusting anything else -- fail closed."""
        monkeypatch.setattr(
            lcr, "_run_bounded", AsyncMock(return_value=f"{tmp_path}\n")
        )

        assert await lcr._resolve_cli_script(tmp_path, timeout=5.0) is None

    @pytest.mark.asyncio
    async def test_never_raises_when_the_subprocess_call_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        async def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(lcr, "_run_bounded", _boom)

        assert await lcr._resolve_cli_script(tmp_path, timeout=5.0) is None

    @pytest.mark.asyncio
    async def test_real_subprocess_round_trip_against_an_empty_home(
        self, tmp_path: Path
    ) -> None:
        """Real, non-mocked round trip through ``_run_bounded`` and the
        actual ``python -m agent_bridge.local_cache_refresh`` subprocess
        entry point, proving the module is genuinely importable and
        runnable that way (not just as a library) -- an empty home with
        nothing installed resolves to nothing."""
        assert await lcr._resolve_cli_script(tmp_path, timeout=30.0) is None

    @pytest.mark.asyncio
    async def test_real_cli_round_trip(self, tmp_path: Path) -> None:
        """End-to-end against the real, shipped ``manage-instruction-
        projections.py`` CLI -- not a stub -- proving the argv shape
        ``refresh_local_cache`` builds actually runs."""
        real_cli = (
            Path(__file__).resolve().parents[3]
            / "plugins"
            / "customizing-copilot"
            / "skills"
            / "reviewing-customizations"
            / "scripts"
            / "manage-instruction-projections.py"
        )
        if not real_cli.is_file():
            pytest.skip("customizing-copilot sibling plugin not checked out here")

        import json

        repo = tmp_path / "repo"
        repo.mkdir()
        result = await lcr._run_bounded(
            [sys.executable, str(real_cli), "render-local-cache", str(repo), "--json"],
            timeout=30.0,
        )
        assert result is not None
        payload = json.loads(result)
        assert payload["operation"] == "render-local-cache"


class TestRefreshLocalCache:
    @pytest.mark.asyncio
    async def test_never_raises_when_not_installed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        await lcr.refresh_local_cache(repo, home=tmp_path)

    @pytest.mark.asyncio
    async def test_real_render_local_cache_call_genuinely_refreshes_the_sibling(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Guard test (Phase 2 Plan): drives ``refresh_local_cache`` itself
        -- not a hand-reconstructed argv -- against the real, production
        ``instruction_projections.render_local_cache()`` (not a stub),
        proving the whole call chain actually refreshes a
        ``.local.instructions.md`` sibling on disk, not merely that some
        subprocess exits zero.

        ``_resolve_cli_script`` is pointed at a small stand-in CLI that
        calls the real ``render_local_cache`` directly with a hand-built
        single-source list, bypassing only ``discover_enabled_sources``'s
        own settings-file discovery (explicitly the CLI's own job per that
        function's docstring, not ``render_local_cache``'s) -- everything
        downstream of that is the real, shipped code."""
        scripts_dir = (
            Path(__file__).resolve().parents[3]
            / "plugins"
            / "customizing-copilot"
            / "skills"
            / "reviewing-customizations"
            / "scripts"
        )
        if not (scripts_dir / "instruction_projections.py").is_file():
            pytest.skip("customizing-copilot sibling plugin not checked out here")

        plugin_root = tmp_path / "test-marketplace" / "test-plugin"
        template = plugin_root / "instructions" / "fallback.instructions.md"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(
            '---\napplyTo: "**"\n---\n\n# Fallback\n\nKeep this useful.\n',
            encoding="utf-8",
        )
        (plugin_root / "plugin.json").write_text(
            '{"name": "test-plugin", "version": "1.0.0"}', encoding="utf-8"
        )
        import json as _json

        (plugin_root / "instruction-projections.json").write_text(
            _json.dumps(
                {
                    "schema": "copilot-extensions.instruction-projections",
                    "version": 1,
                    "projections": [
                        {
                            "id": "fallback",
                            "template": "instructions/fallback.instructions.md",
                            "destination": (
                                ".github/instructions/test-plugin/"
                                "fallback.instructions.md"
                            ),
                            "customizationKind": "instructions",
                            "applyTo": "**",
                            "legacyMarkers": ["test-plugin:static-fallback"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        stub_cli = tmp_path / "manage-instruction-projections.py"
        stub_cli.write_text(
            "import sys, json\n"
            "from pathlib import Path\n"
            "from types import SimpleNamespace\n"
            f"sys.path.insert(0, {str(scripts_dir)!r})\n"
            "import instruction_projections as projections\n"
            "repo_root = Path(sys.argv[2])\n"
            f"plugin_root = Path({str(plugin_root)!r})\n"
            "source = SimpleNamespace(\n"
            "    origin='test-marketplace/test-plugin',\n"
            "    payload_root=plugin_root,\n"
            "    skills_root=plugin_root / 'skills',\n"
            "    controlled=False, source='', version='',\n"
            ")\n"
            "result = projections.render_local_cache(repo_root, lambda: [source])\n"
            "print(json.dumps(result.to_dict()))\n"
            "sys.exit(1 if result.blocking else 0)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            lcr, "_resolve_cli_script", AsyncMock(return_value=stub_cli)
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        destination = (
            repo
            / ".github"
            / "instructions"
            / "test-plugin"
            / "fallback.instructions.md"
        )
        local_sibling = destination.parent / "fallback.local.instructions.md"
        assert not local_sibling.exists()

        await lcr.refresh_local_cache(repo, home=tmp_path, timeout=30.0)

        assert local_sibling.is_file(), (
            "refresh_local_cache never produced the local-cache sibling"
        )
        assert "Keep this useful." in local_sibling.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_never_raises_on_a_subprocess_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            lcr, "_resolve_cli_script", AsyncMock(return_value=script)
        )

        async def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(lcr, "_run_bounded", _boom)
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        await lcr.refresh_local_cache(repo, home=tmp_path)

    @pytest.mark.asyncio
    async def test_invokes_the_cli_with_the_expected_argv(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            lcr, "_resolve_cli_script", AsyncMock(return_value=script)
        )
        monkeypatch.setattr(
            lcr,
            "_resolve_agent_worktrees_path",
            lambda: ("/bin/agent-worktrees", False),
        )

        calls = []

        async def _fake_run_bounded(argv, **kwargs):
            calls.append((argv, kwargs))
            return "{}"

        monkeypatch.setattr(lcr, "_run_bounded", _fake_run_bounded)

        repo = tmp_path / "repo"
        repo.mkdir()
        await lcr.refresh_local_cache(repo, home=tmp_path, timeout=12.0)

        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[0] == sys.executable
        assert argv[1] == str(script)
        assert argv[2:6] == [
            "render-local-cache", str(repo), "--json", "--installed-root",
        ]
        assert argv[6] == str(tmp_path / ".copilot" / "installed-plugins")
        assert argv[7:] == ["--agent-worktrees-path", "/bin/agent-worktrees"]
        assert kwargs["timeout"] == 12.0 - lcr._RESOLUTION_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_omits_agent_worktrees_path_when_unresolved(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            lcr, "_resolve_cli_script", AsyncMock(return_value=script)
        )
        monkeypatch.setattr(
            lcr, "_resolve_agent_worktrees_path", lambda: (None, False)
        )

        calls = []

        async def _fake_run_bounded(argv, **kwargs):
            calls.append(argv)
            return "{}"

        monkeypatch.setattr(lcr, "_run_bounded", _fake_run_bounded)

        repo = tmp_path / "repo"
        repo.mkdir()
        await lcr.refresh_local_cache(repo, home=tmp_path)

        assert "--agent-worktrees-path" not in calls[0]

    @pytest.mark.asyncio
    async def test_skips_the_render_when_same_cell_evidence_cannot_be_passed_through(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A same-cell receipt resolved to a shape ``--agent-worktrees-
        path`` can't carry must never silently fall through to the
        downstream CLI's own ambient lookup -- that would discard the only
        same-cell evidence this call had and risk rendering from the
        wrong installation cell. The whole render is skipped this round
        instead."""
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            lcr, "_resolve_cli_script", AsyncMock(return_value=script)
        )
        monkeypatch.setattr(
            lcr, "_resolve_agent_worktrees_path", lambda: (None, True)
        )

        calls = []

        async def _fake_run_bounded(argv, **kwargs):
            calls.append(argv)
            return "{}"

        monkeypatch.setattr(lcr, "_run_bounded", _fake_run_bounded)

        repo = tmp_path / "repo"
        repo.mkdir()
        await lcr.refresh_local_cache(repo, home=tmp_path)

        assert calls == []

    @pytest.mark.asyncio
    async def test_resolve_agent_worktrees_path_uses_the_ambient_degradation_shape(
        self, monkeypatch
    ) -> None:
        """The common case: no ``COPILOT_EXTENSIONS_CONTEXT`` receipt, so
        ``_agent_worktrees_launch_prefix()`` degrades to a single-element
        ambient-lookup list -- the one shape compatible with ``--agent-
        worktrees-path``'s single-string contract."""
        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix",
            lambda: ["/bin/agent-worktrees"],
        )
        assert lcr._resolve_agent_worktrees_path() == ("/bin/agent-worktrees", False)

    @pytest.mark.asyncio
    async def test_resolve_agent_worktrees_path_requires_skip_when_same_cell_also_fails(
        self, monkeypatch
    ) -> None:
        """A receipt-validated multi-token launch prefix is a different
        invocation shape than a single resolved path -- never squeeze it
        into ``--agent-worktrees-path``'s single-string contract. When the
        direct same-cell resolution also can't produce a path (binstub
        genuinely not at the expected location), never silently discard
        that same-cell evidence by omitting the flag either: the caller
        must skip the render entirely this round."""
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")
        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix",
            lambda: ["python", "-I", "-X", "utf8", "/path/_peer_launch.py", "a", "b"],
        )
        monkeypatch.setattr(
            lcr, "_resolve_same_cell_agent_worktrees_path", lambda: None
        )
        assert lcr._resolve_agent_worktrees_path() == (None, True)

    @pytest.mark.asyncio
    async def test_resolve_agent_worktrees_path_prefers_direct_same_cell_resolution(
        self, monkeypatch
    ) -> None:
        """When a receipt is present and the wrapper prefix is the
        multi-token shape, the feature is still preserved for namespaced
        marketplace cells: the direct same-cell binstub path is used
        instead of skipping the render."""
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")
        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix",
            lambda: ["python", "-I", "-X", "utf8", "/path/_peer_launch.py", "a", "b"],
        )
        monkeypatch.setattr(
            lcr,
            "_resolve_same_cell_agent_worktrees_path",
            lambda: "/cell/plugins/agent-worktrees/bin/payload/agent-worktrees",
        )
        assert lcr._resolve_agent_worktrees_path() == (
            "/cell/plugins/agent-worktrees/bin/payload/agent-worktrees",
            False,
        )

    @pytest.mark.asyncio
    async def test_resolve_same_cell_agent_worktrees_path_returns_none_without_receipt(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
        assert lcr._resolve_same_cell_agent_worktrees_path() is None

    @pytest.mark.asyncio
    async def test_resolve_same_cell_agent_worktrees_path_uses_the_peer_receipts_payload_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # The payload deliberately lives under a *versioned* subdirectory,
        # not directly under cellRoot/plugins/agent-worktrees (the peer's
        # durable install root) -- proving resolution follows the peer's
        # own install.json receipt rather than assuming the durable root
        # doubles as the payload root.
        payload_root = (
            tmp_path / "cell" / "plugins" / "agent-worktrees" / "versions" / "v3"
        )
        payload_dir = payload_root / "bin" / "payload"
        payload_dir.mkdir(parents=True)
        name = "agent-worktrees.cmd" if sys.platform == "win32" else "agent-worktrees"
        expected = payload_dir / name
        expected.write_text("", encoding="utf-8")

        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")
        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_bridge_owner_root",
            lambda: tmp_path / "cell" / "plugins" / "agent-bridge",
        )
        monkeypatch.setattr(
            "agent_bridge._peer_launch.validate_owner",
            lambda *a, **k: {"cellRoot": str(tmp_path / "cell")},
        )
        monkeypatch.setattr(
            "agent_bridge._peer_launch._active_context",
            lambda *a, **k: {"payloadRoot": str(payload_root)},
        )
        assert lcr._resolve_same_cell_agent_worktrees_path() == str(expected)

    @pytest.mark.asyncio
    async def test_resolve_same_cell_agent_worktrees_path_does_not_misresolve_to_the_durable_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A candidate built directly under cellRoot/plugins/agent-worktrees
        (the peer's durable root, not its payload root) must never be
        returned just because a file happens to exist there -- confirms
        the fix actually consults the receipt's payloadRoot rather than
        falling back to the pre-fix (wrong) root."""
        durable_payload_dir = (
            tmp_path / "cell" / "plugins" / "agent-worktrees" / "bin" / "payload"
        )
        durable_payload_dir.mkdir(parents=True)
        name = "agent-worktrees.cmd" if sys.platform == "win32" else "agent-worktrees"
        (durable_payload_dir / name).write_text("", encoding="utf-8")

        real_payload_root = (
            tmp_path / "cell" / "plugins" / "agent-worktrees" / "versions" / "v3"
        )
        (real_payload_root / "bin" / "payload").mkdir(parents=True)
        (real_payload_root / "bin" / "payload" / name).write_text(
            "", encoding="utf-8"
        )

        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")
        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_bridge_owner_root",
            lambda: tmp_path / "cell" / "plugins" / "agent-bridge",
        )
        monkeypatch.setattr(
            "agent_bridge._peer_launch.validate_owner",
            lambda *a, **k: {"cellRoot": str(tmp_path / "cell")},
        )
        monkeypatch.setattr(
            "agent_bridge._peer_launch._active_context",
            lambda *a, **k: {"payloadRoot": str(real_payload_root)},
        )
        resolved = lcr._resolve_same_cell_agent_worktrees_path()
        assert resolved == str(real_payload_root / "bin" / "payload" / name)
        assert resolved != str(durable_payload_dir / name)

    @pytest.mark.asyncio
    async def test_resolve_same_cell_agent_worktrees_path_never_raises(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr("agent_bridge._peer_launch.validate_owner", _boom)
        assert lcr._resolve_same_cell_agent_worktrees_path() is None

    @pytest.mark.asyncio
    async def test_resolve_agent_worktrees_path_degrades_safely_with_no_receipt(
        self, monkeypatch
    ) -> None:
        """No receipt at all and an unresolvable prefix (e.g. ambient
        `agent-worktrees` genuinely isn't installed) is not a same-cell-
        evidence-discarding case -- there was no evidence to discard --
        so this degrades to the pre-existing "just omit the flag"
        behavior, never a forced render skip."""
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix",
            lambda: None,
        )
        assert lcr._resolve_agent_worktrees_path() == (None, False)

    @pytest.mark.asyncio
    async def test_resolve_agent_worktrees_path_never_raises(
        self, monkeypatch
    ) -> None:
        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix",
            _boom,
        )
        assert lcr._resolve_agent_worktrees_path() == (None, False)

    @pytest.mark.asyncio
    async def test_skips_the_render_when_resolution_consumes_the_whole_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A tiny overall ``timeout`` (smaller than ``_RESOLUTION_TIMEOUT_S``)
        must never go negative/zero into the render call -- it must simply
        skip rendering this round, never raise or pass a bogus timeout."""
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            lcr, "_resolve_cli_script", AsyncMock(return_value=script)
        )
        calls = []

        async def _fake_run_bounded(argv, **kwargs):
            calls.append(argv)
            return "{}"

        monkeypatch.setattr(lcr, "_run_bounded", _fake_run_bounded)

        repo = tmp_path / "repo"
        repo.mkdir()
        await lcr.refresh_local_cache(repo, home=tmp_path, timeout=0.5)

        assert calls == []

    @_requires_descendant_safety_net
    @pytest.mark.asyncio
    async def test_descendant_of_a_timed_out_render_does_not_survive(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Real-subprocess regression: a bare ``proc.kill()`` on the direct
        child alone would leak a descendant the real CLI can spawn (an
        ``agent-worktrees`` lookup, Git subprocesses -- see
        ``scan_plugin_sources.py``). This proves ``_run_bounded`` is
        actually wired to the whole-tree kill (``_kill_tree``), not a bare
        kill of the direct child: a stand-in script spawns a grandchild and
        hangs, and the grandchild must not survive the bound -- within the
        documented hard ceiling (``timeout + _CLEANUP_GRACE_S``), asserted
        directly against elapsed wall time, not merely "eventually".

        A ``finally`` block force-kills the grandchild by PID, identity-
        checked against its start-time token (the same PID-reuse-proof
        pattern ``agent_worktrees/tests/test_git_ops.py`` uses), so a
        regression in the tree-kill path under test can never itself leak
        a live process into the suite."""
        import time

        monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)

        ready = tmp_path / "ready"
        pidfile = tmp_path / "grandchild.pid"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            f"import os, time\n"
            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
            f"time.sleep(60)\n"
        )
        script = tmp_path / "hangs.py"
        script.write_text(
            f"import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, {str(grandchild_script)!r}])\n"
            f"open({str(ready)!r}, 'w').write(str(p.pid))\n"
            f"time.sleep(60)\n"
        )

        grandchild_pid: int | None = None
        grandchild_start_time: str | None = None
        try:
            # Wide enough for a loaded host's interpreter startup to spawn the
            # grandchild before the timeout tree-kill fires.
            call_timeout = 5.0
            started = time.monotonic()
            result = await lcr._run_bounded(
                [sys.executable, str(script)], timeout=call_timeout,
            )
            elapsed = time.monotonic() - started
            assert result is None
            # The documented hard ceiling, plus a small scheduling margin
            # -- this must never silently balloon into the old ~13s
            # worst case `terminate_windows_tree`'s own SSH-tuned
            # defaults would have produced.
            assert elapsed <= call_timeout + lcr._CLEANUP_GRACE_S + 2.0, (
                f"tree-kill cleanup took {elapsed:.1f}s, past the "
                f"documented timeout + _CLEANUP_GRACE_S ceiling"
            )

            deadline = time.monotonic() + 10.0
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert pidfile.exists(), "grandchild never started"
            grandchild_pid = int(pidfile.read_text().strip())
            grandchild_start_time = _process_start_time(grandchild_pid)

            deadline = time.monotonic() + 10.0
            while _pid_alive(grandchild_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not _pid_alive(grandchild_pid), (
                "grandchild survived the timed-out render -- tree-kill did "
                "not reach it"
            )
        finally:
            if (
                grandchild_pid is not None
                and grandchild_start_time is not None
                and _pid_alive(grandchild_pid)
                and _process_start_time(grandchild_pid) == grandchild_start_time
            ):
                if sys.platform == "win32":
                    import subprocess as _subprocess

                    _subprocess.run(
                        ["taskkill", "/F", "/PID", str(grandchild_pid)],
                        capture_output=True, check=False,
                    )
                else:
                    import signal

                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(grandchild_pid, signal.SIGKILL)

    @_requires_descendant_safety_net
    @pytest.mark.asyncio
    async def test_grandchild_does_not_survive_when_direct_child_exits_first(
        self, tmp_path: Path
    ) -> None:
        """A distinct, narrower failure mode than the above: the direct
        child exits quickly, but a grandchild it spawned inherited its
        stdout/stderr handles (the default when a child doesn't explicitly
        redirect them) and keeps them open, so ``proc.communicate()``
        still blocks -- and eventually times out -- waiting for EOF on
        those pipes. ``terminate_windows_tree``'s own "whole tree gone once
        the direct child's `proc.wait()` returns" heuristic is **wrong**
        here: the direct child already exited (its own `wait()` resolves
        near-instantly), so the old code never even attempted a forceful
        kill, leaving the real survivor -- the grandchild -- untouched.
        Closing the per-invocation kill-on-close Job Object (``_run_
        bounded``'s own ``finally``) is authoritative regardless of the
        direct child's own state, which this proves directly."""
        import time

        pidfile = tmp_path / "grandchild.pid"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import os, time\n"
            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        # No stdout/stderr redirection on the grandchild Popen call below --
        # it inherits the direct child's own (piped) handles, which is
        # exactly what keeps those pipes open after the direct child exits.
        script = tmp_path / "exits_fast.py"
        # The direct child waits (bounded) for the grandchild's pidfile before
        # exiting, so a loaded host's slow interpreter startup cannot let the
        # timeout tree-kill fire before the grandchild ever existed.
        script.write_text(
            "import os, subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, {str(grandchild_script)!r}])\n"
            "deadline = time.monotonic() + 4.0\n"
            f"while not os.path.exists({str(pidfile)!r}) "
            "and time.monotonic() < deadline:\n"
            "    time.sleep(0.05)\n"
        )

        grandchild_pid: int | None = None
        grandchild_start_time: str | None = None
        try:
            call_timeout = 5.0
            started = time.monotonic()
            result = await lcr._run_bounded(
                [sys.executable, str(script)], timeout=call_timeout,
            )
            elapsed = time.monotonic() - started
            assert result is None
            assert elapsed <= call_timeout + lcr._CLEANUP_GRACE_S + 2.0, (
                f"tree-kill cleanup took {elapsed:.1f}s, past the "
                f"documented timeout + _CLEANUP_GRACE_S ceiling"
            )

            deadline = time.monotonic() + 10.0
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert pidfile.exists(), "grandchild never started"
            grandchild_pid = int(pidfile.read_text().strip())
            grandchild_start_time = _process_start_time(grandchild_pid)

            deadline = time.monotonic() + 10.0
            while _pid_alive(grandchild_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not _pid_alive(grandchild_pid), (
                "grandchild (inherited handles, direct child already "
                "exited) survived -- the Job Object close did not reach it"
            )
        finally:
            if (
                grandchild_pid is not None
                and grandchild_start_time is not None
                and _pid_alive(grandchild_pid)
                and _process_start_time(grandchild_pid) == grandchild_start_time
            ):
                if sys.platform == "win32":
                    import subprocess as _subprocess

                    _subprocess.run(
                        ["taskkill", "/F", "/PID", str(grandchild_pid)],
                        capture_output=True, check=False,
                    )
                else:
                    import signal

                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(grandchild_pid, signal.SIGKILL)

    @_requires_descendant_safety_net
    @pytest.mark.asyncio
    async def test_cancellation_during_communicate_still_kills_the_descendant(
        self, tmp_path: Path
    ) -> None:
        """Dedicated regression for the cancellation-safety claim
        (``_run_bounded``'s own ``except BaseException`` branch): cancel
        the awaiting task after the direct child and its grandchild have
        both started, assert ``asyncio.CancelledError`` propagates out of
        ``_run_bounded`` (never silently absorbed), and that the
        grandchild does not survive -- proving cleanup actually ran before
        the cancellation finished propagating, not just that the claim
        exists in a docstring."""
        import asyncio
        import time

        pidfile = tmp_path / "grandchild.pid"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import os, time\n"
            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        script = tmp_path / "hangs.py"
        script.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, {str(grandchild_script)!r}])\n"
            "time.sleep(60)\n"
        )

        grandchild_pid: int | None = None
        grandchild_start_time: str | None = None
        try:
            task = asyncio.ensure_future(
                lcr._run_bounded([sys.executable, str(script)], timeout=30.0)
            )

            deadline = time.monotonic() + 10.0
            while not pidfile.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert pidfile.exists(), "grandchild never started"
            grandchild_pid = int(pidfile.read_text().strip())
            grandchild_start_time = _process_start_time(grandchild_pid)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            deadline = time.monotonic() + 10.0
            while _pid_alive(grandchild_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not _pid_alive(grandchild_pid), (
                "grandchild survived a cancelled _run_bounded call -- the "
                "cancellation-safety claim is not actually upheld"
            )
        finally:
            if (
                grandchild_pid is not None
                and grandchild_start_time is not None
                and _pid_alive(grandchild_pid)
                and _process_start_time(grandchild_pid) == grandchild_start_time
            ):
                if sys.platform == "win32":
                    import subprocess as _subprocess

                    _subprocess.run(
                        ["taskkill", "/F", "/PID", str(grandchild_pid)],
                        capture_output=True, check=False,
                    )
                else:
                    import signal

                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(grandchild_pid, signal.SIGKILL)


@pytest.mark.skipif(
    sys.platform == "win32", reason="this guard is POSIX-only code in _kill_tree"
)
class TestKillTreeIdentityGuard:
    """Direct, mocked tests of ``_kill_tree``'s POSIX identity-verification
    gate -- the repository's PID-destruction rule requires a dedicated
    mismatch/refusal unit test, not just the real end-to-end descendant
    regressions above (which only prove the happy identity path)."""

    @pytest.mark.asyncio
    async def test_refuses_to_signal_without_an_established_baseline(
        self, monkeypatch
    ) -> None:
        """``expected_group_identity is None`` must fail closed -- this is
        the universal case on any POSIX platform with no identity backend
        at all (macOS: ``process_start_time`` always returns ``None``
        there), not just a transient miss. Never trust an unverified
        numeric pid."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        killpg_calls = []
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append(pgid))
        monkeypatch.setattr(
            "zdd.diagnostics.process_start_time", lambda pid: None
        )

        proc = _MagicMock()
        proc.pid = 4321
        proc.kill = _MagicMock()
        proc.wait = _AsyncMock(return_value=0)

        await lcr._kill_tree(proc, None, None)

        assert killpg_calls == [], (
            "os.killpg was called with no established identity baseline -- "
            "the fail-closed guard did not hold"
        )
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_refuses_to_signal_on_a_confirmed_identity_mismatch(
        self, monkeypatch
    ) -> None:
        """A live, differing identity at the same numeric pid means the
        PID has been recycled by an unrelated process -- refuse the
        signal outright."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        killpg_calls = []
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append(pgid))
        monkeypatch.setattr(
            "zdd.diagnostics.process_start_time", lambda pid: "a-different-identity"
        )

        proc = _MagicMock()
        proc.pid = 4321
        proc.kill = _MagicMock()
        proc.wait = _AsyncMock(return_value=0)

        await lcr._kill_tree(proc, None, "the-original-identity")

        assert killpg_calls == [], (
            "os.killpg was called despite a confirmed identity mismatch"
        )

    @pytest.mark.asyncio
    async def test_signals_when_identity_is_established_and_not_contradicted(
        self, monkeypatch
    ) -> None:
        """The common, expected-to-succeed case: a real baseline was
        captured, and the current reading either matches it or is
        ``None`` (the leader already reaped) -- the signal must proceed."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        killpg_calls = []
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append(pgid))
        monkeypatch.setattr(os, "getpgid", lambda pid: 1 if pid == 0 else pid)
        monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: None)

        proc = _MagicMock()
        proc.pid = 4321
        proc.kill = _MagicMock()
        proc.wait = _AsyncMock(return_value=0)

        await lcr._kill_tree(proc, None, "the-original-identity")

        assert killpg_calls == [4321]


def _process_start_time(pid: int) -> str | None:
    """A stable, per-process start-time identity token for ``pid`` -- only
    needs to be stable for the process's life and differ when the pid is
    later reused; compared for equality, never interpreted as a wall
    clock. Mirrors ``agent_worktrees.locks.process_start_time``."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_t = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            ok = k32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            )
            if not ok:
                return None
            return f"{creation.dwHighDateTime}:{creation.dwLowDateTime}"
        finally:
            k32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            fields = fh.read().split(")")[-1].split()
        return fields[19]  # starttime, field 22 overall (1-indexed)
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        STILL_ACTIVE = 259
        return exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
