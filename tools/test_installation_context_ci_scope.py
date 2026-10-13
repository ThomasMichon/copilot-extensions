"""Coverage selection for canonical and derived installation diagnostics."""

from __future__ import annotations

import importlib.util
import subprocess
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
        "tools/sync-installation-context.py",
        ".github/workflows/ci.yml",
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
    assert job["timeout-minutes"] == 5
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
    assert "-k runtime_resolution_real_helper_read_only" in reconciliation["run"]
    assert "--timeout 120" in reconciliation["run"]
