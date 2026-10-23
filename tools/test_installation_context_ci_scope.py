"""Coverage selection for canonical and derived installation diagnostics."""

from __future__ import annotations

import importlib.util
import os
import shutil
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


@pytest.mark.parametrize("retired_job", [
    "installation-context-windows", "command-lifecycle-windows",
    "vault-snapshot-windows", "fleet-native-windows",
])
def test_native_jobs_do_not_reenter_required_unit_only_ci(retired_job: str) -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert retired_job not in workflow["jobs"]
    assert set(workflow["jobs"]["pr-gate"]["needs"]) == {"checks", "unit-tests"}
    assert workflow["env"]["PROMOTION_UNIT_PROFILE"] == "copilot-extensions.promotion-units/v1"
    units = workflow["jobs"]["unit-tests"]
    execution = next(step for step in units["steps"] if step.get("id") == "units")
    assert execution["if"] == "steps.impact.outputs.run == 'true'"
    assert "tools/run-promotion-unit-tests.py" in execution["run"]
    assert not execution.get("continue-on-error", False)


def test_displaced_native_sources_and_unavailable_portfolio_remain_explicit() -> None:
    root = HERE.parent
    for source in (
        "libs/installation-context/tests/test_installation_mode_governance.py",
        "libs/installation-context/tests/test_diagnostic_consumers.py",
        "libs/payload-invocation/tests/test_command_binding.py",
        "libs/payload-invocation/tests/test_command_lifecycle.py",
        "plugins/agent-vault/tests/test_install_snapshot_ps1.py",
    ):
        assert (root / source).is_file()
    inventory = yaml.safe_load((HERE / "periodic-integration-inventory.json").read_text())
    assert inventory["required_pr_dependency"] is False
    assert inventory["release_dependency"] is False
    assert inventory["failure_owner"]["tracker"]
    windows = inventory["existing_windows_rotation"]
    assert windows["status"] == "unavailable"
    assert windows["execution_enabled"] is False
    assert windows["owner"]
    periodic = yaml.safe_load((root / inventory["workflow"]).read_text())
    assert periodic["jobs"][windows["job"]]["if"] == "${{ false }}"
    trigger = periodic.get("on", periodic.get(True))
    assert set(trigger) == {"schedule", "workflow_dispatch"}


@pytest.mark.parametrize("fail_on", ["tools/seasoned_uv.py", "-m"])
def test_windows_step_does_not_mask_native_admission_failure(
    tmp_path, fail_on,
) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    step = next(
        step for step in workflow["jobs"]["derived-diagnostics-windows"]["steps"]
        if step.get("name") == "All derived native copies"
    )
    body = step["run"].replace("${{ matrix.mode }}", "fixture").replace(
        "${{ matrix.pointer }}", "fixture",
    )
    assert "${{" not in body
    log = tmp_path / "commands"
    prefix = """
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
function python {
    $code = if ($args[0] -eq $env:FIXTURE_FAIL_ON) { 23 } else { 0 }
    & $env:FIXTURE_PYTHON -I -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.open("a").write(sys.argv[2]+"\\n"); raise SystemExit(int(sys.argv[3]))' $env:FIXTURE_COMMAND_LOG $args[0] $code
    $global:LASTEXITCODE = $LASTEXITCODE
}
"""
    command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + body]
    result = subprocess.run(
        command,
        env={
            **os.environ, "FIXTURE_PYTHON": sys.executable.replace("\\", "/"),
            "FIXTURE_COMMAND_LOG": str(log), "FIXTURE_FAIL_ON": fail_on,
        },
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert log.read_text().splitlines() == (
        ["tools/seasoned_uv.py", "-m"] if fail_on == "-m" else ["tools/seasoned_uv.py"]
    )


def test_derived_native_diagnostics_are_not_a_required_smoke_fallback() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    commands = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values() for step in job.get("steps", [])
    )
    assert "test_derived_diagnostic_cli.py" not in commands
    assert "tools/run-plugin-tests.py" not in commands
    assert "tools/run-standalone-tests.py" not in commands


def test_full_derived_native_acceptance_has_explicit_bounded_matrix() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"watch_owner", "dispatch_delegation", "pr_service"}
    assert inputs["watch_owner"]["type"] == "boolean"
    assert inputs["watch_owner"]["default"] is False
    routes = {
        "derived-diagnostics-posix": "ubuntu-latest",
        "derived-diagnostics-windows": "windows-latest",
    }
    assert set(workflow["jobs"]) == set(routes) | {
        "watch-owner-native-linux", "watch-owner-native-windows",
        "watch-owner-macos-refusal",
        "watch-owner-boundaries-linux", "watch-owner-boundaries-windows",
        "dispatch-delegation-linux", "dispatch-delegation-windows",
        "pr-service-linux", "pr-service-windows",
    }
    for name, runner in routes.items():
        job = workflow["jobs"][name]
        assert job["if"] == (
            "${{ !inputs.watch_owner && !inputs.dispatch_delegation && !inputs.pr_service }}"
        )
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


def test_pr_service_acceptance_is_bounded_and_independently_selected() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["pr_service"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (
        ("pr-service-linux", "ubuntu-latest"),
        ("pr-service-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.pr_service }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 10
        assert not job.get("strategy") and not job.get("continue-on-error")
        commands = [step for step in job["steps"] if "run" in step]
        assert len(commands) == 2 and not any(step.get("env") for step in commands)
        assert commands[0]["run"].split() == [
            "python", "tools/run-plugin-tests.py", "agent-pull-requests",
            "--test-file", "tests/test_watch_service.py",
            "--test-file", "tests/test_watch_gateway.py",
            "--test-file", "tests/test_watch_gateway_transport.py",
            "--test-file", "tests/test_watch_observer.py",
            "--max-files-per-sub-suite", "2",
        ]
        assert commands[1]["run"].split() == [
            "python", "tools/run-standalone-tests.py", "services/pr",
            "--prepare", "--timeout", "180", "--admission-wait", "30",
        ]


def test_watch_owner_acceptance_is_opt_in_native_and_separately_contained() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    expected_scenarios = {
        "normal-upgrade": (
            "test_normal_payload_init_refreshes_receipt or legacy_staging "
            "or bootstrap_admission or session_start_never_runs_legacy_reconciler"
        ),
        "two-shipped-cells": "test_two_normal_shipped_cells",
        "peer-roundtrip": "test_normal_payload_peer_roundtrip",
        "lease-only-endpoint-live": "native_account_lease_only_owner and endpoint_live",
        "lease-only-endpoint-missing": "native_account_lease_only_owner and endpoint_missing",
    }
    routes = (
        ("watch-owner-native-linux", "ubuntu-latest", True),
        ("watch-owner-native-windows", "windows-latest", True),
        ("watch-owner-boundaries-linux", "ubuntu-latest", False),
        ("watch-owner-boundaries-windows", "windows-latest", False),
    )
    for name, runner, native in routes:
        job = jobs[name]
        assert job["if"] == "${{ inputs.watch_owner }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 35
        assert job["strategy"]["fail-fast"] is False
        matrix = job["strategy"]["matrix"]
        assert set(matrix) == {"scenario"}
        commands = [step for step in job["steps"] if "run" in step]
        assert len(commands) == 1
        command = commands[0]
        assert "python tools/run-plugin-tests.py agent-pull-requests" in command["run"]
        assert "--allow-explicit-tiers" in command["run"]
        assert "--timeout 1200 --plugin-timeout 1800" in command["run"]
        assert '-k "${{ matrix.scenario.selector }}"' in command["run"]
        if native:
            assert command["env"] == {"APR_NATIVE_OS_ACCOUNT_ACCEPTANCE": "1"}
            assert "--allow-host-state" in command["run"]
            assert {
                case["name"]: case["selector"] for case in matrix["scenario"]
            } == expected_scenarios
        else:
            assert "--allow-host-state" not in command["run"]
            assert not command.get("env")
            assert {case["name"] for case in matrix["scenario"]} == {
                "controller-death", "concurrent-upgrade",
            }
            selectors = " ".join(case["selector"] for case in matrix["scenario"])
            for boundary in (
                "actual_controller_death_recovers", "bound_passive_endpoint_recovery",
                "completed_receipt_parallel_reads_keep_native_identity",
                "actual_installer_upgrade_drains_handlers_poll_ack",
            ):
                assert boundary in selectors
    macos = jobs["watch-owner-macos-refusal"]
    assert macos["if"] == "${{ inputs.watch_owner }}"
    assert macos["runs-on"] == "macos-latest"
    assert macos["timeout-minutes"] == 10
    commands = [step for step in macos["steps"] if "run" in step]
    assert len(commands) == 1
    assert "--allow-host-state" not in commands[0]["run"]
    assert "--allow-explicit-tiers --timeout 300 --plugin-timeout 600" in commands[0]["run"]
    assert "-k native_macos_governed_watch_refuses_unprovable_identity" in commands[0]["run"]


def test_dispatch_delegation_acceptance_is_bounded_and_independently_selected() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["dispatch_delegation"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (
        ("dispatch-delegation-linux", "ubuntu-latest"),
        ("dispatch-delegation-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.dispatch_delegation }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 10
        assert not job.get("strategy") and not job.get("continue-on-error")
        commands = [step for step in job["steps"] if "run" in step]
        assert len(commands) == 1 and not commands[0].get("env")
        assert commands[0]["run"].split() == [
            "python", "tools/run-plugin-tests.py", "agent-dispatch",
            "--test-file", "tests/test_delegated_waiter_cli.py",
            "--test-file", "tests/test_delegated_waiter_context.py",
            "--test-file", "tests/test_delegated_waiter_fences.py",
            "--test-file", "tests/test_delegated_waiter_owner_contract.py",
            "--test-file", "tests/test_delegated_replay_consumer.py",
            "--test-file", "tests/test_delegated_replay_transport.py",
            "--test-file", "tests/test_waiter_maintenance.py",
            "--max-files-per-sub-suite", "2",
        ]


def test_required_foundation_checks_generation_without_running_native_test_corpus() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    generation = next(
        step for step in workflow["jobs"]["checks"]["steps"]
        if step.get("name") == "Payload invocation generated files"
    )
    assert generation["run"].split() == [
        "python", "libs/payload-invocation/generate.py", "--all", "--check",
    ]
    commands = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["checks"]["steps"]
    )
    assert "libs/payload-invocation/tests" not in commands
    assert "plugins/agent-index/tests/test_command_catalog.py" not in commands


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
