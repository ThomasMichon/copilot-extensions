"""Whole-set update preflight must finish before any combined installer runs."""

import argparse
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import config, reconcile, update_cli, update_runtime


pytestmark = pytest.mark.usefixtures("payload_subprocess_double")


@pytest.fixture
def portfolio(monkeypatch, tmp_path):
    engine = tmp_path / "agent-worktrees"
    peer = tmp_path / "agent-example"
    for directory in (engine, peer):
        (directory / "scripts").mkdir(parents=True)
        (directory / "scripts" / "install.sh").write_text("exit 0\n")
        (directory / "plugin.json").write_text(json.dumps({
            "name": directory.name, "version": "2", "runtimeScope": "universal",
        }))
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
    calls = []
    monkeypatch.setattr(update_cli.subprocess, "run",
                        lambda argv, **kw: calls.append(list(argv)) or SimpleNamespace(
                            returncode=0, stdout="", stderr="",
                        ))
    monkeypatch.setattr(update_cli, "_run_engine_installer",
                        lambda *a, **kw: calls.append(["engine-installer"]) or 0)
    return engine, peer, calls


@pytest.mark.parametrize("kind", ["registered", "module"])
@pytest.mark.parametrize("blocker", ["installer", "context", "deadline"])
def test_late_required_blocker_prevents_all_installers(
    portfolio, monkeypatch, kind, blocker,
):
    engine, peer, calls = portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({
            "modules": [{"name": peer.name}],
        }))
    if blocker == "installer":
        (peer / "scripts" / "install.sh").unlink()
    elif blocker == "context":
        def environment(name, directory):
            if name == peer.name:
                raise ValueError("foreign installation owner")
            return {}, engine
        monkeypatch.setattr(reconcile, "runtime_installer_environment", environment)
    else:
        def deadline(name, *args):
            if name == peer.name:
                raise ValueError("invalid installation deadline")
            return 600
        monkeypatch.setattr(update_runtime, "_installer_timeout", deadline)
    args = argparse.Namespace(no_manager=True, force=True, no_anchor_sync=True,
                              no_prune_pivots=True, skip_modules=None)
    assert update_cli._update_in_plugin_steps(args) == 1
    assert calls == [["copilot", "plugin", "update", "agent-worktrees@copilot-extensions"]]


def test_plan_execution_uses_collected_command_and_environment(portfolio, monkeypatch):
    from agent_worktrees import update_runtime_plan

    engine, peer, calls = portfolio
    step = update_runtime_plan.plan_registered(peer.name, "linux", force=True)
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: pytest.fail("installation resolved twice"))
    monkeypatch.setattr(reconcile, "core_installed_payload_dir",
                        lambda *a: pytest.fail("payload discovered twice"))
    captured = []
    monkeypatch.setattr(update_runtime, "_run_human_installer",
                        lambda argv, **kw: captured.append((argv, kw)) or SimpleNamespace(status="OK"))
    assert update_runtime._execute_runtime_step(step) == "OK"
    assert captured[0][0] == list(step.argv)
    assert captured[0][1]["env"] == {"PLAN_TARGET": peer.name}
    assert captured[0][1]["cwd"] == peer
    assert captured[0][1]["target_version"] == "2"


def test_collected_environment_is_an_immutable_snapshot(portfolio, monkeypatch):
    from agent_worktrees import update_runtime_plan

    engine, peer, calls = portfolio
    environment = {"SELECTED_OWNER": peer.name}
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a: (environment, peer))
    step = update_runtime_plan.plan_registered(peer.name, "linux", force=True)
    environment["SELECTED_OWNER"] = "changed"
    assert step.environment == {"SELECTED_OWNER": peer.name}
    with pytest.raises(TypeError):
        step.environment["SELECTED_OWNER"] = "changed"


@pytest.mark.parametrize("kind", ["module", "registered"])
def test_whole_update_resolves_each_selected_installation_once(portfolio, monkeypatch, kind):
    from agent_worktrees import update_integration, update_options

    engine, peer, calls = portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
    order = []
    def environment(name, directory):
        order.append(f"plan:{name}")
        return {"SELECTED": name}, directory
    monkeypatch.setattr(reconcile, "runtime_installer_environment", environment)
    monkeypatch.setattr(
        update_options, "eligible_runtimes",
        lambda *a: pytest.fail("forced-runtime validation repeated discovery"),
    )
    monkeypatch.setattr(update_cli, "_run_engine_installer",
                        lambda *a, **k: order.append("engine") or 0)
    monkeypatch.setattr(update_runtime, "_run_human_installer",
                        lambda *a, **k: order.append("peer") or SimpleNamespace(status="OK"))
    monkeypatch.setattr(update_integration, "finish",
                        lambda *a: order.append("finish") or True)
    args = argparse.Namespace(
        force=False, force_runtime=[peer.name], skip_modules=None,
        no_anchor_sync=True, no_prune_pivots=True,
    )
    assert update_cli._update_in_plugin_steps(args) == 0
    assert order == ["plan:agent-worktrees", f"plan:{peer.name}", "engine", "peer", "finish"]


@pytest.mark.parametrize("kind", ["module", "registered"])
def test_current_runtime_does_not_require_an_unused_installer(portfolio, monkeypatch, kind):
    from agent_worktrees import update_runtime_plan

    engine, peer, calls = portfolio
    (peer / "scripts" / "install.sh").unlink()
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2")
    if kind == "module":
        step = update_runtime_plan.plan_module(peer.name, peer, "linux", force=False)
    else:
        step = update_runtime_plan.plan_registered(peer.name, "linux", force=False)
    assert step.status == "SKIPPED (current)"
    assert not step.blocked
    assert update_runtime._execute_runtime_step(step) == "SKIPPED (current)"
    assert calls == []


def test_native_update_preflight_prevents_real_installer_side_effect(tmp_path):
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
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Required runtime preflight failed" in result.stdout + result.stderr
    assert "PREFLIGHT_BLOCKED_WITHOUT_ACTIVATION" in result.stdout


@pytest.mark.parametrize("kind", ["module", "registered"])
@pytest.mark.parametrize("change", ["none", "missing", "extra", "changed-artifact"])
def test_whole_update_keeps_prepared_sources_and_original_owners(
    portfolio, monkeypatch, tmp_path, kind, change,
):
    from agent_worktrees import update_integration
    from agent_worktrees.update_runtime_source import PIN_ENV, PreparedRuntimeSource
    from test_update_runtime_sources import packet

    engine, peer, calls = portfolio
    if kind == "module":
        (engine / "modules.json").write_text(json.dumps({"modules": [{"name": peer.name}]}))
    artifacts = tmp_path / "prepared"
    artifacts.mkdir()
    data = packet(artifacts, (engine.name, peer.name))
    if change == "missing":
        del data["sources"][peer.name]
    if change == "extra":
        data["sources"].update(packet(artifacts, ("inactive",))["sources"])
    for name in data["sources"]:
        artifact = artifacts / name
        (artifact / "scripts").mkdir()
        (artifact / "scripts/install.sh").write_text("exit 0\n")
        (artifact / "plugin.json").write_bytes((engine / "plugin.json").read_bytes())
    validated, executed = [], []

    def validate(source, name, owner, environment):
        validated.append((name, owner, source.payload))
        if change == "changed-artifact" and name == peer.name:
            raise ValueError("prepared payload bytes changed")

    monkeypatch.setattr(PreparedRuntimeSource, "validate", validate)
    monkeypatch.setattr(PreparedRuntimeSource, "deployed_version", lambda *a: None)
    monkeypatch.setattr(update_cli, "_run_engine_installer", lambda *a, **kw: (
        executed.append(kw["source_step"]) or 0
    ))
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda step: (
        executed.append(step) or "OK"
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
            (engine.name, engine, artifacts / engine.name),
            (peer.name, peer, artifacts / peer.name),
        ]
        for step in executed:
            assert step.payload == (engine if step.name == engine.name else peer)
            assert step.artifact == artifacts / step.name
            assert step.environment[PIN_ENV] == step.source.identity
            assert step.environment["COPILOT_PLUGIN_STAGED_FROM"] == str(step.payload)
            assert step.runtime_root == tmp_path / step.name
    assert calls == [["copilot", "plugin", "update", "agent-worktrees@copilot-extensions"]]
