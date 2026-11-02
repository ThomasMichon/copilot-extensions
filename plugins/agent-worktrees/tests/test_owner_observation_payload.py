"""Actual dispatcher classification and missing-runtime gates, with inert resolvers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from native_shell_support import run_headless

PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
INVOCATIONS = [
    ["worktree-observations"],
    ["worktree-observation-history"],
    ["--project", "project", "worktree-observations"],
    ["-p", "project", "worktree-observation-history"],
    ["--project=project", "worktrees", "worktree-observations"],
    ["worktrees", "--project", "project", "worktree-observation-history"],
    ["worktree", "--project=project", "worktree-observations"],
    ["-p", "project", "worktree", "worktree-observation-history"],
]
ORDINARY = [
    ["init", "--project", "worktree-observations"],
    ["--project", "worktree-observation-history", "init"],
]


def posix_harness() -> str:
    source = (PLUGIN / "scripts/invoke-payload-runtime.sh").read_text(encoding="utf-8")
    classification = source[
        source.index("_aw_passive_observation=0") : source.index('\nMODE_RUNNER="')
    ]
    start = source.index("\nresolve_runtime\n") + 1
    end = source.index(
        "\nprintf '[agent-worktrees] runtime not provisioned -- provisioning", start
    )
    return (
        "set -euo pipefail\nunset AGENT_WORKTREES_NO_SELFPROVISION\n"
        + classification
        + "\nresolve_runtime() { AGENT_RT_PY=''; }\n"
        "assert_plugin_runtime_publication() { printf 'PUBLICATION\\n'; }\n"
        "boot_trace() { :; }\nboot_trace_log() { :; }\n"
        "run_runtime() { printf 'RUNTIME\\n'; }\n"
        + source[start:end]
        + "\nprintf 'INSTALLER\\n'\n"
    )


@pytest.mark.parametrize("argv", INVOCATIONS)
def test_posix_observation_reads_cannot_publish_or_provision(
    tmp_path: Path, native_bash: str, argv: list[str]
) -> None:
    env = dict(os.environ, HOME=str(tmp_path))
    env.pop("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", None)
    result = run_headless(
        [native_bash, "-c", posix_harness(), "dispatcher-test", *argv],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 1
    assert "existing runtime" in result.stderr and "self-provisioning is disabled" in result.stderr
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("argv", ORDINARY)
def test_posix_project_value_is_not_mistaken_for_passive_command(
    native_bash: str, argv: list[str]
) -> None:
    result = run_headless(
        [native_bash, "-c", posix_harness(), "dispatcher-test", *argv],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["PUBLICATION", "INSTALLER"]


def powershell_harness() -> str:
    source = (PLUGIN / "scripts/invoke-payload-runtime.ps1").read_text(encoding="utf-8")
    classification = source[
        source.index("$passiveObservation = $false") : source.index("\n$scriptDir =")
    ]
    start = source.index("\n$python = Resolve-AgentWorktreesRuntime\n") + 1
    end = source.index(
        "\n[Console]::Error.WriteLine(\n    '[agent-worktrees] runtime not provisioned -- provisioning",
        start,
    )
    return (
        "$ErrorActionPreference='Stop'\n$forwardArgs=@($args)\n"
        "$env:AGENT_WORKTREES_NO_SELFPROVISION=$null\n"
        "$env:COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS=$null\n"
        "$_awUpdateDiagnostic=$false\n"
        "function Resolve-AgentWorktreesRuntime { return $null }\n"
        "function Assert-PluginRuntimePublication { 'PUBLICATION' }\n"
        "function Write-BootTrace {}\nfunction Write-BootTraceRecord {}\n"
        "function Invoke-AgentWorktreesRuntime { 'RUNTIME' }\n"
        + classification
        + "\n"
        + source[start:end]
        + "\n'INSTALLER'\n"
    )


@pytest.mark.parametrize("argv", INVOCATIONS)
def test_powershell_observation_reads_cannot_publish_or_provision(
    tmp_path: Path, argv: list[str]
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell unavailable; no Windows-runtime qualification claimed")
    harness = tmp_path / "passive.ps1"
    harness.write_text(powershell_harness(), encoding="utf-8")
    result = run_headless(
        [pwsh, "-NoProfile", "-File", str(harness), *argv],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "existing runtime" in result.stderr and "self-provisioning is disabled" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("argv", ORDINARY)
def test_powershell_project_value_is_not_mistaken_for_passive_command(
    tmp_path: Path, argv: list[str]
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell unavailable; no Windows-runtime qualification claimed")
    harness = tmp_path / "ordinary.ps1"
    harness.write_text(powershell_harness(), encoding="utf-8")
    result = run_headless(
        [pwsh, "-NoProfile", "-File", str(harness), *argv],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["PUBLICATION", "INSTALLER"]
