"""Tests for ``local_cache_refresh`` and its wiring into the worktree
lifecycle boundaries worktree-scoped-dynamic-guidance depends on: create,
resume, and ``sessionStart`` (see ``test_hook_ipc.py`` for the sessionStart
coverage). See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/2026/10/02 ambient-guidance-navigability`` Phase 7.

This module invokes agent-worktrees' packaged
``render-local-cache`` CLI (``manage-instruction-projections.py``) across a
process boundary -- never discovering a sibling plugin's payload -- per
``docs/patterns/a-la-carte-independence.md``'s "no cross-plugin
reach-around" rule.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from dataclasses import replace
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




def test_identity_lookup_shares_overall_budget_without_two_second_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import push_timeout
    clock = iter([100.0, 100.0, 103.0])
    monkeypatch.setattr(lcr.time, "monotonic", lambda: next(clock))
    lookup_timeouts: list[float] = []
    render_timeouts: list[float] = []

    def resolve(_home, *, timeout, outcomes):
        lookup_timeouts.append(timeout)
        return tmp_path / "renderer.py"

    def run(argv, **kwargs):
        render_timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, _renderer_json(changed=["local"]), "")

    monkeypatch.setattr(lcr, "_resolve_cli_script", resolve)
    monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
    monkeypatch.setattr(push_timeout, "run_bounded", run)
    assert lcr.refresh_local_cache(tmp_path, timeout=10).status == "ready"
    assert lookup_timeouts == [10]
    assert render_timeouts == [7]


@pytest.mark.skipif(os.name == "nt", reason="POSIX launch process-group ownership")
@pytest.mark.parametrize("timeout,inherited", [(None, True), (10, False)])
def test_only_foreground_render_inherits_manager_process_group(monkeypatch, tmp_path, timeout, inherited):
    from agent_worktrees import push_timeout

    observed = []
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP", "1")
    monkeypatch.setattr(lcr, "_resolve_cli_script", lambda *_args, **_kwargs: tmp_path / "engine.py")
    monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)

    def run(argv, **kwargs):
        observed.append(kwargs.get("inherit_process_group", False))
        return subprocess.CompletedProcess(argv, 0, _renderer_json(), "")

    monkeypatch.setattr(push_timeout, "run_bounded", run)
    assert lcr.refresh_local_cache(tmp_path, timeout=timeout).status == "ready"
    assert observed == [inherited]


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


def test_foreground_projection_waits_and_reports_each_stage(tmp_path, monkeypatch, capsys):
    from agent_worktrees import push_timeout
    timeouts = []

    def resolve(_home, *, timeout, outcomes):
        timeouts.append(timeout)
        return tmp_path / "renderer.py"

    def render(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, _renderer_json(changed=["local"]), "")

    monkeypatch.setattr(lcr, "_resolve_cli_script", resolve)
    monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
    monkeypatch.setattr(push_timeout, "run_bounded", render)
    assert lcr.prepare_for_launch(tmp_path).status == "ready"
    assert timeouts == [None, None]
    captured = capsys.readouterr()
    assert captured.out == ""
    for label in ("Preparing instruction projections", "Resolving projection renderer", "Rendering instruction projections"):
        assert f"{label}: starting" in captured.err
        assert f"{label}: finished" in captured.err
    assert "1 installed" in captured.err


def test_launch_refresh_repairs_nested_worktree_guidance(tmp_path, monkeypatch):
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
    result = lcr.prepare_for_launch(str(nested))
    assert result.status == "ready"
    assert sibling.read_text(encoding="utf-8") == "fresh"
    assert not (nested / ".github").exists()


@pytest.mark.parametrize("trusted", [True, False], ids=["trusted", "untrusted"])
@pytest.mark.parametrize("delivery", ["launch", "backup"])
@pytest.mark.parametrize("renderer_scope", ["global", "project", "disabled"])
@pytest.mark.timeout(2 * lcr.DEFAULT_TIMEOUT_S + 15)
def test_packaged_renderer_fits_launch_and_backup_budget(
    tmp_path, monkeypatch, trusted, delivery, renderer_scope,
):
    """Exercise the standalone packaged engine and source discovery, not stubs."""
    from test_projection_runtime_assets import materialized_payload
    from agent_worktrees import installer

    payload = materialized_payload(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "_payload_root", lambda: payload)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
    home = tmp_path / "home"
    installed = home / ".copilot" / "installed-plugins" / "test-market"
    installed.mkdir(parents=True)
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {
            "customizing-copilot@test-market": renderer_scope == "global",
        }}), encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    (repo / ".gitignore").write_text("**/*.local.instructions.md\n", encoding="utf-8")
    (home / ".copilot" / "config.json").write_text(
        json.dumps({"trustedFolders": [str(repo)] if trusted else []}), encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    names = [f"guidance-{index}" for index in range(8)]
    settings.write_text(
        json.dumps({"enabledPlugins": {
            **{f"{name}@test-market": True for name in names},
            **({"customizing-copilot@test-market": True} if renderer_scope == "project" else {}),
        }}),
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
    if delivery == "backup":
        processes = []
        popen = subprocess.Popen

        def spawn(argv, **kwargs):
            process = popen(argv, **kwargs)
            if "agent_worktrees.local_cache_worker" in argv:
                processes.append(process)
            return process

        monkeypatch.setattr(lcr.subprocess, "Popen", spawn)
        diagnostic = lcr.sessionstart_diagnostic(str(repo), deadline=time.time() + 2.0)
        assert "[local-guidance] queued" in diagnostic
        assert "not yet delivered" in diagnostic
        assert len(processes) == 1
        directory = lcr._worker_directory(repo.resolve())
        try:
            code = processes[0].wait(timeout=lcr.DEFAULT_TIMEOUT_S + 10)
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"Worker stalled: {(directory / 'request.json').read_text()}; "
                f"{(directory / 'refresh.log').read_text()}"
            )
        log = (directory / "refresh.log").read_text(encoding="utf-8")
        assert code == 0, log
        assert f"[local-guidance] ready: {len(names) if trusted else 0} installed" in log
        assert all(path.exists() == trusted for path in siblings)
        if trusted:
            assert sum(path.stat().st_size for path in siblings) > 48 * 1024
            assert all("Useful guidance.\n" * 700 in path.read_text(encoding="utf-8") for path in siblings)
        return
    first = lcr.refresh_local_cache(repo, home=home)
    assert first.status == "ready", first.diagnostic
    if not trusted:
        assert first.changed == 0
        assert all(not path.exists() for path in siblings)
        return
    assert first.changed == len(names)
    assert first.warnings > 0
    assert time.monotonic() - started < lcr.DEFAULT_TIMEOUT_S
    assert sum(path.stat().st_size for path in siblings) > 48 * 1024
    assert all("Useful guidance.\n" * 700 in path.read_text(encoding="utf-8") for path in siblings)
    templates[0].write_text('---\napplyTo: "**"\n---\n\nUpdated useful guidance.\n', encoding="utf-8")
    started = time.monotonic()
    repaired = lcr.refresh_local_cache(repo, home=home)
    assert repaired.status == "ready", repaired.diagnostic
    assert time.monotonic() - started < lcr.DEFAULT_TIMEOUT_S
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


def test_json_worktree_launch_prepares_selected_root(tmp_path, monkeypatch, sample_record):
    import argparse
    from agent_worktrees import resolve_cli

    record_path = tmp_path / "example.yaml"
    record_path.write_text("fixture", encoding="utf-8")
    root = tmp_path / "selected"
    config = m.cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux", repo_name="demo",
        repos={"demo": m.cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        )},
    )
    record = replace(
        sample_record, worktree_id="example", worktree_path=str(root),
        repo=config.repo_name, machine=config.machine, platform=config.platform,
    )
    state = SimpleNamespace(
        load_config=lambda: config, requested_machine=None, use_base=False,
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
    @pytest.mark.parametrize("timeout", [5.0, 30.0])
    def test_discovery_and_render_share_the_callers_remaining_budget(
        self, tmp_path: Path, monkeypatch, timeout: float
    ) -> None:
        from agent_worktrees import push_timeout

        clock = [100.0]
        monkeypatch.setattr(lcr.time, "monotonic", lambda: clock[0])
        script = tmp_path / "renderer.py"

        def resolve(home, **kwargs):
            assert kwargs["timeout"] == timeout
            clock[0] += 2.5
            return script

        def render(argv, **kwargs):
            assert kwargs["timeout"] == timeout - 2.5
            return SimpleNamespace(returncode=0, stdout=_renderer_json(changed=["guidance"]))

        monkeypatch.setattr(lcr, "_resolve_cli_script", resolve)
        monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
        monkeypatch.setattr(push_timeout, "run_bounded", render)
        result = lcr.refresh_local_cache(tmp_path, home=tmp_path, timeout=timeout)
        assert result.status == "ready"
        assert result.changed == 1

    def test_exhausted_discovery_budget_never_starts_renderer(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agent_worktrees import push_timeout

        clock = [100.0]
        monkeypatch.setattr(lcr.time, "monotonic", lambda: clock[0])

        def resolve(home, **kwargs):
            clock[0] += kwargs["timeout"]
            return tmp_path / "renderer.py"

        monkeypatch.setattr(lcr, "_resolve_cli_script", resolve)
        monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
        monkeypatch.setattr(
            push_timeout, "run_bounded",
            lambda *a, **k: pytest.fail("no budget remains for rendering"),
        )
        result = lcr.refresh_local_cache(tmp_path, home=tmp_path, timeout=5.0)
        assert result.status == "timeout"
        assert result.detail == "pre-render lookup exhausted refresh budget"

    def test_command_lookup_also_consumes_the_shared_deadline(self, tmp_path, monkeypatch):
        from agent_worktrees import push_timeout

        clock = [100.0]
        monkeypatch.setattr(lcr.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda *a, **k: tmp_path / "renderer.py")

        def command():
            clock[0] += 3.0
            return None

        def render(argv, **kwargs):
            assert kwargs["timeout"] == 7.0
            return SimpleNamespace(returncode=0, stdout=_renderer_json())

        monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", command)
        monkeypatch.setattr(push_timeout, "run_bounded", render)
        assert lcr.refresh_local_cache(tmp_path, timeout=10.0).status == "ready"

    def test_real_slow_owned_render_still_publishes_guidance(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        script = tmp_path / "owned-engine" / "manage-instruction-projections.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            "import pathlib, sys, time\n"
            "time.sleep(2.1)\n"
            "pathlib.Path(sys.argv[2], 'guidance').write_text('delivered')\n"
            f"print({_renderer_json(changed=['guidance'])!r})\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(lcr, "_resolve_cli_script", lambda home, **kwargs: script)
        monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
        result = lcr.refresh_local_cache(tmp_path, home=tmp_path, timeout=10.0)
        assert result.status == "ready", result.diagnostic
        assert result.changed == 1
        assert (tmp_path / "guidance").read_text() == "delivered"

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
            # The render inherits the budget left by the instant resolution.
            lcr.refresh_local_cache(
                repo, home=tmp_path, timeout=3.0
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

    def test_queues_without_waiting_for_renderer(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root) or lcr.RefreshResult("queued"),
        )
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda *a, **k: pytest.fail("hook must not render"),
        )

        diagnostic = lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 200.0)

        assert calls == [str(tmp_path)]
        assert "[local-guidance] queued" in diagnostic

    def test_short_hook_budget_still_queues_without_rendering(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root) or lcr.RefreshResult("queued"),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 9.0)

        assert len(calls) == 1
        assert calls == [str(tmp_path)]

    def test_expired_deadline_never_spawns_worker(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() - 1.0)

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
            lcr, "_queue_refresh", lambda repo_root: calls.append(repo_root)
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 0.5)

        assert calls == []

    def test_queues_with_no_deadline(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root) or lcr.RefreshResult("queued"),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=None)

        assert calls == [str(tmp_path)]

    def test_spawn_failure_is_not_reported_as_delivery(self, tmp_path, monkeypatch):
        def fail(cwd):
            raise OSError("spawn refused")

        monkeypatch.setattr(lcr, "_queue_refresh", fail)
        diagnostic = lcr.sessionstart_diagnostic(str(tmp_path), deadline=None)
        assert "[local-guidance] failed" in diagnostic
        assert "spawn refused" in diagnostic

    def test_worker_is_headless_survivable_and_reaped(self, tmp_path, monkeypatch):
        from agent_worktrees import status_monitor_runtime, local_cache_worker

        calls = []
        waits = []
        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path / "worker")
        (tmp_path / "worker").mkdir()
        log_path = tmp_path / "worker" / "refresh.log"
        log_path.write_text("old result\n" * 100_000, encoding="utf-8")
        monkeypatch.setattr(status_monitor_runtime, "_daemon_cwd", lambda: str(tmp_path))
        monkeypatch.setattr(status_monitor_runtime, "_daemon_environment", lambda: {"HOME": str(tmp_path)})
        monkeypatch.setattr(
            local_cache_worker, "windowless_daemon_kwargs",
            lambda **kwargs: calls.append(kwargs) or {"start_new_session": True},
        )
        monkeypatch.setattr(
            lcr.subprocess, "Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(
                pid=123, wait=lambda: waits.append(True) or 0,
            ),
        )
        monkeypatch.setattr(local_cache_worker, "_owner", lambda pid: {"pid": pid, "start_time": "fixture"})
        result = lcr._queue_refresh(str(tmp_path))
        assert result.status == "queued"
        assert "not yet delivered" in result.detail
        assert calls[0] == {"breakaway": True}
        argv, kwargs = calls[1]
        assert argv[:5] == [
            sys.executable, "-m", "agent_worktrees.local_cache_worker",
            str(tmp_path.resolve()), str(tmp_path / "worker"),
        ]
        assert len(argv[5]) == 32
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["start_new_session"]
        assert log_path.stat().st_size < local_cache_worker._MAX_LOG_BYTES
        assert "old result" not in log_path.read_text()
        deadline = time.monotonic() + 1
        while not waits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert waits

    def test_active_worker_keeps_its_log_and_is_not_redispatched(self, tmp_path, monkeypatch):
        from single_instance_lease import SingleInstance

        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path)
        log_path = tmp_path / "refresh.log"
        log_path.write_text("current worker result", encoding="utf-8")
        monkeypatch.setattr(
            lcr.subprocess, "Popen", lambda *a, **k: pytest.fail("worker already owns checkout"),
        )
        lease = SingleInstance(tmp_path, service="local-guidance")
        lease.acquire()
        try:
            result = lcr._queue_refresh(str(tmp_path))
        finally:
            lease.release()
        assert result.status == "queued"
        assert "dispatch already reserved" in result.detail
        assert log_path.read_text() == "current worker result"

    def test_git_worktree_removal_reclaims_worker_metadata(self, tmp_path):
        anchor = tmp_path / "anchor"
        checkout = tmp_path / "checkout"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def git(*args):
            subprocess.run(
                ["git", *args], check=True, capture_output=True, creationflags=flags,
            )

        git("init", "--quiet", str(anchor))
        git(
            "-C", str(anchor), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "Fixture",
        )
        git("-C", str(anchor), "worktree", "add", "--quiet", "--detach", str(checkout), "HEAD")
        directory = lcr._worker_directory(checkout.resolve())
        assert directory.parent.parent == anchor / ".git" / "worktrees"
        directory.mkdir()
        (directory / "refresh.log").write_text("latest result", encoding="utf-8")
        git("-C", str(anchor), "worktree", "remove", str(checkout))
        assert not directory.exists()

    def test_only_one_worker_renders_a_repository(self, tmp_path, monkeypatch, capsys):
        from single_instance_lease import SingleInstance

        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path)
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda *a, **k: pytest.fail("duplicate render"),
        )
        lease = SingleInstance(tmp_path, service="local-guidance")
        lease.acquire()
        try:
            assert lcr._run_refresh_worker(str(tmp_path)) == 0
        finally:
            lease.release()
        assert "another refresh worker" in capsys.readouterr().out

    @pytest.mark.parametrize("status,exit_code", [("ready", 0), ("timeout", 1), ("partial", 1)])
    def test_worker_records_its_real_outcome(self, tmp_path, monkeypatch, capsys, status, exit_code):
        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path)
        monkeypatch.setattr(lcr, "refresh_local_cache", lambda root: lcr.RefreshResult(status))
        assert lcr._run_refresh_worker(str(tmp_path)) == exit_code
        assert f"[local-guidance] {status}" in capsys.readouterr().out


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

    def _patched_resume(self, tmp_path: Path, monkeypatch, sample_record, *, dry_run: bool):
        import argparse

        config = self._config(tmp_path)
        worktree_path = tmp_path / "worktrees" / "wt-1"
        worktree_path.mkdir(parents=True)
        record = replace(
            sample_record,
            worktree_id="wt-1",
            repo=config.repo_name,
            machine=config.machine,
            platform=config.platform,
            worktree_path=str(worktree_path),
            branch="worktree/wt-1",
            sessions=[],
            resume_count=0,
            last_resumed_at=None,
            codename="test-record",
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
        self, tmp_path: Path, monkeypatch, sample_record
    ) -> None:
        record, config, args, worktree_path = self._patched_resume(
            tmp_path, monkeypatch, sample_record, dry_run=False
        )
        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        m._resolve_resume(record, config, args)

        assert calls == [str(worktree_path)]

    def test_dry_run_resume_never_refreshes(
        self, tmp_path: Path, monkeypatch, sample_record
    ) -> None:
        record, config, args, _worktree_path = self._patched_resume(
            tmp_path, monkeypatch, sample_record, dry_run=True
        )
        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        m._resolve_resume(record, config, args)

        assert calls == []
