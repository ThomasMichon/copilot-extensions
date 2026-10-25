"""Coverage selection for canonical and derived installation diagnostics."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
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
    assert "tools/ci_pr_unit_reuse.py" in execution["run"]
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


@pytest.mark.parametrize("platform,runner", [
    ("linux", "ubuntu-latest"), ("windows", "windows-latest"),
])
def test_candidate_profiles_remain_focused_opt_in_native_acceptance(platform, runner) -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["workflow_dispatch"]["inputs"]["candidate_profiles"]["default"] is False
    name = f"candidate-profiles-{platform}"
    job = workflow["jobs"][name]
    assert job["if"] == "${{ inputs.candidate_profiles }}"
    assert job["runs-on"] == runner
    assert job["timeout-minutes"] == 15
    assert "strategy" not in job
    execution = job["steps"][-1]
    assert "--test-file tests/test_python_installer_engine.py" in execution["run"]
    for selector in ("local_role", "local_package", "role_health", "selected_extras"):
        assert selector in execution["run"]
    assert not execution.get("continue-on-error", False)
    for diagnostics in ("derived-diagnostics-posix", "derived-diagnostics-windows"):
        assert "!inputs.candidate_profiles" in workflow["jobs"][diagnostics]["if"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert name not in required["jobs"]


@pytest.mark.parametrize(("plugin", "test_file", "selector"), [
    ("bridge", "test_bridge_first_use_publication.py",
     ["-k", "test_bridge_cell_stamp_preserves_global_ownership"]),
    ("dispatch", "test_dispatch_scoped_publication.py", []),
])
def test_scoped_binding_remains_an_explicit_bounded_native_portfolio(
    plugin: str, test_file: str, selector: list[str],
) -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    portfolio = f"{plugin}-binding"
    assert portfolio in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        name = f"{portfolio}-{platform}"
        job = workflow["jobs"][name]
        assert job["if"] == (
            f"github.event_name == 'workflow_dispatch' && inputs.portfolio == '{portfolio}'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 10
        assert "strategy" not in job
        executions = [step["run"].split() for step in job["steps"] if "run" in step]
        assert executions == [[
            "python", "tools/run-plugin-tests.py", "payload-invocation",
            "--test-file", f"tests/{test_file}", *selector,
            "--allow-explicit-tiers", "--test-timeout", "60", "--timeout", "540",
        ]]
        assert name not in required["jobs"]


def test_pr_sweep_is_a_focused_manual_portfolio_not_required_ci() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "pr-sweep" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    assert trigger["workflow_dispatch"]["inputs"]["portfolio"]["default"] == "context"
    job = workflow["jobs"]["pr-sweep-linux"]
    assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'pr-sweep'"
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 10
    assert "strategy" not in job and job["env"]["UV_PYTHON"] == "3.12"
    execution = next(step for step in job["steps"] if "run" in step)
    assert execution["run"].count("--test-file") == 6
    assert "--test-file tests/test_pr_sweep_process.py" in execution["run"]
    assert "--test-timeout 60 --timeout 540" in execution["run"]
    assert not execution.get("continue-on-error", False)
    guard = next(step for step in job["steps"] if step.get("name", "").startswith("Qualify publication"))
    assert guard["run"].split() == [
        "timeout", "--signal=TERM", "--kill-after=10s", "120s",
        "python", "-m", "pytest", "-q", "tools/test_check_large_files.py",
        "tools/test_check_no_internal_identifiers.py",
    ]
    assert not guard.get("continue-on-error", False)
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert "pr-sweep-linux" not in required["jobs"]


def test_codespaces_bootstrap_remains_an_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "codespaces-bootstrap" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner, admission in (
        ("linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        name = f"codespaces-bootstrap-{platform}"
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'codespaces-bootstrap'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][admission] == "1" and "strategy" not in job
        assert name not in required["jobs"]
        executions = [step["run"].split() for step in job["steps"] if "run" in step]
        assert executions == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_codespaces_bootstrap.py",
             "--test-file", "tests/test_first_use_codespaces.py",
             "--test-file", "tests/test_first_use_codespaces_checkout.py",
             "--test-timeout", "900", "--timeout", "1200"],
            ["python", "tools/run-plugin-tests.py", "agent-codespaces",
             "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-file", "tests/test_bridge_provider_manifest.py",
             "--test-file", "tests/test_write_session_guidance.py",
             "--test-timeout", "60", "--timeout", "240"],
        ]


def test_source_profile_acceptance_uses_existing_opt_in_and_unchanged_native_budget() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    for platform, runner in (
        ("linux", "ubuntu-latest"), ("windows", "windows-latest"), ("macos", "macos-latest"),
    ):
        job = workflow["jobs"][f"source-profiles-{platform}"]
        assert job["if"] == "${{ inputs.wheel_inputs }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 6
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert commands == [
            "python tools/seasoned_uv.py --uv uv --registry-only -- --system pytest packaging pyyaml",
            "python -m pytest -q tools/test_runtime_source_wheels.py",
        ]


def test_full_derived_native_acceptance_has_explicit_bounded_matrix() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "watch_owner", "dispatch_delegation", "pr_service", "ssh_service", "pr_artifacts", "dependency_compile",
        "candidate_profiles", "ssh_snapshot", "wheel_inputs",
        "guardian_followthrough", "guardian_ref",
        "runtime_profiles", "chat_construction",
        "immutable_runtime", "immutable_runtime_ref",
        "interactive_completion", "ssh_service",
    }
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
        "ssh-service-linux", "ssh-service-windows",
        "pr-artifacts-linux", "pr-artifacts-windows",
        "dependency-compile-linux", "dependency-compile-windows",
        "candidate-profiles-linux", "candidate-profiles-windows",
        "chat-construction-linux", "chat-construction-windows",
        "ssh-snapshot-native-linux", "ssh-snapshot-native-windows",
        "wheel-inputs-linux", "wheel-inputs-windows",
        "source-profiles-linux", "source-profiles-windows",
        "source-profiles-macos",
        "guardian-followthrough-macos",
        "runtime-profile-policy-linux", "runtime-profile-policy-windows",
        "immutable-runtime-linux", "immutable-runtime-macos",
        "interactive-completion-linux", "interactive-completion-macos",
        "interactive-completion-windows",
    }
    for name, runner in routes.items():
        job = workflow["jobs"][name]
        assert job["if"] == (
            "${{ !inputs.interactive_completion && !inputs.immutable_runtime "
            "&& !inputs.guardian_followthrough && !inputs.watch_owner "
            "&& !inputs.dispatch_delegation && !inputs.pr_service && !inputs.ssh_service "
            "&& !inputs.pr_artifacts && !inputs.dependency_compile && !inputs.candidate_profiles "
            "&& !inputs.chat_construction && !inputs.ssh_snapshot && !inputs.wheel_inputs "
            "&& !inputs.runtime_profiles }}"
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


def test_foundation_snapshot_staging_is_disposable_and_bounded() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    for name, runner in (
        ("foundation-linux", "ubuntu-latest"), ("foundation-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'foundation'"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        step = next(
            entry for entry in job["steps"]
            if entry.get("name") == "Qualify selected snapshot staging and ordinary-copy refusal"
        )
        assert step["env"] == {"APR_NATIVE_OS_ACCOUNT_ACCEPTANCE": "1"}
        assert step["run"].split() == [
            "python", "tools/run-plugin-tests.py", "agent-pull-requests",
            "--allow-explicit-tiers", "--allow-host-state",
            "--test-file", "tests/test_cell_invocation.py",
            "-k", '"native_staged_snapshot', "or", "legacy_staging_reentry", "or", 'frozen_input"',
            "--test-timeout", "60", "--timeout", "300",
        ]


def test_pull_requests_command_adoption_uses_explicit_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "pull-requests-commands" in (
        trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    )
    assert workflow["jobs"]["exhaustive"]["if"] == (
        "github.event_name != 'workflow_dispatch' || inputs.portfolio == 'context'"
    )
    for name, runner in (
        ("pull-requests-command-linux", "ubuntu-latest"),
        ("pull-requests-command-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'pull-requests-commands'"
        )
        assert job["timeout-minutes"] == 25
        assert job["runs-on"] == runner and not job.get("strategy")
        steps = [step["run"] for step in job["steps"] if "run" in step]
        assert len(steps) == 2
        assert steps[0].split() == [
            "python", "tools/run-plugin-tests.py", "update-driver",
            "--test-file", "tests/test_first_use_pull_requests_command.py",
            "--test-timeout", "900", "--timeout", "1080",
        ]
        assert steps[1].split() == [
            "python", "tools/run-plugin-tests.py", "agent-pull-requests",
            "--test-file", "tests/test_cell_invocation.py",
            "-k", '"dispatch_preserves', "or", 'command_cannot"',
            "--test-timeout", "60", "--timeout", "180",
        ]
        assert not job.get("continue-on-error")


def test_logger_command_adoption_uses_explicit_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "logger-commands" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner, opt_in in (
        ("linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        name = f"logger-commands-{platform}"
        job = workflow["jobs"][name]
        assert name not in required["jobs"] and not job.get("strategy")
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'logger-commands'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 35
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        execution = next(step for step in job["steps"] if step.get("name") == (
            "Qualify real command admission, caller loss and installed consumers"
        ))
        assert execution["env"]["PYTEST_ADDOPTS"] == "--capture=tee-sys"
        assert [shlex.split(step["run"]) for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_logger_command.py",
             "--test-timeout", "900", "--timeout", "1800"],
            ["python", "tools/run-plugin-tests.py", "agent-logger",
             "--test-file", "tests/test_runtime_gate.py",
             "--test-file", "tests/test_install_binstub.py",
             "--max-files-per-sub-suite", "1", "--test-timeout", "120", "--timeout", "600"],
        ]


def test_codespaces_recipes_remain_manual_and_bounded() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "codespaces-recipes" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("codespaces-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("codespaces-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'codespaces-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_codespaces.py",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_genesis.py", "-k", "owning_codespaces",
             "--test-timeout", "900", "--timeout", "1080"],
        ]


def test_logger_selected_snapshots_remain_manual_with_bounded_cleanup() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    for name, runner, opt_in in (
        ("logger-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("logger-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'logger-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        assert [shlex.split(step["run"]) for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_logger.py",
             "--test-file", "tests/test_first_use_logger_snapshot.py",
             "--max-files-per-sub-suite", "1", "--test-timeout", "900", "--timeout", "1200"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_genesis.py", "-k", "owning_logger or selected_logger_snapshot",
             "--test-timeout", "900", "--timeout", "1080"],
        ]


def test_ssh_recipes_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "ssh-recipes" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("ssh-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("ssh-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'ssh-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        commands = [step["run"].split() for step in job["steps"] if "run" in step]
        assert commands == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_ssh.py",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_genesis.py", "-k", "owning_ssh",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-timeout", "60", "--timeout", "240"],
        ]


def test_bridge_recipes_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "bridge-recipes" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("bridge-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("bridge-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'bridge-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 35
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"not', 'native_caller_loss"',
             "--test-timeout", "900", "--timeout", "900"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"native_caller_loss', "and", 'provision"',
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"native_caller_loss', "and", 'install"',
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "agent-bridge",
             "--test-file", "tests/test_install_ps1_s4u_recovery.py",
             "--test-file", "tests/test_install_lifecycle_emit.py",
             "--test-timeout", "60", "--timeout", "180"],
        ]


def test_ssh_commands_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "ssh-commands" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("ssh-commands-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("ssh-commands-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'ssh-commands'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_ssh_command.py",
             "--test-timeout", "900", "--timeout", "1200"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_payload_invocation.py",
             "--test-timeout", "60", "--timeout", "180"],
        ]


def test_ssh_bootstrap_uses_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "ssh-bootstrap" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("ssh-bootstrap-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("ssh-bootstrap-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'ssh-bootstrap'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_ssh_bootstrap.py",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-file", "tests/test_payload_invocation.py", "-k", '"bootstrap"',
             "--test-timeout", "60", "--timeout", "180"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_install_snapshot.py", "-k", "checkout_stamp",
             "--test-timeout", "120", "--timeout", "240"],
        ]


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
            "--test-file", "tests/test_watch_replay_store.py",
            "--test-file", "tests/test_watch_service.py",
            "--test-file", "tests/test_watch_gateway.py",
            "--test-file", "tests/test_watch_gateway_transport.py",
            "--test-file", "tests/test_watch_observer.py",
            "--test-file", "tests/test_watch_replay_client.py",
            "--test-file", "tests/test_watch_replay_binding.py",
            "--test-file", "tests/test_watch_replay_client_transport.py",
            "--max-files-per-sub-suite", "2",
        ]
        assert commands[1]["run"].split() == [
            "python", "tools/run-standalone-tests.py", "services/pr",
            "--prepare", "--timeout", "180", "--admission-wait", "30",
        ]


def test_compile_acceptance_is_bounded_native_and_independently_selected() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["dependency_compile"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (
        ("dependency-compile-linux", "ubuntu-latest"),
        ("dependency-compile-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.dependency_compile }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 6 and not job.get("continue-on-error")
        assert not job.get("strategy")
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert commands == [
            "python tools/seasoned_uv.py --uv uv --registry-only -- --system pytest pyyaml",
            'python -m pytest -q tools/test_installer_dependency_compile.py '
            'tools/test_installer_dependency_policy.py '
            'tools/test_runtime_source_lock.py '
            '-k "compile or real_uv_registry_transitive_and_isolated_build_age or runtime_source_lock"',
        ]


def test_wheel_acquisition_reuses_the_existing_bounded_opt_in_lane() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["wheel_inputs"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (("wheel-inputs-linux", "ubuntu-latest"), ("wheel-inputs-windows", "windows-latest")):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.wheel_inputs }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 6 and not job.get("continue-on-error")
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert len(commands) == 2 and "pytest packaging pyyaml" in commands[0]
        selection = commands[1].split()
        assert selection[:4] == ["python", "-m", "pytest", "-q"]
        assert "-k" not in selection
        for path in (
            "tools/test_installer_wheel_acquisition.py", "tools/test_runtime_feed_configuration.py",
            "tools/test_installer_closed_wheels.py", "tools/test_runtime_profile_release.py",
        ):
            assert path in selection


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
            "--test-file", "tests/test_delegated_replay_binding.py",
            "--test-file", "tests/test_delegated_replay_transport.py",
            "--test-file", "tests/test_delegated_replay_credentials.py",
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


@pytest.fixture(scope="module")
def native_partition_collections() -> dict[str, list[str]]:
    # Reuse only interpreter imports, not collection results or selector logic.
    script = """
import contextlib, io, json, sys, pytest
class Collected:
    def pytest_collection_finish(self, session):
        self.nodeids = [item.nodeid for item in session.items]
results = {}
for pointer in ("pointer_absent", "pointer_retained"):
    for mode in ("mode_legacy", "mode_active"):
        collected = Collected()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = pytest.main([
                "--collect-only", "-q", "--color=no", "-p", "no:cacheprovider",
                "libs/payload-invocation/tests/test_derived_diagnostic_cli.py",
                "-k", mode + " and " + pointer,
            ], plugins=[collected])
        if status:
            print(output.getvalue(), file=sys.stderr)
            raise SystemExit(status)
        results[pointer + ":" + mode] = collected.nodeids
print(json.dumps(results))
"""
    environment = {**os.environ, "PYTEST_ADDOPTS": "", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=HERE.parent, env=environment, capture_output=True, text=True, check=False,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["mode_legacy", "mode_active"])
@pytest.mark.parametrize("pointer", ["pointer_absent", "pointer_retained"])
def test_full_native_matrix_collects_exact_inventory_partition(
    mode: str, pointer: str, native_partition_collections: dict[str, list[str]],
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
    selected = [
        line for line in native_partition_collections[f"{pointer}:{mode}"]
        if "::test_derived_native_diagnostic_cli_read_only[" in line
    ]
    assert len(selected) == len(expected), selected
    assert {
        line.split(f"[{pointer}-{mode}-", 1)[1].removesuffix("]")
        for line in selected
    } == expected, selected
