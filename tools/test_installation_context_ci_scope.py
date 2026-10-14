"""Coverage selection for canonical and derived installation diagnostics."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "installation_context_ci_scope", HERE / "installation_context_ci_scope.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    "path",
    [
        "libs/installation-context/_installation_context_mode_cli.py",
        "libs/installation-context/tests/test_installation_mode_governance.py",
        "plugins/agent-vault/scripts/installation-context/installation-context.ps1",
        "plugins/agent-ssh/scripts/installation-context/installation-context.sh",
        "plugins/agent-bridge/src/agent_bridge/_installation_context_files.py",
        "worktree-manager/src/worktree_manager/_installation_context.py",
        "worktree-manager/src/worktree_manager/agent_plugin_runtime.py",
        "worktree-manager/tests/test_agent_plugin_runtime.py",
        "worktree-manager/tests/_installation_context_fixtures.py",
        "libs/peer-launch/peer_launch.py",
        "plugins/agent-bridge/src/agent_bridge/_peer_launch.py",
        "plugins/agent-dispatch/src/agent_dispatch/peer_launch.py",
        "plugins/agent-worktrees/src/agent_worktrees/reconcile.py",
        "plugins/agent-worktrees/tests/test_reconcile.py",
        "plugins/agent-worktrees/src/agent_worktrees/installer.py",
        "plugins/agent-worktrees/src/agent_worktrees/installation_cli.py",
        "plugins/agent-worktrees/src/agent_worktrees/deploy_manifest.py",
        "plugins/agent-worktrees/tests/test_installer_command_binding.py",
        "plugins/agent-worktrees/scripts/install.sh",
        "plugins/agent-worktrees/scripts/install.ps1",
        "plugins/agent-worktrees/scripts/command_binding.py",
        "plugins/agent-worktrees/src/agent_worktrees/_command_binding.py",
        "plugins/agent-worktrees/scripts/command_lifecycle.py",
        "plugins/agent-worktrees/src/agent_worktrees/_command_lifecycle.py",
        "plugins/agent-worktrees/scripts/emit-command-catalog.sh",
        "plugins/agent-worktrees/scripts/emit-command-catalog.ps1",
        "plugins/agent-worktrees/payload-invocation.json",
        "libs/payload-invocation/command_binding.py",
        "libs/payload-invocation/tests/test_derived_diagnostic_cli.py",
        "libs/payload-invocation/templates/catalog-posix.tmpl",
        "tools/sync-installation-context.py",
        ".github/workflows/ci.yml",
        ".github/workflows/installation-context-acceptance.yml",
        "tools/installation_context_ci_scope.py",
        "tools/test_installation_context_ci_scope.py",
    ],
)
def test_selects_each_contract_surface(path: str) -> None:
    assert MODULE.needs_installation_context_smoke([path])


@pytest.mark.parametrize("paths", [[], ["docs/architecture.md"], ["plugins/agent-vault/README.md"]])
def test_unrelated_changes_do_not_expand_ci(paths: list[str]) -> None:
    assert not MODULE.needs_installation_context_smoke(paths)


def test_dispatch_selects_smoke_without_git(monkeypatch, capsys) -> None:
    def unexpected_git(*args, **kwargs):
        raise AssertionError("manual dispatch must not need diff metadata")

    monkeypatch.setattr(MODULE.subprocess, "run", unexpected_git)
    assert MODULE.main(["--event", "workflow_dispatch"]) == 0
    assert capsys.readouterr().out == "true\n"


def test_missing_base_selects_smoke_visibly(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        MODULE.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 128, "", "unknown revision\n"),
    )
    assert MODULE.main(["--base", "missing"]) == 0
    output = capsys.readouterr()
    assert output.out == "true\n"
    assert "base revision unavailable" in output.err


def test_diff_error_is_not_reported_as_unrelated(monkeypatch, capsys) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "base\n", ""),
            subprocess.CompletedProcess([], 128, "", "invalid head\n"),
        ]
    )
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *args, **kwargs: next(results))
    assert MODULE.main(["--base", "base", "--head", "missing"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "invalid head" in output.err


def test_native_windows_diagnostic_step_is_scoped_and_bounded() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["test-runner-windows"]
    assert job["timeout-minutes"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.promotion_run == '' && 15 || 7 }}"
    )
    steps = job["steps"]
    detection = next(step for step in steps if step.get("id") == "installation-context")
    smoke = next(
        step for step in steps
        if step.get("name") == "Source provenance and read-only diagnostics (Windows)"
    )
    assert "installation_context_ci_scope.py" in detection["run"]
    assert smoke["if"] == "steps.installation-context.outputs.run == 'true'"
    assert smoke["timeout-minutes"] <= 3
    assert smoke["env"]["INSTALLATION_CONTEXT_POWERSHELL"] == "powershell"
    assert "test_installation_mode_governance.py" in smoke["run"]
    assert "status_and_probe_cli_parity_and_read_only" in smoke["run"]
    assert "diagnostic_api_never_enters_mutation" in smoke["run"]
    assert "active_namespaced_cli_is_visible_and_probe_refuses" in smoke["run"]
    assert "test_diagnostic_consumers.py" in smoke["run"]
    assert "derived_diagnostic_api_read_only" in smoke["run"]
    assert "peer_governance_wrapper_read_only" in smoke["run"]
    assert "libs/installation-context/tests/test_payload_source.py" in smoke["run"]
    assert 'payload_source or installation_context_ci_scope' in smoke["run"]
    assert "libs/payload-invocation/tests/test_command_binding.py" in smoke["run"]
    assert "libs/payload-invocation/tests/test_command_lifecycle.py" in smoke["run"]
    assert "or command_binding" in smoke["run"]
    manager = next(
        step for step in steps if step.get("name") == "Manager read-only wrapper (Windows)"
    )
    assert manager["if"] == smoke["if"]
    assert manager["timeout-minutes"] <= 2
    assert "runtime_inspection_uses_real_read_only_primitive" in manager["env"]["PYTEST_ADDOPTS"]
    assert "run-standalone-tests.py worktree-manager --prepare" in manager["run"]
    assert "--timeout 120" in manager["run"]
    reconciliation = next(
        step for step in steps
        if step.get("name") == "Reconciliation read-only wrapper (Windows)"
    )
    assert reconciliation["if"] == smoke["if"]
    assert reconciliation["timeout-minutes"] <= 2
    assert "run-plugin-tests.py agent-worktrees" in reconciliation["run"]
    assert "--test-file test_reconcile.py --test-file test_installer_command_binding.py" in reconciliation["run"]
    assert '-k "runtime_resolution_real_helper_read_only or command_binding"' in reconciliation["run"]
    assert "or command_binding" in reconciliation["run"]
    assert "--timeout 120" in reconciliation["run"]


@pytest.mark.parametrize("job_name,step_name", [
    ("checks", "Derived native diagnostics (POSIX)"),
    ("test-runner-windows", "Derived native diagnostics (Windows PowerShell 5.1)"),
])
def test_derived_native_diagnostics_select_focused_smoke_in_bounded_lane(
    job_name: str, step_name: str,
) -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    step = next(
        step for step in workflow["jobs"][job_name]["steps"]
        if step.get("name") == step_name
    )
    assert step["if"] == "steps.installation-context.outputs.run == 'true'"
    assert step["timeout-minutes"] == 2
    assert "libs/payload-invocation/tests/test_derived_diagnostic_cli.py" in step["run"]
    assert '-m "not derived_diagnostic_full"' in step["run"]
    if job_name == "test-runner-windows":
        assert step["env"]["INSTALLATION_CONTEXT_POWERSHELL"] == "powershell"


def test_full_derived_native_acceptance_has_explicit_bounded_matrix() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    assert workflow.get("on", workflow.get(True)) == {"workflow_dispatch": None}
    routes = {
        "derived-diagnostics-posix": "ubuntu-latest",
        "derived-diagnostics-windows": "windows-latest",
    }
    assert set(workflow["jobs"]) == set(routes)
    for name, runner in routes.items():
        job = workflow["jobs"][name]
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 4
        matrix = job["strategy"]["matrix"]
        assert set(matrix) == {"mode", "pointer"}
        assert set(matrix["mode"]) == {"mode_legacy", "mode_active"}
        assert set(matrix["pointer"]) == {"pointer_absent", "pointer_retained"}
        assert job["strategy"]["fail-fast"] is False
        step = next(step for step in job["steps"] if step.get("name") == "All derived native copies")
        assert "test_derived_diagnostic_cli.py" in step["run"]
        assert '-k "${{ matrix.mode }} and ${{ matrix.pointer }}"' in step["run"]
        assert "-m " not in step["run"].rsplit("pytest", 1)[1]
        if runner == "windows-latest":
            assert step["env"]["INSTALLATION_CONTEXT_POWERSHELL"] == "powershell"


def test_foundation_lane_leaves_new_native_matrix_to_its_scoped_step() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    foundation = next(
        step for step in workflow["jobs"]["checks"]["steps"]
        if step.get("name") == "Payload invocation foundation tests"
    )
    assert "--ignore=libs/payload-invocation/tests/test_derived_diagnostic_cli.py" in foundation["run"]
    assert "libs/payload-invocation/tests" in foundation["run"]
    assert "plugins/agent-index/tests/test_command_catalog.py" in foundation["run"]


@pytest.mark.parametrize("mode", ["mode_legacy", "mode_active"])
@pytest.mark.parametrize("pointer", ["pointer_absent", "pointer_retained"])
def test_full_native_matrix_collects_exact_inventory_partition(
    mode: str, pointer: str,
) -> None:
    root = HERE.parent
    spec = importlib.util.spec_from_file_location(
        "native_partition_inventory", HERE / "sync-installation-context.py",
    )
    assert spec and spec.loader
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    script = "installation-context.ps1" if os.name == "nt" else "installation-context.sh"
    expected = {
        destination.relative_to(root).as_posix()
        for source, destination in inventory.vendor_pairs()
        if source.name == script
    }
    environment = {**os.environ, "PYTEST_ADDOPTS": "", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q", "--color=no",
            "-p", "no:cacheprovider",
            "libs/payload-invocation/tests/test_derived_diagnostic_cli.py",
            "-k", f"{mode} and {pointer}",
        ],
        cwd=root, env=environment, capture_output=True, text=True, check=False,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = [
        line for line in result.stdout.splitlines()
        if "::test_derived_native_diagnostic_cli_read_only[" in line
    ]
    assert len(selected) == len(expected), result.stdout
    assert {
        line.split(f"[{pointer}-{mode}-", 1)[1].removesuffix("]")
        for line in selected
    } == expected, result.stdout
