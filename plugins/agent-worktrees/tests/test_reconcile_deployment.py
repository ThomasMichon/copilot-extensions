"""Plugin reconciliation respects installation-owned publication policy."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import reconcile, reconcile_deployment, update_cli, update_stage

pytest_plugins = ["test_reconcile"]


@pytest.fixture
def deployment(
    env: Any, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> Any:
    plugin = getattr(request, "param", "agent-index")
    monkeypatch.setenv("HOME", str(env.home))
    monkeypatch.setenv("USERPROFILE", str(env.home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: env.home))
    native_run = subprocess.run

    def isolated_probe(command: list[str], **kwargs: Any) -> Any:
        if os.name == "nt" or not any(
            str(argument).endswith("installation_context.py") for argument in command
        ):
            return native_run(command, **kwargs)
        helper_index = next(
            index for index, argument in enumerate(command)
            if str(argument).endswith("installation_context.py")
        )
        bootstrap = (
            "import pwd,runpy,sys\n"
            "home=sys.argv[1]\n"
            "pwd.getpwuid=lambda uid: pwd.struct_passwd("
            "('fixture','x',uid,uid,'fixture',home,'/bin/sh'))\n"
            "sys.argv=sys.argv[2:]\n"
            "runpy.run_path(sys.argv[0],run_name='__main__')\n"
        )
        return native_run(
            [command[0], "-I", "-c", bootstrap, str(env.home), *command[helper_index:]],
            **kwargs,
        )

    monkeypatch.setattr(reconcile.subprocess, "run", isolated_probe)
    env.write_settings({f"{plugin}@{reconcile.MARKETPLACE}": True})
    env.payload = env.install_payload(plugin, "2.0.0", scope="universal")
    env.deploy_runtime(plugin, "1.0.0")
    env.root = env.home / f".{plugin}"
    env.context = None
    env.plugin = plugin

    def policy(state: str) -> None:
        if state == "invalid":
            (env.root / "runtime-deployment.json").write_text("invalid json", encoding="utf-8")
            return
        helper = reconcile._installation_context_helper(plugin, env.payload)
        resolution = reconcile._run_installation_mode_helper(
            plugin, env.payload, helper, env.home / f".{plugin}",
            context=env.context,
        )
        owner = {
            "sourceFingerprint": "sha256:" + hashlib.sha256(
                reconcile._CORE_SOURCE_RECORD.encode("utf-8"),
            ).hexdigest(),
            "pluginId": plugin, "mode": resolution["actualMode"],
            "runtimeRoot": str(env.root), "environment": resolution["environment"],
        }
        if state == "foreign":
            owner["sourceFingerprint"] = "sha256:" + "0" * 64
        record = {
            "schema": "copilot-extensions.runtime-deployment",
            "version": 2 if state == "unsupported" else 1,
            "owner": owner, "managed": state != "standalone",
        }
        (env.root / "runtime-deployment.json").write_text(
            json.dumps(record), encoding="utf-8",
        )

    env.policy = policy
    return env


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*") if path.is_file()
    }


def _versioned_observation(deployment, version):
    source = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime"
    scripts = deployment.payload / "scripts"
    for helper in source.glob("versioned_runtime*.py"):
        shutil.copyfile(helper, scripts / helper.name)
    slot = deployment.root / "versions" / version
    interpreter = slot / ("Scripts" if os.name == "nt" else "bin")
    interpreter.mkdir(parents=True, exist_ok=True)
    # These are resolver observations, not a claim that the fixture interpreter is runnable.
    (interpreter / ("python.exe" if os.name == "nt" else "python")).write_bytes(b"fixture")
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": version, "completed_at": "fixture", "pid": 0}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("installed", "serving", "local", "reason"),
    [
        ("3.0.0", "1.0.0", False, "runtime-payload-older"),
        ("1.0.0", "3.0.0", False, "runtime-payload-older"),
        ("1.0.0", None, True, "runtime-source-selection-required"),
        ("1.0.0", None, False, None),
    ],
)
def test_launch_freshness_preserves_both_versions_and_local_source(
    deployment, monkeypatch, installed, serving, local, reason,
):
    deployment.deploy_runtime(deployment.plugin, installed)
    if local:
        manifest_path = deployment.root / "deploy-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"].update(
            kind="local", path=str(deployment.home / "selected-source"),
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if serving:
        deployment.deploy_running(deployment.plugin, serving)
        monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False, include_payload_refresh=True,
    )
    assert _snapshot(deployment.home) == before
    assert [step["phase"] for step in plan["updates"]] == (
        ["payload", "runtime"] if reason is None else ["payload"]
    )
    if reason:
        assert plan["diagnostics"][0]["reason"] == reason
        assert plan["diagnostics"][0]["message"]
    else:
        assert reconcile_deployment.execution_diagnostic(plan["updates"][-1]) is None


@pytest.mark.parametrize("change", ["payload", "source", "selector", "serving"])
def test_actual_launch_apply_rejects_changed_freshness_before_installer(
    deployment, monkeypatch, change,
):
    from agent_worktrees import update_launch_cli

    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    if change == "payload":
        source = deployment.payload / "src"
        source.mkdir()
        (source / "changed.py").write_text("CHANGED = True\n", encoding="utf-8")
    elif change == "source":
        manifest = deployment.root / "deploy-manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["source"].update(kind="local", path=str(deployment.home / "another-source"))
        manifest.write_text(json.dumps(data), encoding="utf-8")
    elif change == "selector":
        (deployment.root / "current-version").write_text("3.0.0", encoding="utf-8")
    else:
        deployment.deploy_running(deployment.plugin, "3.0.0")
        monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    before = _snapshot(deployment.home)
    monkeypatch.setattr(
        reconcile, "_run_plan_step", lambda *args, **kwargs: pytest.fail("installer executed"),
    )
    result = update_launch_cli._apply(step)
    assert result["ok"] is False
    assert result["diagnostic"]["reason"] == "runtime-freshness-changed"
    assert _snapshot(deployment.home) == before


@pytest.mark.parametrize("change", ["forward", "older", "local", "payload"])
def test_native_launch_freshness_protocol(deployment, change):
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    if change in {"older", "local"}:
        manifest_path = deployment.root / "deploy-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"]["version"] = "3.0.0" if change == "older" else "2.0.0"
        if change == "local":
            manifest["source"].update(
                kind="local", path=str(deployment.home / "another-source"),
            )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif change == "payload":
        source = deployment.payload / "src"
        source.mkdir()
        (source / "changed.py").write_text("CHANGED = True\n", encoding="utf-8")
    before = _snapshot(deployment.root)
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from agent_worktrees import reconcile, __main__ as main\n"
        "payload,root=map(Path,sys.argv[1:])\n"
        "reconcile.core_installed_payload_dir=lambda name: payload\n"
        "reconcile.runtime_installer_environment=lambda *a,**kw: ({},root)\n"
        "raise SystemExit(main.main(['reconcile-plugins','--check-update']))\n"
    )
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script,
         str(deployment.payload), str(deployment.root)],
        input=json.dumps(step), text=True, encoding="utf-8", capture_output=True,
        timeout=30, check=False, **no_window_kwargs(),
    )
    assert run.returncode == (0 if change == "forward" else 3), run.stderr
    result = json.loads(run.stdout)
    assert result["allowed"] is (change == "forward")
    if change != "forward":
        assert result["diagnostics"][0]["reason"] == "runtime-freshness-changed"
    assert _snapshot(deployment.root) == before


def test_plan_refuses_preexisting_selected_deployment_disagreement(deployment):
    _versioned_observation(deployment, "3.0.0")
    (deployment.root / "current-version").write_text("3.0.0", encoding="utf-8")
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )
    assert plan["action"] == "continue"
    assert plan["diagnostics"][0]["reason"] == "runtime-freshness-unavailable"
    assert _snapshot(deployment.home) == before


@pytest.mark.parametrize(
    "parts",
    [
        ("scripts", "init.sh"), ("scripts", "init.ps1"),
        ("scripts", "runtime_install.py"), ("libs", "example", "pyproject.toml"),
    ],
)
def test_cached_action_includes_constructor_inputs(deployment, monkeypatch, parts):
    from agent_worktrees import update_launch_cli

    path = deployment.payload.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("original construction input\n", encoding="utf-8")
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    path.write_text("changed construction input\n", encoding="utf-8")
    before = _snapshot(deployment.root)
    monkeypatch.setattr(
        reconcile, "_run_plan_step", lambda *args, **kwargs: pytest.fail("installer executed"),
    )
    result = update_launch_cli._apply(step)
    assert result["diagnostic"]["reason"] == "runtime-freshness-changed"
    assert _snapshot(deployment.root) == before


@pytest.mark.parametrize("change", ["direct", "transitive", "missing"])
def test_cached_action_includes_external_local_dependencies(deployment, monkeypatch, change):
    from agent_worktrees import update_launch_cli

    dependency = deployment.payload.parent / "example-dependency"
    dependency.mkdir()
    (deployment.payload / "pyproject.toml").write_text(
        '[project]\nname = "example-plugin"\nversion = "2.0.0"\n'
        '[tool.uv.sources]\nexample-dependency = { path = "../example-dependency" }\n',
        encoding="utf-8",
    )
    (dependency / "pyproject.toml").write_text(
        '[project]\nname = "example-dependency"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    if change == "transitive":
        transitive = deployment.payload.parent / "example-transitive"
        transitive.mkdir()
        (dependency / "pyproject.toml").write_text(
            (dependency / "pyproject.toml").read_text(encoding="utf-8")
            + '[tool.uv.sources]\nexample-transitive = { path = "../example-transitive" }\n',
            encoding="utf-8",
        )
        (transitive / "pyproject.toml").write_text(
            '[project]\nname = "example-transitive"\nversion = "1.0.0"\n'
            '[tool.uv.sources]\nexample-dependency = { path = "../example-dependency" }\n',
            encoding="utf-8",
        )
        dependency = transitive
    source = dependency / "example.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    stage_fingerprint = update_stage.fingerprint(deployment.payload)
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    assert reconcile_deployment.execution_diagnostic(step) is None
    generated = dependency / ".venv"
    generated.mkdir()
    (generated / "generated.py").write_text("GENERATED = True\n", encoding="utf-8")
    assert reconcile_deployment.execution_diagnostic(step) is None
    if change == "missing":
        (dependency / "pyproject.toml").unlink()
    else:
        source.write_text("VALUE = 2\n", encoding="utf-8")
    assert update_stage.fingerprint(deployment.payload) == stage_fingerprint
    before = _snapshot(deployment.home)
    monkeypatch.setattr(
        reconcile, "_run_plan_step", lambda *args, **kwargs: pytest.fail("installer executed"),
    )
    result = update_launch_cli._apply(step)
    assert result["diagnostic"]["reason"] == (
        "runtime-freshness-unavailable" if change == "missing" else "runtime-freshness-changed"
    )
    assert _snapshot(deployment.home) == before


def test_apply_replans_after_its_own_successful_payload_acquisition(deployment, monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile, "resolve_copilot", lambda: "fixture-copilot")

    def run(argv):
        calls.append(argv)
        if argv[0] == "fixture-copilot":
            path = deployment.payload / "plugin.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["version"] = "3.0.0"
            path.write_text(json.dumps(manifest), encoding="utf-8")
        else:
            deployment.deploy_runtime(deployment.plugin, "3.0.0")
        return 0

    result = reconcile.apply_plan(
        deployment.repo, machine="test", runner=run, include_payload_refresh=True,
    )
    assert len(calls) == 2
    assert all(step["ok"] for step in result["executed"])
    assert reconcile.runtime_deployed_version(deployment.plugin, root=deployment.root) == "3.0.0"


@pytest.mark.parametrize("deployment", ["agent-worktrees"], indirect=True)
def test_stage_allows_same_package_repair_content_refresh(deployment, monkeypatch):
    from agent_worktrees import __main__ as core

    repair = "2.0.0+repair." + "a" * 32
    _versioned_observation(deployment, repair)
    deployment.deploy_runtime(deployment.plugin, "2.0.0")
    path = deployment.root / "deploy-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["runtime_version"] = repair
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (deployment.root / "current-version").write_text(repair, encoding="utf-8")
    monkeypatch.setattr(
        reconcile, "_runtime_running_record",
        lambda *args: {"version": repair, "runtime_slot": repair},
    )
    monkeypatch.setattr(
        update_stage, "discover_plugin_dir", lambda home: (deployment.payload, "marketplace"),
    )
    monkeypatch.setattr(core, "plan_pre_launch", lambda: {"action": "continue"})

    def refresh():
        path = deployment.payload / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["changed_content"] = True
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return True, "refreshed"

    monkeypatch.setattr(update_stage, "_run_copilot_update", refresh)
    before = _snapshot(deployment.root)
    result = update_stage.stage(
        status=deployment.home / "stage-status.json", lock=deployment.home / "stage-lock",
        home=deployment.home,
    )
    assert result["plugin_changed"] is True
    assert "runtime_apply_blocked" not in result
    assert _snapshot(deployment.root) == before


def test_reconcile_treats_same_package_serving_repair_as_current(deployment, monkeypatch):
    deployment.deploy_runtime(deployment.plugin, "2.0.0")
    repair = "2.0.0+repair." + "a" * 32
    monkeypatch.setattr(
        reconcile, "_runtime_running_record",
        lambda *args: {"version": repair, "runtime_slot": repair},
    )
    monkeypatch.setattr(reconcile, "runtime_running_version", lambda *args, **kwargs: repair)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )
    assert plan["action"] == "continue"
    assert "diagnostics" not in plan


@pytest.mark.parametrize(
    "version",
    [
        "2.0.0+repair." + "a" * 32,
        "2.0.0+source." + "a" * 40 + "." + "b" * 16 + ".repair." + "c" * 32,
        "2.0.0+base.source." + "a" * 40 + "." + "b" * 16,
        "2.0.0+build.metadata",
    ],
)
def test_freshness_normalization_matches_canonical_runtime_identity(version):
    from agent_worktrees.reconcile_freshness import runtime_package_version

    source = (
        Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime"
        / "versioned_runtime_source.py"
    )
    spec = importlib.util.spec_from_file_location("freshness_canonical_runtime_source", source)
    assert spec and spec.loader
    canonical = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = canonical
    spec.loader.exec_module(canonical)
    assert runtime_package_version(version) == canonical.package_version(version)


@pytest.mark.parametrize("selection", ["plain", "source", "source-mismatch"])
def test_profiled_source_selection_consistency(deployment, selection):
    from agent_worktrees.reconcile_freshness import check_runtime_freshness

    deployment.deploy_runtime(deployment.plugin, "2.0.0")
    receipt = {
        "schema": "copilot-extensions.runtime-source", "version": 1,
        "plugin": deployment.plugin, "package_version": "2.0.0",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": "c" * 64,
        "runtime_version": "2.0.0+source." + "a" * 40 + "." + "c" * 16,
    }
    selected = "2.0.0+base"
    path = deployment.root / "deploy-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if selection != "plain":
        commit = "d" * 40 if selection == "source-mismatch" else receipt["source_commit"]
        selected += ".source." + commit + "." + "c" * 16
        manifest["runtime_source"] = receipt
        (deployment.payload / "runtime-source.json").write_text(
            json.dumps(receipt), encoding="utf-8",
        )
    manifest.update(runtime_profile="base", runtime_version=selected)
    _versioned_observation(deployment, selected)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (deployment.root / "current-version").write_text(selected, encoding="utf-8")
    before = _snapshot(deployment.root)
    _facts, diagnostic = check_runtime_freshness(
        deployment.plugin, deployment.payload, deployment.root,
    )
    if selection == "source-mismatch":
        assert diagnostic["reason"] == "runtime-freshness-unavailable"
    else:
        assert diagnostic is None
    assert _snapshot(deployment.root) == before


@pytest.mark.parametrize("tier", ["last-known-good", "newest-complete", "broken-current"])
def test_plan_checks_canonical_effective_selection_not_just_the_marker(deployment, tier):
    _versioned_observation(deployment, "3.0.0")
    if tier == "last-known-good":
        (deployment.root / "last-known-good").write_text("3.0.0", encoding="utf-8")
    elif tier == "broken-current":
        (deployment.root / "current-version").write_text("1.0.0", encoding="utf-8")
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )
    assert plan["action"] == "continue"
    assert plan["diagnostics"][0]["reason"] == "runtime-freshness-unavailable"
    assert "package differs" in plan["diagnostics"][0]["message"]
    assert _snapshot(deployment.home) == before


@pytest.mark.parametrize("packaged", [False, True])
def test_current_selection_does_not_inherit_newer_unselected_slot_veto(deployment, packaged):
    _versioned_observation(deployment, "1.0.0")
    _versioned_observation(deployment, "3.0.0")
    if packaged:
        helper = deployment.payload / "scripts" / "versioned_runtime.py"
        helper.rename(helper.with_name("_versioned_runtime.py"))
    (deployment.root / "current-version").write_text("1.0.0", encoding="utf-8")
    (deployment.root / "last-known-good").write_text("3.0.0", encoding="utf-8")
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )
    assert plan["updates"][0]["runtime_freshness"]["resolved_selection"] == "1.0.0"
    assert reconcile_deployment.execution_diagnostic(plan["updates"][0]) is None
    assert _snapshot(deployment.home) == before


def test_selected_lookup_refuses_ambiguous_primitives(deployment):
    _versioned_observation(deployment, "1.0.0")
    helper = deployment.payload / "scripts" / "versioned_runtime.py"
    shutil.copyfile(helper, helper.with_name("_versioned_runtime.py"))
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )
    assert plan["action"] == "continue"
    assert "exactly one" in plan["diagnostics"][0]["message"]
    assert _snapshot(deployment.home) == before


def test_effective_fallback_change_invalidates_cached_action(deployment, monkeypatch):
    from agent_worktrees import update_launch_cli

    _versioned_observation(deployment, "1.0.0")
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    _versioned_observation(deployment, "3.0.0")
    before = _snapshot(deployment.root)
    monkeypatch.setattr(
        reconcile, "_run_plan_step", lambda *args, **kwargs: pytest.fail("installer executed"),
    )
    result = update_launch_cli._apply(step)
    assert result["diagnostic"]["reason"] == "runtime-freshness-changed"
    assert _snapshot(deployment.root) == before


@pytest.mark.parametrize("case", ["missing-receipt", "different-source", "malformed", "lost-manifest"])
def test_launch_freshness_refuses_same_version_source_or_unavailable_evidence(deployment, case):
    from agent_worktrees.reconcile_freshness import check_runtime_freshness

    deployment.deploy_runtime(deployment.plugin, "2.0.0")
    manifest_path = deployment.root / "deploy-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = {
        "schema": "copilot-extensions.runtime-source", "version": 1,
        "plugin": deployment.plugin, "package_version": "2.0.0",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": "c" * 64, "runtime_version": "2.0.0+source.fixture",
    }
    manifest["runtime_source"] = receipt
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if case == "different-source":
        (deployment.payload / "runtime-source.json").write_text(
            json.dumps({**receipt, "source_commit": "d" * 40}), encoding="utf-8",
        )
    elif case == "malformed":
        manifest_path.write_text("{", encoding="utf-8")
    elif case == "lost-manifest":
        manifest_path.unlink()
        (deployment.root / "current-version").write_text("2.0.0", encoding="utf-8")
    before = _snapshot(deployment.home)
    _facts, diagnostic = check_runtime_freshness(
        deployment.plugin, deployment.payload, deployment.root,
    )
    assert diagnostic["reason"] == (
        "runtime-source-selection-required"
        if case in {"missing-receipt", "different-source"} else "runtime-freshness-unavailable"
    )
    assert _snapshot(deployment.home) == before


@pytest.mark.parametrize("deployment", ["agent-worktrees"], indirect=True)
@pytest.mark.parametrize("producer", ["stage", "prelaunch"])
def test_native_launch_producers_preserve_newer_local_runtime(deployment, monkeypatch, producer):
    from types import SimpleNamespace
    from agent_worktrees import __main__ as core

    deployment.deploy_runtime(deployment.plugin, "3.0.0")
    manifest_path = deployment.root / "deploy-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"].update(kind="local", path=str(deployment.home / "selected-source"))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = _snapshot(deployment.root)
    if producer == "stage":
        monkeypatch.setattr(
            update_stage, "discover_plugin_dir", lambda home: (deployment.payload, "marketplace"),
        )
        refreshes = []
        monkeypatch.setattr(
            update_stage, "_run_copilot_update", lambda: (refreshes.append(True) or True, "refreshed"),
        )
        monkeypatch.setattr(core, "plan_pre_launch", lambda: {"action": "continue"})
        result = update_stage.stage(
            status=deployment.home / "stage-status.json", lock=deployment.home / "stage-lock",
            home=deployment.home,
        )
        assert refreshes == [True]
        assert result["plugin_changed"] is False
        assert result["runtime_apply_blocked"] == "runtime-payload-older"
    else:
        monkeypatch.setattr(update_cli.worktree_identity, "_find_repo_dir", lambda: deployment.repo)
        monkeypatch.setattr(update_cli.cfg, "load_config", lambda **kwargs: SimpleNamespace(
            default_repo=SimpleNamespace(service_paths=None, bootstrap_services=[]),
        ))
        monkeypatch.setattr(update_cli, "_resolve_environment", lambda config: "test")
        monkeypatch.setattr(update_cli.svc, "discover_services", lambda *args, **kwargs: [])
        monkeypatch.setattr(update_cli.svc, "check_staleness", lambda *args: "unknown")
        result = update_cli.plan_pre_launch()
        assert result["action"] == "continue"
        assert result["diagnostics"][0]["reason"] == "runtime-payload-older"
    assert _snapshot(deployment.root) == before


@pytest.mark.parametrize(
    ("state", "allowed", "reason"),
    [
        ("missing", True, None), ("standalone", True, None),
        ("managed", False, "deployment-managed"),
        ("invalid", False, "deployment-policy-invalid"),
        ("unsupported", False, "deployment-policy-version-unsupported"),
        ("foreign", False, "deployment-policy-invalid"),
    ],
)
def test_plan_uses_real_deployment_probe_without_publication(
    deployment: Any, state: str, allowed: bool, reason: str | None,
) -> None:
    if state != "missing":
        deployment.policy(state)
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
        include_payload_refresh=True,
    )
    assert _snapshot(deployment.home) == before
    phases = [step["phase"] for step in plan.get("updates", [])]
    assert phases == (["payload", "runtime"] if allowed else ["payload"])
    if reason is not None:
        diagnostic = next(d for d in plan["diagnostics"] if d["reason"] == reason)
        assert diagnostic["message"]
        assert diagnostic["service"] == "agent-index"


def test_managed_target_does_not_disable_other_runtime(deployment: Any) -> None:
    deployment.policy("managed")
    deployment.write_settings({
        f"agent-index@{reconcile.MARKETPLACE}": True,
        f"agent-bridge@{reconcile.MARKETPLACE}": True,
    })
    deployment.install_payload("agent-bridge", "2.0.0", scope="universal")
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert [(step["service"], step["phase"]) for step in plan["updates"]] == [
        ("agent-bridge", "runtime"),
    ]
    assert plan["diagnostics"][0]["reason"] == "deployment-managed"


@pytest.mark.parametrize("custom", [False, True])
def test_execution_rechecks_policy_added_after_plan(
    deployment: Any, monkeypatch: pytest.MonkeyPatch, custom: bool,
) -> None:
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    deployment.policy("managed")
    before = _snapshot(deployment.home)
    monkeypatch.setattr(reconcile, "build_plan", lambda *args, **kwargs: plan)
    calls: list[Any] = []
    monkeypatch.setattr(
        reconcile, "_run_plan_step", lambda *args: calls.append(args) or 0,
    )
    logs: list[str] = []
    summary = reconcile.apply_plan(
        deployment.repo, machine="test", passes=1, log=logs.append,
        runner=(lambda argv: calls.append(argv) or 0) if custom else None,
    )
    assert calls == []
    assert _snapshot(deployment.home) == before
    assert summary["executed"][0]["returncode"] == 3
    assert summary["executed"][0]["diagnostic"]["reason"] == "deployment-managed"
    assert any("explicit installation management" in line for line in logs)


def test_execution_rejects_changed_root(deployment: Any) -> None:
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    step = plan["updates"][0]
    step["runtime_root"] = str(deployment.home / "another-runtime")
    diagnostic = reconcile_deployment.execution_diagnostic(step)
    assert diagnostic is not None
    assert "runtime_root changed" in diagnostic["message"]


@pytest.mark.parametrize("state", ["missing", "standalone", "managed", "invalid"])
@pytest.mark.parametrize("old_helper", [False, True])
def test_native_execution_cli_rechecks_cached_plan(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], state: str, old_helper: bool,
) -> None:
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    if state != "missing":
        deployment.policy(state)
    if old_helper:
        monkeypatch.setattr(reconcile, "_installation_context_helper", lambda *args: None)
    before = _snapshot(deployment.home)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(step)))
    monkeypatch.setattr(
        update_cli.worktree_identity, "_find_repo_dir",
        lambda: pytest.fail("Execution admission must not discover a repository"),
    )
    allowed = state == "missing" or (state == "standalone" and not old_helper)
    parser = argparse.ArgumentParser()
    update_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    result = update_cli.cmd_reconcile_plugins(
        parser.parse_args(["reconcile-plugins", "--check-update"]),
    )
    report = json.loads(capsys.readouterr().out)
    assert result == (0 if allowed else 3)
    assert report["allowed"] is allowed
    assert bool(report["diagnostics"]) is not allowed
    assert _snapshot(deployment.home) == before


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", "null", '{"phase":"payload","service":"agent-index"}',
     '{"phase":"runtime","service":"agent-index","environment":null}',
     '{"phase":"runtime","service":"agent-index","environment":{"COPILOT_EXTENSIONS_CONTEXT":1}}'],
)
def test_native_execution_cli_refuses_invalid_input(
    raw: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    monkeypatch.setattr(
        reconcile_deployment, "execution_diagnostic",
        lambda update: pytest.fail("Invalid input reached execution admission"),
    )
    assert update_cli.cmd_reconcile_plugins(argparse.Namespace(check_update=True)) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["allowed"] is False
    assert report["diagnostics"][0]["reason"] == "execution-admission-invalid"


@pytest.mark.parametrize("field", ["runtime_root", "payload_root", "environment"])
def test_native_execution_cli_refuses_changed_installation(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], field: str,
) -> None:
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    step[field] = (
        {"COPILOT_EXTENSIONS_CONTEXT": str(deployment.home / "other-context")}
        if field == "environment" else str(deployment.home / "other-root")
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(step)))
    assert update_cli.cmd_reconcile_plugins(argparse.Namespace(check_update=True)) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["allowed"] is False
    assert "changed; rebuild the plan" in report["diagnostics"][0]["message"]


@pytest.mark.parametrize("managed", [False, True])
def test_native_execution_front_door_needs_no_project(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], managed: bool,
) -> None:
    from agent_worktrees import __main__ as main

    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    if managed:
        deployment.policy("managed")
    main.cfg.set_active_project(None)
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.chdir(deployment.home)
    monkeypatch.setattr(
        main, "_resolve_active_project",
        lambda project: pytest.fail("Execution admission tried to resolve a project"),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(step)))
    assert main.main(["reconcile-plugins", "--check-update"]) == (3 if managed else 0)
    report = json.loads(capsys.readouterr().out)
    assert report["allowed"] is not managed


@pytest.mark.parametrize("deployment", ["agent-worktrees"], indirect=True)
@pytest.mark.parametrize(
    "state",
    ["missing", "standalone", "managed", "old-helper", "old-helper-policy", "root-changed",
     "payload-changed", "context-changed", "marketplace-arrived"],
)
def test_direct_stage_execution_retains_owning_discovery(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], state: str,
) -> None:
    from agent_worktrees import __main__ as core

    direct = deployment.home / ".copilot" / "installed-plugins" / "_direct" / "worktrees"
    direct.parent.mkdir(parents=True)
    deployment.payload.rename(direct)
    deployment.payload = direct
    assert reconcile.core_installed_payload_dir("agent-worktrees") is None
    monkeypatch.setattr(
        update_stage, "_run_copilot_update",
        lambda: pytest.fail("Direct staging must not refresh marketplace content"),
    )
    monkeypatch.setattr(core, "plan_pre_launch", lambda: {"action": "continue"})
    staged = update_stage.stage(
        status=deployment.home / "stage-status.json",
        lock=deployment.home / "stage-lock", home=deployment.home,
    )
    assert staged["layout"] == "direct" and staged["venv_drift"] is True
    assert staged["plugin_changed"] is True
    step = {
        "service": "agent-worktrees", "phase": "runtime", "trigger": "stage-update",
        "payload_root": staged["plugin_dir"], "runtime_root": staged["runtime_root"],
        "environment": staged["environment"],
    }
    # Ordinary marketplace reconciliation cannot borrow staged direct discovery.
    assert reconcile_deployment.execution_diagnostic(step) is not None
    if state in {"standalone", "managed"}:
        deployment.policy(state)
    elif state in {"old-helper", "old-helper-policy"}:
        if state == "old-helper-policy":
            deployment.policy("standalone")
        monkeypatch.setattr(reconcile, "_installation_context_helper", lambda *args: None)
    elif state == "root-changed":
        step["runtime_root"] = str(deployment.home / "other-runtime")
    elif state == "payload-changed":
        step["payload_root"] = str(deployment.home / "other-payload")
    elif state == "context-changed":
        step["environment"] = {"COPILOT_EXTENSIONS_CONTEXT": str(deployment.home / "other-context")}
    elif state == "marketplace-arrived":
        deployment.install_payload("agent-worktrees", "3.0.0", scope="universal")
    before = _snapshot(deployment.home)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(step)))
    allowed = state in {"missing", "standalone", "old-helper"}
    assert update_cli.cmd_reconcile_plugins(argparse.Namespace(check_update=True)) == (
        0 if allowed else 3
    )
    report = json.loads(capsys.readouterr().out)
    assert report["allowed"] is allowed
    assert _snapshot(deployment.home) == before


def test_staged_discovery_cannot_select_other_plugins(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    step = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
    )["updates"][0]
    step["trigger"] = "stage-update"
    monkeypatch.setattr(
        update_stage, "discover_plugin_dir",
        lambda: pytest.fail("A foreign plugin reached staged Worktrees discovery"),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(step)))
    assert update_cli.cmd_reconcile_plugins(argparse.Namespace(check_update=True)) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["allowed"] is False
    assert "only supported for agent-worktrees" in report["diagnostics"][0]["message"]


@pytest.mark.parametrize("phase", ["payload", "uninstall"])
def test_nonpublication_steps_do_not_probe_runtime(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: pytest.fail(name),
    )
    assert reconcile_deployment.execution_diagnostic({
        "phase": phase, "service": "agent-worktrees",
    }) is None


def test_old_helper_cannot_ignore_present_policy(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("managed")
    monkeypatch.setattr(reconcile, "_installation_context_helper", lambda *args: None)
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None
    assert diagnostic["reason"] == "deployment-policy-unavailable"
    assert "update the plugin adapter explicitly" in diagnostic["message"]


def test_environment_hint_is_not_publication_authority(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("managed")
    monkeypatch.setenv("COPILOT_EXTENSIONS_MANAGED_UPDATE", "1")
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None and diagnostic["reason"] == "deployment-managed"


def test_deactivation_status_does_not_authorize_a_legacy_owner(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("standalone")
    original = reconcile._run_installation_mode_helper

    def wrong_status(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)
        return {**result, "status": "deactivation-required"}

    monkeypatch.setattr(reconcile, "_run_installation_mode_helper", wrong_status)
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None
    assert diagnostic["reason"] == "deployment-policy-unavailable"


def _select_namespaced(deployment: Any, *, enabled: bool) -> None:
    plugin = deployment.plugin
    helper = reconcile._installation_context_helper(plugin, deployment.payload)
    current = reconcile._run_installation_mode_helper(
        plugin, deployment.payload, helper, deployment.root, context=None,
    )
    context, root = deployment.select_context(
        plugin, deployment.payload, "2.0.0", "1.0.0",
    )
    receipt = json.loads(context.read_text(encoding="utf-8"))
    namespace = json.loads(Path(receipt["namespaceReceipt"]).read_text(encoding="utf-8"))
    (root / "installation-activation.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-activation", "version": 1,
        "marketplaceId": reconcile.CORE_MARKETPLACE_ID, "pluginId": plugin,
        "mode": "namespaced", "state": "active",
        "environment": current["environment"], "context": str(context),
        "namespaceGeneration": namespace["generation"],
        "installGeneration": receipt["generation"], "generation": 1,
        "legacy": {
            "disposition": "absent",
            "probe": {"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        },
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    (deployment.home / ".copilot-extensions" / "installation-mode.json").write_text(
        json.dumps({
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": enabled},
        }), encoding="utf-8",
    )
    # The legacy policy is deliberately invalid. Only the active cell owns
    # this runtime decision; probing the conventional root would refuse it.
    deployment.policy("invalid")
    deployment.root, deployment.context = root, context


@pytest.mark.parametrize("state", ["standalone", "managed", "invalid"])
@pytest.mark.parametrize("phase", ["plan", "execution"])
def test_namespaced_policy_uses_actual_root_not_legacy(
    deployment: Any, state: str, phase: str,
) -> None:
    _select_namespaced(deployment, enabled=True)
    root, context = deployment.root, deployment.context
    deployment.policy(state)
    before = _snapshot(deployment.home)
    if phase == "execution":
        diagnostic = reconcile_deployment.execution_diagnostic({
            "phase": "runtime", "service": "agent-index",
            "runtime_root": str(root), "payload_root": str(deployment.payload),
            "environment": {"COPILOT_EXTENSIONS_CONTEXT": str(context)},
        })
        assert _snapshot(deployment.home) == before
        if state == "standalone":
            assert diagnostic is None
        else:
            assert diagnostic["reason"] == (
                "deployment-managed" if state == "managed" else "deployment-policy-invalid"
            )
        return
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert _snapshot(deployment.home) == before
    if state == "standalone":
        # Publication permission does not invent an unsupported cell installer.
        assert plan["action"] == "continue"
        assert plan["diagnostics"][0]["reason"] == "installation-context-unsupported"
    else:
        assert plan["action"] == "continue"
        assert plan["diagnostics"][0]["reason"] == (
            "deployment-managed" if state == "managed" else "deployment-policy-invalid"
        )


def test_managed_healthy_runtime_is_not_republished(deployment: Any) -> None:
    deployment.policy("managed")
    deployment.deploy_runtime("agent-index", "2.0.0")
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert plan["action"] == "continue"
    assert _snapshot(deployment.home) == before


def test_old_probe_rejects_unknown_publication_action(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("managed")
    monkeypatch.setattr(
        reconcile.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, "", "invalid choice: probe-plugin-publication",
        ),
    )
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None and diagnostic["reason"] == "deployment-policy-unavailable"
    assert "invalid choice" in diagnostic["message"]


@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_aliased_home_retains_installation_policy_scope(
    deployment: Any, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    if state != "missing":
        deployment.policy(state)
    physical_home = deployment.home
    alias = physical_home.parent / "home-alias"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(physical_home)],
            capture_output=True, check=True, timeout=10, **no_window_kwargs(),
        )
    else:
        alias.symlink_to(physical_home, target_is_directory=True)
    monkeypatch.setattr(reconcile, "_home", lambda: alias)
    monkeypatch.setenv("HOME", str(alias))
    monkeypatch.setenv("USERPROFILE", str(alias))
    before = _snapshot(physical_home)
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert _snapshot(physical_home) == before
    if state == "managed":
        assert plan["action"] == "continue"
        assert plan["diagnostics"][0]["reason"] == "deployment-managed"
    else:
        assert plan["updates"][0]["runtime_root"] == str(alias / ".agent-index")
        assert reconcile_deployment.execution_diagnostic(plan["updates"][0]) is None
    assert _snapshot(physical_home) == before
    # The same normalization applies to optional context and policy paths;
    # missing, relative and wrong-target values still do not match.
    assert reconcile_deployment._matching_path(str(physical_home), alias)
    assert reconcile_deployment._matching_path(None, None)
    for value in (None, "", "relative", 1, str(alias.parent / "foreign")):
        assert not reconcile_deployment._matching_path(value, alias)
    assert not reconcile_deployment._matching_path(str(physical_home), None)


@pytest.mark.parametrize("deployment", ["agent-worktrees"], indirect=True)
@pytest.mark.parametrize("current", [False, True])
@pytest.mark.parametrize("deactivation_required", [False, True])
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("missing", None), ("standalone", None),
        ("managed", "deployment-managed"),
        ("invalid", "deployment-policy-invalid"),
        ("unsupported", "deployment-policy-version-unsupported"),
        ("foreign", "deployment-policy-invalid"),
    ],
)
def test_prelaunch_retains_custom_bootstrap_but_gates_runtime(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
    state: str, reason: str | None, current: bool, deactivation_required: bool,
) -> None:
    from types import SimpleNamespace

    if deactivation_required:
        _select_namespaced(deployment, enabled=False)
    if state != "missing":
        deployment.policy(state)
    (deployment.payload / "scripts" / "install.ps1").write_text("# installer\n", encoding="utf-8")
    installer = deployment.repo / "custom.ps1"
    installer.write_text("# custom bootstrap\n", encoding="utf-8")
    service = SimpleNamespace(name="custom", installer_path="custom.ps1")
    config = SimpleNamespace(default_repo=SimpleNamespace(
        service_paths=None, bootstrap_services=["custom"],
    ))
    monkeypatch.setattr(update_cli.worktree_identity, "_find_repo_dir", lambda: deployment.repo)
    monkeypatch.setattr(update_cli.cfg, "load_config", lambda **kwargs: config)
    monkeypatch.setattr(update_cli, "_resolve_environment", lambda config: "test")
    monkeypatch.setattr(update_cli.svc, "discover_services", lambda *args, **kwargs: [service])
    monkeypatch.setattr(
        update_cli.svc, "check_staleness", lambda *args: "current" if current else "behind",
    )
    monkeypatch.setattr(
        update_cli.svc, "get_service_status",
        lambda *args: SimpleNamespace(staleness="behind"),
    )
    before = _snapshot(deployment.home)
    plan = update_cli.plan_pre_launch()
    assert _snapshot(deployment.home) == before
    assert [step["service"] for step in plan["updates"]] == (
        ["agent-worktrees", "custom"] if reason is None and not current else ["custom"]
    )
    if current:
        assert "diagnostics" not in plan
    elif reason is None:
        step = plan["updates"][0]
        assert step["phase"] == "runtime"
        assert step["payload_root"] == str(deployment.payload)
        assert step["runtime_root"] == str(deployment.root)
        assert reconcile_deployment.execution_diagnostic(step) is None
    else:
        assert plan["diagnostics"][0]["reason"] == reason
        assert plan["diagnostics"][0]["message"]


@pytest.mark.parametrize("deployment", ["agent-worktrees"], indirect=True)
@pytest.mark.parametrize("state", ["missing", "standalone", "managed", "invalid", "probe-error"])
@pytest.mark.parametrize("trigger", ["drift", "payload", "current"])
@pytest.mark.parametrize("deactivation_required", [False, True])
def test_stage_refreshes_payload_without_republishing_managed_runtime(
    deployment: Any, monkeypatch: pytest.MonkeyPatch, state: str, trigger: str,
    deactivation_required: bool,
) -> None:
    if deactivation_required:
        _select_namespaced(deployment, enabled=False)
    if state == "probe-error":
        def failed_probe(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("publication probe failed")
        monkeypatch.setattr(reconcile, "publication_diagnostic", failed_probe)
    elif state != "missing":
        deployment.policy(state)
    if trigger != "drift":
        (deployment.root / "deploy-manifest.json").write_text(
            json.dumps({"schema_version": 3, "source": {"version": "2.0.0"}}),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        update_stage, "discover_plugin_dir", lambda home: (deployment.payload, "marketplace"),
    )
    calls: list[str] = []

    def refresh() -> tuple[bool, str]:
        calls.append("payload-refresh")
        if trigger == "payload":
            manifest = deployment.payload / "plugin.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["refreshed"] = True
            manifest.write_text(json.dumps(data), encoding="utf-8")
        return True, "payload refreshed"

    monkeypatch.setattr(update_stage, "_run_copilot_update", refresh)
    from agent_worktrees import __main__ as core
    monkeypatch.setattr(core, "plan_pre_launch", lambda: {"action": "continue"})
    before = _snapshot(deployment.root)
    result = update_stage.stage(
        status=deployment.home / "stage-status.json",
        lock=deployment.home / "stage-lock", home=deployment.home,
    )
    assert calls == ["payload-refresh"]
    manifest = json.loads((deployment.payload / "plugin.json").read_text(encoding="utf-8"))
    assert manifest.get("refreshed", False) is (trigger == "payload")
    assert _snapshot(deployment.root) == before
    assert result["venv_drift"] is (trigger == "drift")
    assert result["plugin_changed"] is (
        state in {"missing", "standalone"} and trigger != "current"
    )
    if state in {"managed", "invalid"} and trigger != "current":
        assert result["runtime_apply_blocked"] == (
            "deployment-managed" if state == "managed" else "deployment-policy-invalid"
        )
        assert result["runtime_apply_diagnostic"]["message"]
    elif state == "probe-error" and trigger != "current":
        assert result["runtime_apply_blocked"] == "venv-drift-check-failed"
        assert "publication probe failed" in result["venv_drift_error"]
    else:
        assert "runtime_apply_blocked" not in result
