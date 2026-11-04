"""Join ordinary collected update planning to the guarded native producer."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="native Windows update"),
    pytest.mark.timeout(180),
]


@pytest.mark.parametrize("mode", ["success", "fail-pip"])
def test_collected_update_consumes_codespaces_repair(tmp_path, mode):
    repo = Path(__file__).resolve().parents[3]
    program = textwrap.dedent(r"""
        import argparse, importlib, json, os, sys
        from pathlib import Path
        from types import SimpleNamespace

        sys.path[:] = json.loads(sys.argv[2])
        sys.path.insert(0, sys.argv[3])
        fixture = importlib.import_module("plugins.agent-codespaces.tests.test_install_ps1_recovery")
        root, mode = Path(sys.argv[1]), sys.argv[4]
        case = fixture._repair_case(root)
        payload = root / "source"
        fixture._copy_plugin_tree(payload)
        manifest = case.runtime / "deploy-manifest.json"
        manifest.write_text(json.dumps({
            "source": {"version": fixture.VERSION},
            "runtime_version": case.version, "venv": str(case.slot),
        }))
        before = {name: (case.runtime / name).read_bytes() for name in (
            "current-version", "last-known-good", "deploy-manifest.json",
        )}
        environment = fixture._environment(
            case.home, case.fake_bin, root / "fake_uv.py", mode, uv_only=True,
        )
        os.environ.clear()
        os.environ.update(environment)
        from agent_worktrees import __main__ as core, config, reconcile
        from agent_worktrees import update_acquisition, update_cli, update_integration
        from agent_worktrees import update_runtime, update_runtime_plan, update_stage

        engine = root / "unrelated-engine"
        engine.mkdir()
        core._registered_plugin_targets = lambda: {
            "agent-codespaces": update_runtime._RegisteredPluginTarget(
                case.home, update_runtime._PluginActivation.ACTIVE,
            ),
        }
        core._find_installed_plugin_dir = lambda: engine
        core._resolve_copilot = lambda: "fixture-copilot"
        core._update_registered_plugins = lambda targets: True
        update_acquisition.acquire = lambda *a, **k: "OK (fixture acquisition)"
        update_integration.finish = lambda *a: True
        update_stage.application_status = lambda: ({}, None, None)
        config.detect_platform = lambda: "windows"
        update_runtime._machine_gate_blocks = lambda *a: False
        reconcile.core_installed_payload_dir = lambda name: payload if name == "agent-codespaces" else None
        reconcile.runtime_installer_environment = lambda name, path: (environment, case.runtime)
        update_runtime_plan.plan_engine = lambda *a, **k: update_runtime_plan.RuntimeUpdateStep(
            "agent-worktrees", "engine_runtime", engine, status="SKIPPED (current)",
            eligible=True, environment=environment,
        )
        original_plan = update_runtime_plan.plan_registered
        planned = []
        def plan(*a, **k):
            step = original_plan(*a, **k)
            planned.append(step.status)
            return step
        update_runtime_plan.plan_registered = plan
        args = argparse.Namespace(
            force=False, skip_modules=None, no_anchor_sync=True, no_prune_pivots=True,
        )
        rc = update_cli._update_in_plugin_steps(args)
        assert planned == [""], planned
        fixture.assert_installer_isolated(case.fake_bin)
        if mode == "fail-pip":
            assert rc != 0, rc
            assert fixture.snapshot_tree_manifest(case.slot) == case.before
            assert case.config.read_text() == "default_idle_timeout: 42\n"
            for name, content in before.items():
                assert (case.runtime / name).read_bytes() == content, name
            assert list((case.runtime / "versions").iterdir()) == [case.slot]
            assert fixture._construction_calls(case)
            print("COLLECTED_UPDATE_REPAIR_FAILED_WITH_STATE_PRESERVED")
        else:
            selected = fixture._assert_repair(case, SimpleNamespace(returncode=rc, stdout="outer update"))
            writes = fixture._construction_calls(case)
            assert writes
            repeated = update_cli._update_in_plugin_steps(args)
            assert planned == ["", "SKIPPED (current)"], planned
            assert fixture._assert_repair(
                case, SimpleNamespace(returncode=repeated, stdout="outer repeat"),
            ) == selected
            assert fixture._construction_calls(case) == writes
            print("COLLECTED_UPDATE_REPAIR_AND_CURRENT_REPEAT")
    """)
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), json.dumps(sys.path), str(repo), mode],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = (
        "COLLECTED_UPDATE_REPAIR_AND_CURRENT_REPEAT" if mode == "success"
        else "COLLECTED_UPDATE_REPAIR_FAILED_WITH_STATE_PRESERVED"
    )
    assert expected in result.stdout
