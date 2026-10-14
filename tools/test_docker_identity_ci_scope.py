"""Scope, execution and minimum-runtime contract for the fixture-only lane."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("docker_identity_ci_scope", HERE / "docker_identity_ci_scope.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("path", [
    "libs/docker-resource-producer/src/docker_resource_producer/models.py",
    "libs/docker-resource-producer/tests/test_identity.py",
    "libs/docker-resource-producer/pyproject.toml",
    "tools/docker_identity_ci_scope.py", "tools/test_docker_identity_ci_scope.py",
    ".github/workflows/ci.yml",
])
def test_selects_contract_surface(path: str) -> None:
    assert MODULE.needs_docker_identity_smoke([path])


@pytest.mark.parametrize("paths", [[], ["docs/architecture.md"], ["plugins/agent-mcp/README.md"]])
def test_unrelated_changes_do_not_select_fixture_processes(paths: list[str]) -> None:
    assert not MODULE.needs_docker_identity_smoke(paths)


def test_manual_dispatch_does_not_need_git(monkeypatch, capsys) -> None:
    monkeypatch.setattr(MODULE, "_git", lambda *a: pytest.fail("unexpected Git"))
    assert MODULE.main(["--event", "workflow_dispatch"]) == 0
    assert capsys.readouterr().out == "true\n"


def test_unavailable_base_expands_scope_visibly(monkeypatch, capsys) -> None:
    monkeypatch.setattr(MODULE, "_git", lambda *a: subprocess.CompletedProcess([], 128, "", "missing\n"))
    assert MODULE.main(["--base", "missing"]) == 0
    output = capsys.readouterr()
    assert output.out == "true\n" and "base revision unavailable" in output.err


def test_diff_error_is_not_a_successful_unrelated_verdict(monkeypatch, capsys) -> None:
    results = iter([subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 128, "", "invalid head\n")])
    monkeypatch.setattr(MODULE, "_git", lambda *a: next(results))
    assert MODULE.main(["--base", "base", "--head", "bad"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "invalid head" in output.err


def test_workflow_runs_scoped_minimum_without_changing_guard_python() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["checks"]["steps"]
    scope = next(s for s in steps if s.get("id") == "docker-identity")
    runtime = next(s for s in steps if s.get("id") == "docker-identity-python")
    smoke = next(s for s in steps if s.get("name") == "Docker identity fixture contracts (Python 3.10)")
    assert "docker_identity_ci_scope.py" in scope["run"]
    assert scope["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert runtime["if"] == smoke["if"] == "steps.docker-identity.outputs.run == 'true'"
    assert runtime["with"]["python-version"] == "3.10"
    assert runtime["with"]["update-environment"] is False
    assert smoke["timeout-minutes"] <= 3
    assert "steps.docker-identity-python.outputs.python-path" in smoke["run"]
    assert "libs/docker-resource-producer/tests" in smoke["run"]
    assert "--timeout=20" in smoke["run"]
