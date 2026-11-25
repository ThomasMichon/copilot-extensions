"""Whole-set update preflight must finish before any combined installer runs."""

import argparse
from collections.abc import Mapping, Sequence
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock
from typing import TypeVar

import pytest
from agent_procutil import no_window_kwargs
from agent_worktrees.update_runtime_source import PreparedRuntimeSource
from agent_worktrees.update_runtime_plan import RuntimeUpdateStep

from agent_worktrees import __main__ as core
from agent_worktrees import config, reconcile, update_cli, update_runtime


pytestmark = pytest.mark.usefixtures("payload_subprocess_double")

Portfolio = tuple[Path, Path, list[Sequence[object]]]
PhasePortfolio = tuple[Path, Path, list[Sequence[object]], list[str]]
T = TypeVar("T")
R = TypeVar("R")


def record(items: list[T], item: T, result: R) -> R:
    items.append(item)
    return result


@pytest.fixture
def portfolio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Portfolio:
    engine = tmp_path / "agent-worktrees"
    peer = tmp_path / "agent-example"
    for directory in (engine, peer):
        (directory / "scripts").mkdir(parents=True)
        (directory / "scripts" / "install.sh").write_text("exit 0\n")
        (directory / "plugin.json").write_text(json.dumps({
            "name": directory.name, "version": "2", "runtimeScope": "universal",
        }))
    for extension in ("sh", "ps1"):
        (engine / "scripts" / f"install.{extension}").write_text(
            "# AGENT_WORKTREES_TARGETED_UPDATE\nexit 0\n",
        )
    targets = {"agent-example": update_runtime._RegisteredPluginTarget(
        context=tmp_path, activation=update_runtime._PluginActivation.ACTIVE,
    )}
    monkeypatch.setattr(core, "_registered_plugin_targets", lambda: targets)
    monkeypatch.setattr(core, "_resolve_copilot", lambda: "copilot")
    monkeypatch.setattr(core, "_update_registered_plugins", lambda targets: True)
    monkeypatch.setattr(core, "_find_installed_plugin_dir", lambda: engine)
    monkeypatch.setattr(core, "_invocation_update_context", lambda: tmp_path)
    monkeypatch.setattr(config, "detect_platform", lambda: "linux")
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: peer)
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *a: False)
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda name, path: ({"PLAN_TARGET": name}, tmp_path / name))
    calls: list[Sequence[object]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: record(calls, list(argv), SimpleNamespace(
                            returncode=0, stdout="", stderr="",
                        )))
    monkeypatch.setattr(update_cli, "_run_engine_installer",
                        lambda *a, **kw: record(calls, ['engine-installer'], 0))
    return engine, peer, calls


@pytest.fixture
def phase_portfolio(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch) -> PhasePortfolio:
    from agent_worktrees import update_acquisition, update_integration

    engine, peer, calls = portfolio
    planned: list[str] = []
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda name, directory: (
            record(planned, name, {"PLAN_TARGET": name}), directory,
        ),
    )
    monkeypatch.setattr(
        update_acquisition, "acquire",
        lambda name, *a, **kw: record(calls, ['payload', name], "OK"),
    )
    monkeypatch.setattr(
        core, "_update_registered_plugins",
        lambda targets, **kw: record(calls, ['payloads', *sorted(targets)], True),
    )
    monkeypatch.setattr(
        update_runtime, "_run_human_installer",
        lambda *a, **kw: record(calls, ['runtime', kw['cwd'].name], SimpleNamespace(status="OK")),
    )
    monkeypatch.setattr(update_integration, "finish",
                        lambda *a: record(calls, ['integration'], True))
    monkeypatch.setattr(core, "_fast_forward_project_anchors", lambda: calls.append(["anchors"]))
    monkeypatch.setattr(core, "_prune_stale_pivots_after_update", lambda: calls.append(["prune"]))
    return engine, peer, calls, planned


def _phase_args(phase: str | None, plugins: list[str] | None=None) -> argparse.Namespace:
    return argparse.Namespace(
        phase=phase, plugin=plugins, force=False, force_runtime=None,
        skip_modules=None, no_anchor_sync=False, no_prune_pivots=False,
    )


def test_engine_planning_does_not_preload_packaging_in_isolated_process(tmp_path: Path) -> None:
    from agent_worktrees import update_runtime_plan

    script = textwrap.dedent("""
        import json, sys
        from pathlib import Path
        sys.path[:0] = json.loads(sys.argv[1])
        from agent_worktrees import reconcile, update_runtime, update_runtime_plan
        assert Path(update_runtime_plan.__file__).resolve() == Path(sys.argv[2]).resolve()
        root = Path(sys.argv[3])
        reconcile.runtime_installer_environment = lambda *_args: ({}, root)
        reconcile.payload_version = lambda *_args: "2.0"
        reconcile.runtime_deployed_version = lambda *_args, **_kwargs: "1.0"
        update_runtime._installer_timeout = lambda *_args: 600
        update_runtime_plan._shell_prefix = lambda *_args, **_kwargs: (["fixture-shell"], True)
        assert "packaging" not in sys.modules
        result = update_runtime_plan.plan_engine(root, "windows", force=False, check_footprint=False)
        assert result.eligible and not result.status
        assert "packaging" not in sys.modules
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script,
         json.dumps([path for path in sys.path if path]), str(Path(update_runtime_plan.__file__).resolve()),
         str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("outcome", [
    "success", "failed-engine", "failed-peer", "failed-integration", "newer-stage", "already-current",
])
def test_comprehensive_update_acknowledges_only_successfully_consumed_stage(
    phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str,
) -> None:
    from agent_worktrees import update_stage, update_integration

    engine, peer, calls, planned = phase_portfolio
    status, lock = tmp_path / "updater-status.json", tmp_path / "updater.lock"
    prior = dict(
        stage_done=True, plugin_changed=True, plugin_dir=str(engine), runtime_root=str(engine),
        payload_version="1", deployed_version="0", ts=1,
    )
    update_stage._write_status(status, prior)
    monkeypatch.setattr(update_stage, "status_path", lambda *a: status)
    monkeypatch.setattr(update_stage, "lock_path", lambda *a: lock)
    deployed = {"version": "2" if outcome == "already-current" else "1"}
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: deployed["version"])

    def install(*args: object, **kwargs: object) -> int:
        if outcome == "failed-engine":
            return 7
        deployed["version"] = "2"
        if outcome == "newer-stage":
            update_stage._write_status(status, {**prior, "ts": 2})
        return 0

    monkeypatch.setattr(update_cli, "_run_engine_installer", install)
    monkeypatch.setattr(core, "_reconcile_registered_runtimes", lambda *a, **k: outcome != "failed-peer")
    monkeypatch.setattr(update_integration, "finish", lambda *a: outcome != "failed-integration")
    assert update_cli._update_in_plugin_steps(_phase_args(None)) == (
        7 if outcome == "failed-engine" else 1 if outcome.startswith("failed-") else 0
    )
    result = update_stage.read_status()
    assert result["plugin_changed"] is (outcome != "success")
    assert result["ts"] == (2 if outcome == "newer-stage" else 1)


def test_comprehensive_update_reads_staging_owner_separately_from_deployment_target(
    phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import update_stage

    engine, peer, calls, planned = phase_portfolio
    legacy, cell = tmp_path / "legacy", tmp_path / "cell"
    monkeypatch.setattr(config, "install_dir", lambda: legacy)
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda name, directory: ({}, cell if name == "agent-worktrees" else peer),
    )
    prior = dict(
        stage_done=True, plugin_changed=True, plugin_dir=str(engine), runtime_root=str(cell),
        payload_version="2", deployed_version="1",
    )
    update_stage._write_status(update_stage.status_path(), prior)
    deployed = {"version": "1"}
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: deployed["version"])
    monkeypatch.setattr(
        update_cli, "_run_engine_installer", lambda *a, **k: deployed.update(version="2") or 0,
    )
    assert update_cli._update_in_plugin_steps(_phase_args(None)) == 0
    assert update_stage.read_status()["plugin_changed"] is False
    assert not (cell / "updater-status.json").exists()


@pytest.mark.parametrize("kind", ["registered", "module"])
@pytest.mark.parametrize("phase", ["preflight", "runtimes"])
def test_selected_runtime_does_not_plan_or_execute_unrelated_work(
    phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, kind: str, phase: str | None,
) -> None:
    engine, peer, calls, planned = phase_portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
    target = update_runtime._RegisteredPluginTarget(
        context=engine.parent, activation=update_runtime._PluginActivation.ACTIVE,
    )
    discoveries: list[bool] = []
    monkeypatch.setattr(
        core, "_registered_plugin_targets",
        lambda: record(discoveries, True, {peer.name: target, "agent-unrelated": target}),
    )
    assert update_cli._update_in_plugin_steps(_phase_args(phase, [peer.name])) == 0
    assert discoveries == [True]
    assert planned == [peer.name]
    assert calls == ([] if phase == "preflight" else [["runtime", peer.name]])


@pytest.mark.parametrize("phase", ["anchors", "prune"])
def test_repository_phase_does_not_discover_plugins_or_runtimes(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, phase: str | None) -> None:
    _engine, _peer, calls, planned = phase_portfolio
    monkeypatch.setattr(core, "_registered_plugin_targets",
                        lambda: pytest.fail("unselected plugin discovery"))
    monkeypatch.setattr(core, "_find_installed_plugin_dir",
                        lambda: pytest.fail("unselected payload discovery"))
    assert update_cli._update_in_plugin_steps(_phase_args(phase)) == 0
    assert calls == [[phase]]
    assert planned == []


@pytest.mark.parametrize("engine_only", [False, True])
def test_payload_phase_only_acquires_selected_payload(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, engine_only: bool) -> None:
    _engine, peer, calls, planned = phase_portfolio
    monkeypatch.setattr(core, "_find_installed_plugin_dir",
                        lambda: pytest.fail("payload-only phase entered preflight"))
    name = "agent-worktrees" if engine_only else peer.name
    assert update_cli._update_in_plugin_steps(_phase_args("payloads", [name])) == 0
    assert calls == ([["payload", name]] if engine_only else [["payloads", name]])
    assert planned == []


def test_integration_phase_uses_engine_scope_without_plugin_sweep(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_runtime_plan

    _engine, _peer, calls, planned = phase_portfolio
    monkeypatch.setattr(core, "_registered_plugin_targets",
                        lambda: pytest.fail("integration enumerated plugins"))
    monkeypatch.setattr(update_runtime_plan, "plan_modules",
                        lambda *a, **kw: pytest.fail("integration planned modules"))
    monkeypatch.setattr(update_runtime_plan, "plan_engine",
                        lambda *a, **kw: pytest.fail("integration planned an installer"))
    assert update_cli._update_in_plugin_steps(_phase_args("integration")) == 0
    assert calls == [["integration"]]
    assert planned == ["agent-worktrees"]


def test_delegated_project_integration_uses_controller_owned_manager(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import project_update_controller, update_integration, update_runtime_plan

    _engine, _peer, calls, planned = phase_portfolio
    monkeypatch.setattr(config, "active_project", lambda: "example")
    monkeypatch.setattr(
        update_runtime_plan, "plan_modules", lambda *a, **kw: pytest.fail("integration planned modules"),
    )
    monkeypatch.setattr(
        update_integration, "finish",
        lambda _payload, _platform, _env, **kwargs: record(calls, ['integration', kwargs.get('manager_owned_by_controller')], True),
    )
    request = SimpleNamespace(authority="project", project="example", argv=())
    authority = SimpleNamespace(configured=True, mode="project", project="example")
    selected = _phase_args("integration")
    selected._controller_request = request
    selected._controller_authority = authority
    result = project_update_controller.dispatch_update(
        selected, lambda _args: pytest.fail("auth report ran"),
        lambda _args: pytest.fail("Manager handoff ran"),
        lambda: update_cli._update_in_plugin_steps(selected),
    )
    assert result == 0
    assert calls == [["integration", True]]
    assert planned == ["agent-worktrees"]
    assert not hasattr(selected, "_controller_owns_manager")


def test_standalone_integration_does_not_accept_boolean_owner_marker(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_integration

    _engine, _peer, calls, planned = phase_portfolio
    monkeypatch.setattr(
        update_integration, "finish",
        lambda _payload, _platform, _env, **kwargs: record(calls, ['integration', kwargs.get('manager_owned_by_controller')], True),
    )
    selected = _phase_args("integration")
    selected._controller_owns_manager = True
    assert update_cli._update_in_plugin_steps(selected) == 0
    assert calls == [["integration", None]]
    assert planned == ["agent-worktrees"]


def test_integration_phase_refuses_invalid_selected_scope(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    _engine, _peer, calls, _planned = phase_portfolio

    def refuse(*args: object) -> None:
        raise ValueError("selected installation context is invalid")

    monkeypatch.setattr(reconcile, "runtime_installer_environment", refuse)
    assert update_cli._update_in_plugin_steps(_phase_args("integration")) == 1
    assert calls == []


@pytest.mark.parametrize("phase", ["payloads", "preflight", "runtimes"])
def test_unknown_selected_plugin_refuses_before_any_effect(phase_portfolio: PhasePortfolio, phase: str | None) -> None:
    _engine, _peer, calls, planned = phase_portfolio
    assert update_cli._update_in_plugin_steps(_phase_args(phase, ["agent-typo"])) == 1
    assert not calls and not planned


def test_selected_runtime_blocker_does_not_run_other_phases(phase_portfolio: PhasePortfolio) -> None:
    _engine, peer, calls, _planned = phase_portfolio
    (peer / "scripts" / "install.sh").unlink()
    assert update_cli._update_in_plugin_steps(_phase_args("runtimes", [peer.name])) == 1
    assert calls == []


def test_selected_peer_requires_readable_module_ownership(phase_portfolio: PhasePortfolio) -> None:
    engine, peer, calls, _planned = phase_portfolio
    (engine / "modules.json").write_text("{")
    assert update_cli._update_in_plugin_steps(_phase_args("runtimes", [peer.name])) == 1
    assert calls == []


def test_engine_only_phase_does_not_read_unrelated_module_manifest(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_runtime_plan

    engine, _peer, calls, planned = phase_portfolio
    for extension in ("sh", "ps1"):
        (engine / "scripts" / f"install.{extension}").write_text("AGENT_WORKTREES_TARGETED_UPDATE")
    monkeypatch.setattr(update_runtime_plan, "plan_modules",
                        lambda *a, **kw: pytest.fail("engine-only phase read module manifest"))
    assert update_cli._update_in_plugin_steps(_phase_args("preflight", ["agent-worktrees"])) == 0
    assert calls == []
    assert planned == ["agent-worktrees"]


@pytest.mark.parametrize("phase", ["preflight", "runtimes"])
@pytest.mark.parametrize("supported", [False, True])
def test_engine_phase_refuses_unscoped_installer_and_suppresses_recovery(
    phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, phase: str | None, supported: bool,
) -> None:
    engine, _peer, calls, _planned = phase_portfolio
    for extension in ("sh", "ps1"):
        (engine / "scripts" / f"install.{extension}").write_text(
            "AGENT_WORKTREES_TARGETED_UPDATE" if supported else "old installer",
        )
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(
        update_cli, "_run_engine_installer",
        lambda argv, payload, deadline, environment, **kw:
        record(environments, dict(environment), 0),
    )
    args = _phase_args(phase, ["agent-worktrees"])
    args.force = True
    assert update_cli._update_in_plugin_steps(args) == (0 if supported else 1)
    assert calls == []
    assert len(environments) == int(supported and phase == "runtimes")
    for environment in environments:
        assert environment["AGENT_WORKTREES_TARGETED_UPDATE"] == "1"
        assert environment["AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION"] == "1"


@pytest.mark.parametrize("filtered", [False, True])
def test_failed_payload_refresh_does_not_escape_explicit_filter(
    phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, filtered: bool,
) -> None:
    from agent_worktrees import update_auth_context as auth

    engine, peer, calls, _planned = phase_portfolio
    selected_context = engine.parent / "selected-context"
    unrelated_context = engine.parent / "unrelated-context"
    target = update_runtime._RegisteredPluginTarget(
        selected_context, update_runtime._PluginActivation.ACTIVE,
    )
    monkeypatch.setattr(core, "_registered_plugin_targets", lambda: {peer.name: target})
    monkeypatch.setattr(core, "_update_registered_plugins", update_runtime._update_registered_plugins)
    monkeypatch.setattr(core, "_project_update_context", lambda: unrelated_context)
    prepared: list[Path] = []
    refreshed: list[Path] = []
    monkeypatch.setattr(auth, "prepare_marketplace", lambda context, **kw: prepared.append(context))
    monkeypatch.setattr(core, "_refresh_marketplace",
                        lambda marketplace, **kw: record(refreshed, kw['cwd'], False))
    monkeypatch.setattr(core, "_update_one_plugin_payload", lambda *a, **kw: "acquisition failed")
    assert update_cli._update_in_plugin_steps(
        _phase_args("payloads", [peer.name] if filtered else None),
    ) == 1
    expected = [selected_context] if filtered else [selected_context, unrelated_context]
    assert prepared == refreshed == expected
    assert calls == ([] if filtered else [["payload", "agent-worktrees"]])


@pytest.mark.parametrize("kind", ["registered", "module"])
def test_selected_runtime_failure_is_not_success_or_integration(phase_portfolio: PhasePortfolio, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    engine, peer, calls, _planned = phase_portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
    monkeypatch.setattr(update_runtime, "_run_human_installer",
                        lambda *a, **kw: SimpleNamespace(status="install exited 7"))
    assert update_cli._update_in_plugin_steps(_phase_args("runtimes", [peer.name])) == 1
    assert calls == []


@pytest.mark.parametrize("kind", ["registered", "module"])
@pytest.mark.parametrize("blocker", ["installer", "context", "deadline"])
def test_late_required_blocker_prevents_all_installers(
    portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch, kind: str, blocker: str,
) -> None:
    engine, peer, calls = portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({
            "modules": [{"name": peer.name}],
        }))
    if blocker == "installer":
        (peer / "scripts" / "install.sh").unlink()
    elif blocker == "context":
        def environment(name: str, directory: Path) -> tuple[dict[str, str], Path]:
            if name == peer.name:
                raise ValueError("foreign installation owner")
            return {}, engine
        monkeypatch.setattr(reconcile, "runtime_installer_environment", environment)
    else:
        def deadline(name: str, *args: object) -> int:
            if name == peer.name:
                raise ValueError("invalid installation deadline")
            return 600
        monkeypatch.setattr(update_runtime, "_installer_timeout", deadline)
    args = argparse.Namespace(no_manager=True, force=True, no_anchor_sync=True,
                              no_prune_pivots=True, skip_modules=None)
    assert update_cli._update_in_plugin_steps(args) == 1
    assert calls == [["copilot", "plugin", "update", "agent-worktrees@copilot-extensions"]]


def test_plan_execution_uses_collected_command_and_environment(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_runtime_plan

    engine, peer, calls = portfolio
    step = update_runtime_plan.plan_registered(peer.name, "linux", force=True)
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: pytest.fail("installation resolved twice"))
    monkeypatch.setattr(reconcile, "core_installed_payload_dir",
                        lambda *a: pytest.fail("payload discovered twice"))
    captured: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(update_runtime, "_run_human_installer",
                        lambda argv, **kw: record(captured, (argv, kw), SimpleNamespace(status="OK")))
    assert update_runtime._execute_runtime_step(step) == "OK"
    assert captured[0][0] == list(step.argv)
    assert captured[0][1]["env"] == {
        "PLAN_TARGET": peer.name,
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(Path.cwd().resolve()),
    }
    assert captured[0][1]["cwd"] == peer
    assert captured[0][1]["target_version"] == "2"


@pytest.mark.parametrize("name,explicit_repo", [
    ("agent-index", False), ("agent-index", True), ("agent-bridge", False),
])
def test_index_activation_retains_logical_repo_without_changing_artifact(
    portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch, name: str, explicit_repo: bool,
) -> None:
    from agent_worktrees import update_runtime_plan

    _engine, payload, _calls = portfolio
    project = payload.parent / "consumer"
    project.mkdir()
    explicit = payload.parent / "explicit-repo"
    environment = {"AGENT_INDEX_REPO": str(explicit)} if explicit_repo else {}
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda *args: (environment, payload.parent / "runtime"),
    )
    step = update_runtime_plan._select_inputs(
        update_runtime_plan.RuntimeUpdateStep(name, "module_update", payload), None, project,
    )
    assert not step.blocked
    assert step.artifact == payload and step.project_root == project
    assert step.environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] == str(project)
    expected = str(explicit) if explicit_repo else str(project) if name == "agent-index" else None
    assert step.environment.get("AGENT_INDEX_REPO") == expected


@pytest.mark.parametrize("prepared", [False, True])
def test_installer_project_overrides_inherited_staging_context(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch, prepared: bool) -> None:
    from agent_worktrees import update_runtime_plan, update_runtime_target

    _engine, payload, _calls = portfolio
    project = payload.parent / "consumer"
    project.mkdir()
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda *args: ({"COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(payload)}, payload),
    )
    monkeypatch.setattr(update_runtime_target, "plan_target", lambda *args: None)
    source = Mock(spec=PreparedRuntimeSource,
        payload=payload, identity="prepared-source", version="2", validate=lambda *args: None,
        installer_arguments=lambda *args: None, install_source=None, application=None,
    ) if prepared else None
    step = update_runtime_plan._select_inputs(
        update_runtime_plan.RuntimeUpdateStep("agent-example", "runtime_reconcile", payload),
        source, project,
    )
    assert not step.blocked
    assert step.environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] == str(project)


@pytest.mark.parametrize("kind", ["registered", "module"])
@pytest.mark.installer_receipts
@pytest.mark.parametrize("activation", [
    "enabled", "enabled-no-receipt", "disabled", "invalid", "missing-reader",
    "enabled-first", "enabled-second", "invalid-second", "aliased-machine-denied",
])
def test_native_installer_reads_vault_activation_for_invoking_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, activation: str) -> None:
    from agent_worktrees import update_runtime_plan

    project, payload, home = (tmp_path / name for name in ("consumer", "payload", "home"))
    for path in (project, payload / "scripts", home):
        path.mkdir(parents=True)
    enabled = activation.startswith("enabled")
    first_enabled = activation in {"enabled", "enabled-no-receipt", "enabled-first", "invalid-second", "aliased-machine-denied"}
    config = "{" if activation == "invalid" else json.dumps({"enabled": first_enabled})
    (project / ".agent-vault.json").write_text(config, encoding="utf-8")
    (payload / ".agent-vault.json").write_text('{"enabled":false}', encoding="utf-8")
    (payload / "plugin.json").write_text(
        '{"name":"agent-vault","version":"2","runtimeScope":"machine-gated"}', encoding="utf-8",
    )
    vault = Path(__file__).resolve().parents[2] / "agent-vault"
    reader = payload / "scripts" / "runtime-activation.py"
    if activation != "missing-reader":
        shutil.copy(vault / "scripts" / reader.name, reader)
        shutil.copytree(vault / "src", payload / "src")
        shutil.copytree(vault / "scripts" / "configuration-layers", payload / "scripts" / "configuration-layers")
    witness = tmp_path / "activation.json"
    environment = dict(
        os.environ, HOME=str(home), USERPROFILE=str(home),
        AGENT_VAULT_HOME=str(home / ".agent-vault"),
        AGENT_VAULT_CONFIG=str(home / "absent-config.json"),
        AGENT_RT_ROOT=str(home / ".agent-vault"),
        AGENT_CONFIGURATION_CONTEXT="", COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=str(payload),
        ACTIVATION_TEST_PYTHON=sys.executable, ACTIVATION_TEST_READER=str(reader),
        ACTIVATION_TEST_WITNESS=str(witness),
    )
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    (payload / "scripts" / "install.ps1").write_text(
        "$project = if ($env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) {\n"
        "    $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT\n"
        "} else { (Get-Location).ProviderPath }\n"
        "$result = & $env:ACTIVATION_TEST_PYTHON -I -B -X utf8 "
        "$env:ACTIVATION_TEST_READER --cwd $project\n"
        "$code = $LASTEXITCODE\n"
        '[IO.File]::WriteAllText($env:ACTIVATION_TEST_WITNESS, ($result -join "`n"))\n'
        "exit $code\n", encoding="utf-8",
    )
    (payload / "scripts" / "install.sh").write_text(
        '"$ACTIVATION_TEST_PYTHON" -I -B -X utf8 "$ACTIVATION_TEST_READER" '
        '--cwd "${COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT:-$PWD}" > "$ACTIVATION_TEST_WITNESS"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *args: (environment, home / ".agent-vault"))
    def deployed_version(*args: object, **kwargs: object) -> str | None:
        assert enabled, "only an enabled Vault requires a version"
        return None if activation == "enabled-no-receipt" else "2"

    monkeypatch.setattr(reconcile, "runtime_deployed_version", deployed_version)
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *args: False)
    platform = "windows" if os.name == "nt" else "linux"
    contexts: tuple[Path, ...] = (project,)
    if activation == "aliased-machine-denied":
        contexts += (project / ".." / project.name,)
        monkeypatch.setattr(update_runtime, "_machine_gate_blocks",
                            lambda name, scope, path: scope == "machine-gated" and path == project)
    if activation in {"enabled-first", "enabled-second", "invalid-second"}:
        other = tmp_path / "other"
        other.mkdir()
        (other / ".agent-vault.json").write_text(
            "{" if activation == "invalid-second" else json.dumps({"enabled": activation == "enabled-second"}),
        )
        contexts += (other,)
    engine = tmp_path / "engine"
    engine.mkdir()
    if kind == "module":
        (engine / "modules.json").write_text('{"modules":[{"name":"agent-vault","source":"payload"}]}')
    target = update_runtime._RegisteredPluginTarget(project, update_runtime._PluginActivation.ACTIVE, contexts)
    _, modules, registered = update_runtime_plan.plan_portfolio(
        engine, platform, None, force=True, targets={"agent-vault": target},
        selected_plugins=("agent-vault",), force_runtimes=(),
    )
    step, = modules if kind == "module" else registered
    if activation == "aliased-machine-denied":
        from agent_worktrees.update_gate import MACHINE_GATED_SKIP
        assert step.status == MACHINE_GATED_SKIP and not step.eligible
        assert not step.argv and not witness.exists()
    elif enabled:
        status = update_runtime._execute_runtime_step(step)
        if activation == "enabled-no-receipt":
            assert "runtime not at target" in status
        else:
            assert status == "OK"
        assert json.loads(witness.read_text(encoding="utf-8"))["state"] == "configured"
    else:
        assert step.blocked == (activation != "disabled")
        assert update_runtime._execute_runtime_step(step) == step.status
        if activation == "disabled":
            assert step.status == "SKIPPED (configuration-empty)"
            assert step.required and not step.eligible
            from agent_worktrees import installation_updates

            monkeypatch.setattr(update_runtime, "_registered_plugin_targets", lambda: {step.name: target})
            monkeypatch.setattr(installation_updates, "target_plan", lambda *args, **kwargs: step)
            monkeypatch.setattr(reconcile, "core_installed_payload_dir", {step.name: payload}.get)
            monkeypatch.setattr(reconcile, "discover_core_payload_dirs", lambda: [(step.name, payload)])
            monkeypatch.setattr(reconcile, "resolve_runtime_installation", lambda *args: SimpleNamespace(
                runtime_root=home / ".agent-vault", actual_mode="legacy", reason="fixture"))
            monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *args, **kwargs: None)
            monkeypatch.setattr(reconcile, "runtime_running_version", lambda *args, **kwargs: None)
            monkeypatch.setattr(update_runtime, "_refresh_marketplace", lambda *args, **kwargs: True)
            acquire = Mock(return_value="OK")
            monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", acquire)
            row = next(r for r in installation_updates.inventory()["entries"] if r["id"] == step.name)
            assert row["updatable"] and installation_updates.update_target(step.name) == 0
            acquire.assert_called_once_with(step.name, reconcile.MARKETPLACE, cwd=target.context)
            from agent_worktrees.update_runtime_source import check_source_coverage

            prepared = Mock(spec=PreparedRuntimeSource, payload=payload)
            sources = {row["id"]: prepared}
            _, modules, registered = update_runtime_plan.plan_portfolio(
                engine, platform, None, force=True, targets={step.name: target},
                selected_plugins=(step.name,), force_runtimes=(), sources=sources,
            )
            planned, = (*modules, *registered)
            prepared.validate.assert_called_once()
            assert planned.status == step.status and not planned.eligible
            check_source_coverage(sources, [planned])
            with pytest.raises(ValueError, match="unexpected: other"):
                check_source_coverage({**sources, "other": prepared}, [planned])
        assert not witness.exists()
    assert not (home / ".agent-vault").exists()


@pytest.mark.parametrize("kind", ["module", "registered"])
@pytest.mark.parametrize("known_repo", [False, True])
def test_vault_incomplete_discovery_cannot_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, known_repo: bool,
) -> None:
    from agent_worktrees import update_runtime_plan

    def unreadable(_repo: Path, **kwargs: object) -> list[str]:
        raise OSError("unreadable repository")

    monkeypatch.setattr(core, "_invocation_update_context", lambda: None)
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(repos={
        "other": SimpleNamespace(anchor=str(tmp_path / "other")),
    }))
    monkeypatch.setattr(reconcile, "read_enabled_plugins", unreadable)
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: ["agent-vault"] if known_repo else [])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: ["agent-vault"])
    targets = update_runtime._registered_plugin_targets()
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _: tmp_path)
    if kind == "module":
        (tmp_path / "modules.json").write_text('{"modules":[{"name":"agent-vault"}]}')
    _, modules, registered = update_runtime_plan.plan_portfolio(
        tmp_path, "linux", None, force=True, targets=targets, force_runtimes=(),
        selected_plugins=("agent-vault",),
    )
    step, = modules if kind == "module" else registered
    assert step.blocked and "discovery incomplete" in step.status and not step.argv


def test_global_vault_collects_inheriting_repositories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inherited, disabled = tmp_path / "inherited", tmp_path / "disabled"
    inherited.mkdir()
    settings = disabled / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"enabledPlugins":{"agent-vault@copilot-extensions":false}}')
    monkeypatch.setattr(core, "_invocation_update_context", lambda: disabled)
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(repos={
        "inherited": SimpleNamespace(anchor=str(inherited)),
    }))
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: ["agent-vault"])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: ["agent-vault"])
    target = update_runtime._registered_plugin_targets()["agent-vault"]
    assert target.context == inherited and target.planning_contexts("agent-vault") == (inherited,)


def test_source_index_without_repository_does_not_export_neutral_cwd(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_runtime_plan, update_runtime_target

    _engine, payload, _calls = portfolio
    source = Mock(spec=PreparedRuntimeSource, application=None,
        payload=payload, identity="prepared-source", version="2",
        validate=lambda *args: None,
        install_source=None, installer_arguments=lambda platform: [],
    )
    monkeypatch.setattr(update_runtime_target, "plan_target", lambda *args: None)
    step = update_runtime_plan._select_inputs(
        update_runtime_plan.RuntimeUpdateStep("agent-index", "runtime_reconcile", payload),
        source,
    )
    assert not step.blocked
    assert step.project_root is None and "AGENT_INDEX_REPO" not in step.environment
    assert step.environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] == str(Path.cwd().resolve())


@pytest.mark.parametrize("kind", ["registered", "module"])
@pytest.mark.parametrize("prepared", [False, True])
def test_index_update_selects_real_host_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, prepared: bool) -> None:
    from agent_procutil import no_window_kwargs
    from agent_worktrees import update_runtime_plan, update_runtime_target

    payload = Path(__file__).resolve().parents[2] / "agent-index"
    repos = {}
    for role, machine in (("client", "remote-fixture"), ("host", "local-fixture")):
        repo = tmp_path / role
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True,
                       timeout=10, **no_window_kwargs())
        (repo / ".agent-index").mkdir()
        (repo / ".agent-index" / "config.yaml").write_text(
            f"indexer:\n  machine: {machine}\n  ssh: {machine}\n", encoding="utf-8",
        )
        repos[role] = repo
    monkeypatch.setattr(core, "_invocation_update_context", lambda: repos["client"])
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(repos={
        role: SimpleNamespace(anchor=str(repos[role]))
        for role in ("client", "host")
    }))
    monkeypatch.setattr(config, "detect_machine", lambda *args: "local-fixture")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda *args: ["agent-index"])
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: [])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: ["agent-index"])
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda *args: payload)
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *args: (dict(os.environ, AGENT_INDEX_MACHINE="local-fixture"),
                                       tmp_path / "runtime"))
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *args, **kwargs: "old")
    gate_contexts: list[Path] = []
    monkeypatch.setattr(
        update_runtime, "_machine_gate_blocks",
        lambda name, scope, project: record(gate_contexts, project, project == repos["client"]),
    )
    target_environments: list[dict[str, str]] = []
    monkeypatch.setattr(
        update_runtime_target, "plan_target",
        lambda *args: record(target_environments, dict(args[-1]), None),
    )
    target = update_runtime._registered_plugin_targets()["agent-index"]
    assert set(target.repository_contexts) == set(repos.values())
    platform = "windows" if os.name == "nt" else "linux"
    source = Mock(spec=PreparedRuntimeSource, application=None,
                    payload=payload, identity="prepared-source", version="new",
                    validate=lambda *args: None, deployed_version=lambda *args, **kwargs: "old",
                    install_source=None, installer_arguments=lambda platform: [],
                ) if prepared else None
    if kind == "module":
        step = update_runtime_plan.plan_module("agent-index", payload, platform, force=True,
            gate_context=target.context, repository_contexts=target.repository_contexts, source=source)
    else:
        step = update_runtime_plan.plan_registered("agent-index", platform, force=True,
            gate_context=target.context, repository_contexts=target.repository_contexts, source=source)
    assert not step.blocked, step.status
    assert step.project_root == repos["host"]
    assert step.environment["AGENT_INDEX_REPO"] == str(repos["host"])
    assert step.artifact == payload
    assert gate_contexts and set(gate_contexts) == {repos["host"]}
    if prepared:
        assert target_environments[0]["AGENT_INDEX_REPO"] == str(repos["host"])


def test_index_multi_repository_role_failure_blocks_preflight(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_runtime_plan

    _engine, payload, calls = portfolio
    project = payload.parent / "consumer"
    other = payload.parent / "other"
    step = update_runtime_plan._select_inputs(
        update_runtime_plan.RuntimeUpdateStep("agent-index", "module_update", payload),
        None, project, repository_contexts=(project, other),
    )
    assert step.blocked and "role resolver is unavailable" in step.status
    assert not calls and not step.argv


def test_collected_environment_is_an_immutable_snapshot(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import update_runtime_plan

    engine, peer, calls = portfolio
    environment = {"SELECTED_OWNER": peer.name}
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a: (environment, peer))
    step = update_runtime_plan.plan_registered(peer.name, "linux", force=True)
    environment["SELECTED_OWNER"] = "changed"
    assert step.environment == {
        "SELECTED_OWNER": peer.name,
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(Path.cwd().resolve()),
    }
    assert isinstance(step.environment, MappingProxyType)


@pytest.mark.parametrize("kind", ["module", "registered"])
def test_whole_update_resolves_each_selected_installation_once(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    from agent_worktrees import update_integration, update_options

    engine, peer, calls = portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
    order = []
    def environment(name: str, directory: Path) -> tuple[dict[str, str], Path]:
        order.append(f"plan:{name}")
        return {"SELECTED": name}, directory
    monkeypatch.setattr(reconcile, "runtime_installer_environment", environment)
    monkeypatch.setattr(
        update_options, "eligible_runtimes",
        lambda *a: pytest.fail("forced-runtime validation repeated discovery"),
    )
    monkeypatch.setattr(update_cli, "_run_engine_installer",
                        lambda *a, **k: record(order, 'engine', 0))
    monkeypatch.setattr(update_runtime, "_run_human_installer",
                        lambda *a, **k: record(order, 'peer', SimpleNamespace(status="OK")))
    monkeypatch.setattr(update_integration, "finish",
                        lambda *a: record(order, 'finish', True))
    args = argparse.Namespace(
        force=False, force_runtime=[peer.name], skip_modules=None,
        no_anchor_sync=True, no_prune_pivots=True,
    )
    assert update_cli._update_in_plugin_steps(args) == 0
    assert order == ["plan:agent-worktrees", f"plan:{peer.name}", "engine", "peer", "finish"]


@pytest.mark.parametrize("kind", ["module", "registered"])
def test_current_runtime_does_not_require_an_unused_installer(portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    from agent_worktrees import update_runtime_plan

    engine, peer, calls = portfolio
    (peer / "scripts" / "install.sh").unlink()
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2")
    monkeypatch.setattr(update_runtime_plan, "_runtime_footprint_current", lambda step: True)
    if kind == "module":
        step = update_runtime_plan.plan_module(peer.name, peer, "linux", force=False)
    else:
        step = update_runtime_plan.plan_registered(peer.name, "linux", force=False)
    assert step.status == "SKIPPED (current)"
    assert not step.blocked
    assert update_runtime._execute_runtime_step(step) == "SKIPPED (current)"
    assert calls == []


def test_native_update_preflight_prevents_real_installer_side_effect(tmp_path: Path) -> None:
    """Cross a real Python process and require the actual runtime preflight failure."""
    program = textwrap.dedent(r"""
        import argparse, json, os, subprocess, sys
        from pathlib import Path
        sys.path[:] = json.loads(sys.argv[2])
        from agent_worktrees import __main__ as core, reconcile, update_acquisition, update_cli, update_runtime

        root = Path(sys.argv[1])
        engine, peer = root / "agent-worktrees", root / "agent-example"
        for payload in (engine, peer):
            (payload / "scripts").mkdir(parents=True)
            (payload / "plugin.json").write_text(json.dumps({
                "name": payload.name, "version": "2", "runtimeScope": "universal",
            }))
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
        marker = root / "activated"
        os.environ["UPDATE_PREFLIGHT_MARKER"] = str(marker)
        (engine / "scripts" / "install.ps1").write_text(
            "[IO.File]::WriteAllText($env:UPDATE_PREFLIGHT_MARKER, 'executed')\n")
        (engine / "scripts" / "install.sh").write_text(
            'printf executed > "$UPDATE_PREFLIGHT_MARKER"\n')
        core._registered_plugin_targets = lambda: {
            peer.name: update_runtime._RegisteredPluginTarget(
                root, update_runtime._PluginActivation.ACTIVE,
            ),
        }
        core._update_registered_plugins = lambda targets: True
        core._find_installed_plugin_dir = lambda: engine
        core._invocation_update_context = lambda: root
        core._resolve_copilot = lambda: "fixture-copilot"
        reconcile.runtime_installer_environment = lambda name, path: (dict(os.environ), root / name)
        reconcile.runtime_deployed_version = lambda *a, **k: "1"
        update_runtime._machine_gate_blocks = lambda *a: False
        update_acquisition._run_update = (
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "")
        )
        args = argparse.Namespace(force=True, skip_modules=None, no_anchor_sync=True,
                                  no_prune_pivots=True)
        rc = update_cli._update_in_plugin_steps(args)
        assert rc == 1 and not marker.exists()
        print("PREFLIGHT_BLOCKED_WITHOUT_ACTIVATION")
    """)
    dependency = Path(__file__).resolve().parents[3] / "libs" / "agent-procutil" / "src"
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), json.dumps([str(dependency), *sys.path])],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Required runtime preflight failed" in result.stdout + result.stderr
    assert "PREFLIGHT_BLOCKED_WITHOUT_ACTIVATION" in result.stdout


@pytest.mark.parametrize("kind", ["module", "registered"])
@pytest.mark.parametrize("change", ["none", "missing", "extra", "changed-artifact"])
def test_whole_update_keeps_prepared_sources_and_original_owners(
    portfolio: Portfolio, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, change: str,
) -> None:
    from agent_worktrees import update_integration
    from agent_worktrees.update_runtime_source import PIN_ENV, PreparedRuntimeSource
    from test_update_runtime_sources import native_packet

    engine, peer, calls = portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
    artifacts = tmp_path / "prepared"
    artifacts.mkdir()
    data = native_packet(artifacts, (engine.name, peer.name))
    if change == "missing":
        del data["sources"][peer.name]
    if change == "extra":
        data["sources"].update(native_packet(artifacts, ("inactive",))["sources"])
    for name in data["sources"]:
        artifact = artifacts / name / "payload"
        (artifact / "scripts").mkdir()
        (artifact / "scripts/install.sh").write_text("exit 0\n")
        (artifact / "plugin.json").write_bytes((engine / "plugin.json").read_bytes())
    validated: list[tuple[str, Path, Path]] = []
    executed: list[RuntimeUpdateStep] = []

    def validate(source: PreparedRuntimeSource, name: str, owner: Path, environment: Mapping[str, str]) -> None:
        validated.append((name, owner, source.payload))
        if change == "changed-artifact" and name == peer.name:
            raise ValueError("prepared payload bytes changed")

    monkeypatch.setattr(PreparedRuntimeSource, "validate", validate)
    monkeypatch.setattr(PreparedRuntimeSource, "deployed_version", lambda *a, **kw: None)
    monkeypatch.setattr(update_cli, "_run_engine_installer", lambda *a, **kw: (
        record(executed, kw['source_step'], 0)
    ))
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda step: (
        record(executed, step, "OK")
    ))
    monkeypatch.setattr(update_integration, "finish", lambda *a: True)
    args = argparse.Namespace(
        no_manager=True, runtime_sources=json.dumps(data), skip_modules=None,
        no_anchor_sync=True, no_prune_pivots=True,
    )
    assert update_cli._update_in_plugin_steps(args) == (0 if change == "none" else 1)
    assert len(executed) == (2 if change == "none" else 0)
    if change == "none":
        assert validated == [
            (engine.name, engine, artifacts / engine.name / "payload"),
            (peer.name, peer, artifacts / peer.name / "payload"),
        ]
        for step in executed:
            assert step.payload == (engine if step.name == engine.name else peer)
            assert step.artifact == artifacts / step.name / "payload"
            assert step.source is not None
            assert step.environment[PIN_ENV] == step.source.identity
            assert step.environment["COPILOT_PLUGIN_STAGED_FROM"] == str(step.payload)
            assert step.runtime_root == tmp_path / step.name
    assert calls == [["copilot", "plugin", "update", "agent-worktrees@copilot-extensions"]]
