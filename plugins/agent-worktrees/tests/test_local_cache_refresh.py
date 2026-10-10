"""Tests for ``local_cache_refresh`` and its wiring into the worktree
lifecycle boundaries worktree-scoped-dynamic-guidance depends on: create,
resume, and ``sessionStart`` (see ``test_hook_ipc.py`` for the sessionStart
coverage). See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/2026/10/02 ambient-guidance-navigability`` Phase 7.

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
import subprocess
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import local_cache_refresh as lcr

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _renderer_json(*, changed=None, unchanged=None, warnings=0, blocking=0):
    return json.dumps({
        "operation": "render-local-cache",
        "changed": changed or [],
        "written": changed or [],
        "removed": [],
        "unchanged": unchanged or [],
        "warnings": warnings,
        "blocking": blocking,
        "findings": [],
    })


@pytest.mark.parametrize(
    "blocking,changed,returncode,expected",
    [(0, ["local"], 0, "ready"), (1, ["local"], 1, "partial"),
     (1, [], 1, "failed"), (0, [], 1, "failed")],
)
def test_renderer_outcomes_preserve_actual_delivery(blocking, changed, returncode, expected):
    result = lcr._render_result(
        _renderer_json(changed=changed, warnings=4, blocking=blocking), returncode
    )
    assert result.status == expected
    assert result.changed == len(changed)
    assert result.warnings == 4
    assert result.blocking == blocking


@pytest.mark.parametrize("raw", ["not-json", "{}", _renderer_json(warnings=True)])
def test_renderer_invalid_output_cannot_be_ready(raw):
    with pytest.raises(ValueError):
        lcr._render_result(raw, 0)


def test_cleanup_is_not_installation_and_blocking_findings_take_priority():
    data = json.loads(_renderer_json(changed=["removed"], blocking=1))
    data.update(written=[], removed=["removed"], findings=[
        {"severity": "warning", "check": "budget", "path": "large", "message": "audit"}
    ] * 3 + [{"severity": "blocking", "check": "unsafe", "path": "foreign", "message": "refused"}])
    result = lcr._render_result(json.dumps(data), 1)
    assert result.status == "failed"
    assert result.changed == 0
    assert result.removed == 1
    assert result.detail.startswith("unsafe: foreign: refused")


def test_dry_run_launch_never_refreshes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lcr, "refresh_local_cache",
        lambda root: pytest.fail("dry-run must not render guidance"),
    )
    assert lcr.prepare_for_launch(tmp_path, dry_run=True).status == "skipped"


def test_sessionstart_repairs_nested_worktree_guidance(tmp_path, monkeypatch):
    from agent_worktrees import push_timeout

    root = tmp_path / "repository"
    (root / ".git").mkdir(parents=True)
    nested = root / "src"
    nested.mkdir()
    sibling = root / ".github" / "instructions" / "demo.local.instructions.md"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("old", encoding="utf-8")
    monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **kwargs: tmp_path / "renderer.py")
    monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)

    def render(argv, **kwargs):
        assert Path(argv[3]) == root
        sibling.write_text("fresh", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0, stdout=_renderer_json(changed=["demo.local.instructions.md"], warnings=1)
        )

    monkeypatch.setattr(push_timeout, "run_bounded", render)
    diagnostic = lcr.sessionstart_diagnostic(str(nested), deadline=None)
    assert "ready" in diagnostic
    assert sibling.read_text(encoding="utf-8") == "fresh"
    assert not (nested / ".github").exists()


@pytest.mark.parametrize("trusted", [True, False], ids=["trusted", "untrusted"])
def test_real_global_discovery_and_renderer_fit_sessionstart_budget(tmp_path, monkeypatch, trusted):
    """Exercise both production subprocesses and source discovery, not stubs."""
    scripts = (
        _REPO_ROOT / "plugins" / "customizing-copilot" / "skills"
        / "reviewing-customizations" / "scripts"
    )
    if not scripts.is_dir():
        pytest.skip("customizing-copilot sibling plugin not checked out here")
    home = tmp_path / "home"
    installed = home / ".copilot" / "installed-plugins" / "test-market"
    renderer = installed / "customizing-copilot"
    shutil.copytree(scripts, renderer / "skills" / "reviewing-customizations" / "scripts")
    (renderer / "plugin.json").write_text(
        json.dumps({"name": "customizing-copilot", "version": "1.0.0"}), encoding="utf-8"
    )
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"customizing-copilot@test-market": True}}),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (home / ".copilot" / "config.json").write_text(
        json.dumps({"trustedFolders": [str(repo)] if trusted else []}), encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    names = [f"guidance-{index}" for index in range(8)]
    settings.write_text(
        json.dumps({"enabledPlugins": {f"{name}@test-market": True for name in names}}),
        encoding="utf-8",
    )
    templates = []
    siblings = []
    for name in names:
        plugin = installed / name
        template = plugin / "instructions" / "fallback.instructions.md"
        template.parent.mkdir(parents=True)
        template.write_text('---\napplyTo: "**"\n---\n\n' + "Useful guidance.\n" * 700, encoding="utf-8")
        templates.append(template)
        (plugin / "plugin.json").write_text(
            json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8"
        )
        (plugin / "instruction-projections.json").write_text(json.dumps({
            "schema": "copilot-extensions.instruction-projections", "version": 1,
            "projections": [{
                "id": "fallback", "template": "instructions/fallback.instructions.md",
                "destination": f".github/instructions/{name}/fallback.instructions.md",
                "customizationKind": "instructions", "applyTo": "**",
                "legacyMarkers": [f"{name}:static-fallback"],
            }],
        }), encoding="utf-8")
        siblings.append(repo / ".github" / "instructions" / name / "fallback.local.instructions.md")
    monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
    started = time.monotonic()
    first = lcr.refresh_local_cache(repo, home=home, timeout=lcr.SESSIONSTART_MAX_TIMEOUT_S)
    assert first.status == "ready", first.diagnostic
    if not trusted:
        assert first.changed == 0
        assert all(not path.exists() for path in siblings)
        return
    assert first.changed == len(names)
    assert first.warnings > 0
    assert time.monotonic() - started < lcr.SESSIONSTART_MAX_TIMEOUT_S
    assert sum(path.stat().st_size for path in siblings) > 48 * 1024
    assert all("Useful guidance.\n" * 700 in path.read_text(encoding="utf-8") for path in siblings)
    templates[0].write_text('---\napplyTo: "**"\n---\n\nUpdated useful guidance.\n', encoding="utf-8")
    repaired = lcr.refresh_local_cache(repo, home=home, timeout=lcr.SESSIONSTART_MAX_TIMEOUT_S)
    assert repaired.status == "ready", repaired.diagnostic
    assert repaired.changed == 1
    assert repaired.unchanged == len(names) - 1
    assert "Updated useful guidance." in siblings[0].read_text(encoding="utf-8")


def test_json_base_launch_prepares_guidance_before_command(tmp_path, monkeypatch, capfd):
    from agent_worktrees import resolve_cli
    import argparse

    events = []
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
    args = argparse.Namespace(dry_run=False)
    state = SimpleNamespace(
        load_config=lambda: config, requested_machine=None, use_base=True, use_new=False, args=args,
    )
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda config: None)
    monkeypatch.setattr(resolve_cli, "_preflight_launch", lambda *a: SimpleNamespace(error=None))
    monkeypatch.setattr(
        lcr, "prepare_for_launch", lambda root, **kwargs: events.append(("guidance", root))
    )
    monkeypatch.setattr(
        resolve_cli, "_build_launch_cmd", lambda *a, **kwargs: events.append(("command", None)) or ["copilot"]
    )
    monkeypatch.setattr(resolve_cli, "_repo_session_env", lambda *a: {})
    monkeypatch.setattr(resolve_cli, "_build_env", lambda *a, **kwargs: {})
    assert resolve_cli._resolve_json_mode(state) == 0
    assert events == [("guidance", str(tmp_path)), ("command", None)]
    assert json.loads(capfd.readouterr().out)["action"] == "exec"


def test_json_worktree_launch_prepares_selected_root(tmp_path, monkeypatch):
    import argparse
    from agent_worktrees import resolve_cli

    record_path = tmp_path / "example.yaml"
    record_path.write_text("fixture", encoding="utf-8")
    root = tmp_path / "selected"
    record = SimpleNamespace(worktree_path=str(root))
    state = SimpleNamespace(
        load_config=lambda: SimpleNamespace(), requested_machine=None, use_base=False,
        use_new=False, worktree_id="example", args=argparse.Namespace(dry_run=False),
    )
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda config: None)
    monkeypatch.setattr(resolve_cli.worktree_identity, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(resolve_cli, "_relocate_active_project_for_worktree", lambda value: False)
    monkeypatch.setattr(resolve_cli.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(resolve_cli.tracking, "load_record", lambda path: record)
    monkeypatch.setattr(resolve_cli, "_preflight_launch", lambda *a: SimpleNamespace(error=None))

    def prepared(selected_root, **kwargs):
        assert selected_root == str(root)
        assert kwargs == {"dry_run": False}
        raise RuntimeError("stopped after pre-session preparation")

    monkeypatch.setattr(lcr, "prepare_for_launch", prepared)
    with pytest.raises(RuntimeError, match="stopped after pre-session preparation"):
        resolve_cli._resolve_json_mode(state)


@pytest.mark.parametrize("machine", [None, "remote"])
def test_json_new_dry_run_cannot_create_or_render(tmp_path, monkeypatch, capfd, machine):
    import argparse
    from agent_worktrees import resolve_cli

    state = SimpleNamespace(
        load_config=lambda: SimpleNamespace(), requested_machine=machine, use_base=False,
        use_new=True, args=argparse.Namespace(dry_run=True),
    )
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda config: None)
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *args, **kwargs: pytest.fail("dry-run must not create a worktree"),
    )
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *args, **kwargs: pytest.fail("dry-run refusal must precede delegation"),
    )
    assert resolve_cli._resolve_json_mode(state) != 0
    assert "no worktree or guidance was created" in capfd.readouterr().out


class TestSelectGlobalRoot:
    """Direct, in-process tests of the pure resolution/filtering logic
    ``_resolve_cli_script``'s subprocess entry point calls. See
    ``TestResolveCliScript`` for the subprocess-wiring coverage."""

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
        """A matching-named plugin whose live roots are ONLY project-
        scoped (an agent-worktrees-registered project's local dev
        override of customizing-copilot, never this machine's own global
        install) must never be trusted -- a session in an unrelated repo
        must never execute that project's own local content."""
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
        """Two distinct active-plugin entries both claiming the expected
        name (different marketplace identities) must never be resolved by
        arbitrary iteration order -- an ambiguous identity is refused."""
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
    why), so these tests mock ``push_timeout.run_bounded`` rather than
    ``plugin_activation.resolve_active_plugins`` directly -- that logic
    has its own direct coverage in ``TestSelectGlobalRoot``."""

    def test_finds_the_script_when_the_subprocess_reports_a_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        plugin_root = tmp_path / "customizing-copilot"
        scripts_dir = (
            plugin_root / "skills" / "reviewing-customizations" / "scripts"
        )
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")

        from agent_worktrees import push_timeout

        monkeypatch.setattr(
            push_timeout, "run_bounded",
            lambda *a, **k: SimpleNamespace(stdout=f"{plugin_root}\n"),
        )

        assert lcr._resolve_cli_script(tmp_path, timeout=5.0) == script

    def test_fails_closed_when_the_subprocess_reports_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agent_worktrees import push_timeout

        monkeypatch.setattr(
            push_timeout, "run_bounded", lambda *a, **k: SimpleNamespace(stdout="\n"),
        )

        assert lcr._resolve_cli_script(tmp_path, timeout=5.0) is None

    def test_fails_closed_when_the_reported_root_has_no_script(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A reported root that doesn't actually contain the expected
        script (a stale or mismatched report) must never fall back to
        trusting anything else -- fail closed."""
        from agent_worktrees import push_timeout

        monkeypatch.setattr(
            push_timeout, "run_bounded",
            lambda *a, **k: SimpleNamespace(stdout=f"{tmp_path}\n"),
        )

        assert lcr._resolve_cli_script(tmp_path, timeout=5.0) is None

    def test_never_raises_when_the_subprocess_call_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agent_worktrees import push_timeout

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(push_timeout, "run_bounded", _boom)

        assert lcr._resolve_cli_script(tmp_path, timeout=5.0) is None

    def test_fails_closed_on_a_resolution_timeout(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A resolution subprocess that exceeds its own bound must fail
        closed -- ``push_timeout.run_bounded`` already kills the whole
        process tree on a stall, so this only needs to prove the raised
        ``TimeoutExpired`` is absorbed, never surfaced."""
        import subprocess

        from agent_worktrees import push_timeout

        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(push_timeout, "run_bounded", _timeout)

        assert lcr._resolve_cli_script(tmp_path, timeout=0.1) is None

    def test_real_subprocess_round_trip_against_an_empty_home(
        self, tmp_path: Path
    ) -> None:
        """Real, non-mocked round trip through ``push_timeout.run_
        bounded`` and the actual ``python -m agent_worktrees.local_cache_
        refresh`` subprocess entry point, proving the module is genuinely
        importable and runnable that way (not just as a library) -- an
        empty home with nothing installed resolves to nothing."""
        assert lcr._resolve_cli_script(tmp_path, timeout=30.0) is None



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
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **k: script)

        def _boom(*a, **k):
            raise RuntimeError("boom")

        from agent_worktrees import push_timeout

        monkeypatch.setattr(push_timeout, "run_bounded", _boom)
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_never_raises_on_a_subprocess_timeout(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **k: script)

        import subprocess as subprocess_mod

        from agent_worktrees import push_timeout

        def _timeout(*a, **k):
            raise subprocess_mod.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(push_timeout, "run_bounded", _timeout)
        repo = tmp_path / "repo"
        repo.mkdir()
        # Must not raise.
        lcr.refresh_local_cache(repo, home=tmp_path)

    def test_invokes_the_cli_with_the_expected_argv(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **k: script)
        monkeypatch.setattr(
            lcr, "_resolve_own_agent_worktrees_command", lambda: "/bin/agent-worktrees"
        )

        calls = []

        def _fake_run_bounded(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout=_renderer_json())

        from agent_worktrees import push_timeout

        monkeypatch.setattr(push_timeout, "run_bounded", _fake_run_bounded)

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
        assert 0 < kwargs["timeout"] <= 12.0

    def test_omits_agent_worktrees_path_when_unresolved(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **k: script)
        monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)

        calls = []

        def _fake_run_bounded(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        from agent_worktrees import push_timeout

        monkeypatch.setattr(push_timeout, "run_bounded", _fake_run_bounded)

        repo = tmp_path / "repo"
        repo.mkdir()
        lcr.refresh_local_cache(repo, home=tmp_path)

        assert "--agent-worktrees-path" not in calls[0]

    def test_descendant_of_a_timed_out_cli_does_not_survive(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Real-subprocess regression: a plain ``subprocess.run(
        timeout=...)`` only terminates its direct child, so a CLI
        invocation that itself spawns a descendant (the real CLI can
        launch an ``agent-worktrees`` lookup and git subprocesses, see
        ``scan_plugin_sources.py``) could leak that descendant past a
        timeout -- exactly the limitation ``push_timeout.py`` documents
        and ``test_git_ops.py``'s ``TestPushTimeoutTreeKill`` already
        proves ``run_bounded`` itself closes. This proves
        ``refresh_local_cache`` is actually wired to that tree-killing
        runner, not a bare ``subprocess.run``: a stand-in CLI script spawns
        a grandchild and hangs, and the grandchild must not survive the
        refresh's own timeout.
        """
        import os
        import platform
        import subprocess
        import time

        from agent_worktrees.locks import pid_alive, process_start_time

        # See TestPushTimeoutTreeKill's own docstring for why this must be
        # cleared: the repository's full-suite test runner sets this
        # ambiently to protect itself, which would otherwise silently
        # disable the real descendant sweep this test asserts on.
        monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)

        ready = tmp_path / "ready"
        pidfile = tmp_path / "grandchild.pid"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            f"import time\n"
            f"open({str(pidfile)!r}, 'w').write('x')\n"
            f"time.sleep(60)\n"
        )
        # Stand-in for manage-instruction-projections.py: spawns a
        # descendant (mirroring the real CLI's own subprocess calls) then
        # hangs well past the timeout below.
        script = tmp_path / "manage-instruction-projections.py"
        script.write_text(
            f"import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, {str(grandchild_script)!r}])\n"
            f"open({str(ready)!r}, 'w').write(str(p.pid))\n"
            f"time.sleep(60)\n"
        )
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **k: script)

        repo = tmp_path / "repo"
        repo.mkdir()

        grandchild_pid: int | None = None
        grandchild_start_time: str | None = None
        try:
            # Must not raise -- refresh_local_cache absorbs the timeout.
            # timeout is split between the (mocked, instant) resolution
            # step and the render subprocess -- see _RESOLUTION_TIMEOUT_S.
            lcr.refresh_local_cache(
                repo, home=tmp_path, timeout=lcr._RESOLUTION_TIMEOUT_S + 1.0
            )

            deadline = time.monotonic() + 10
            while (
                not (ready.exists() and pidfile.exists())
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            assert ready.exists() and pidfile.exists(), (
                "grandchild never started -- test setup issue, not a real assertion"
            )
            grandchild_pid = int(ready.read_text().strip())
            grandchild_start_time = process_start_time(grandchild_pid)

            deadline = time.monotonic() + 10
            alive = pid_alive(grandchild_pid)
            while alive and time.monotonic() < deadline:
                time.sleep(0.2)
                alive = pid_alive(grandchild_pid)
            assert not alive, "grandchild process survived refresh_local_cache's timeout"
        finally:
            if (
                grandchild_pid is not None
                and grandchild_start_time is not None
                and pid_alive(grandchild_pid)
                and process_start_time(grandchild_pid) == grandchild_start_time
            ):
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(grandchild_pid)],
                        capture_output=True, check=False,
                    )
                else:
                    import signal
                    try:
                        os.kill(grandchild_pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

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
    ``_run_session_lifecycle`` calls before registration on ``sessionStart``; see
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
        what actually remains -- after reserving margin for
        ``push_timeout.run_bounded``'s own post-kill cleanup wait (once,
        not per sequential bounded subprocess -- see
        ``_RUN_BOUNDED_CLEANUP_GRACE_S``'s own docstring for why), not
        just the subprocess timeout itself -- rather than risking the
        shared lifecycle deadline."""
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "refresh_local_cache",
            lambda repo_root, **k: calls.append(k.get("timeout")),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 9.0)

        assert len(calls) == 1
        assert 2.0 <= calls[0] < lcr.SESSIONSTART_MAX_TIMEOUT_S

    def test_skips_when_deadline_cannot_absorb_the_cleanup_grace(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """A deadline with enough room for the subprocess timeout alone
        (a flat 1s margin) but not enough to also absorb
        ``_RUN_BOUNDED_CLEANUP_GRACE_S`` must still skip -- a timed-out
        ``run_bounded`` call can spend that much longer past its own
        timeout draining pipes, and attempting the call anyway would risk
        the shared lifecycle deadline exactly the budget check exists to
        prevent."""
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "refresh_local_cache",
            lambda repo_root, **k: calls.append(k.get("timeout")),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 3.5)

        assert calls == []

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
