"""Required unit-workflow and test-only worker dependency contracts."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("ci.yml", "validate-and-promote.yml")
UNIT_JOBS = {"ci.yml": "unit-tests", "validate-and-promote.yml": "source-units"}
UNIT_ENTRYPOINTS = {
    "ci.yml": "ci_pr_unit_reuse.py",
    "validate-and-promote.yml": "run-promotion-unit-tests.py",
}


def _workflow(name: str) -> dict[str, Any]:
    value = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _has_worker_option(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"-n\s*=?\s*\d|--numprocesses", value))


def assert_unit_step_preserves_coverage_and_failure_gates(workflow: str) -> None:
    job = _workflow(workflow)["jobs"][UNIT_JOBS[workflow]]
    steps = [
        step for step in job["steps"]
        if UNIT_ENTRYPOINTS[workflow] in step.get("run", "")
    ]
    assert len(steps) == 1
    step = steps[0]
    assert "--source-sha" in step["run"] and "--output-dir" in step["run"]
    assert not any(option in step["run"].split() for option in ("-k", "-m", "--guards", "-n"))
    assert any("coverage" in item.get("run", "") for item in job["steps"])
    assert job["runs-on"] == "ubuntu-latest"
    assert 0 < job["timeout-minutes"] <= 20
    assert "continue-on-error" not in job
    assert "continue-on-error" not in step
    assert not any(
        "run-plugin-tests.py" in item.get("run", "")
        for item in job["steps"]
    )


def assert_no_workers_in_required_workflow(workflow: str) -> None:
    declaration = _workflow(workflow)
    assert not _has_worker_option(declaration.get("env", {}).get("PYTEST_ADDOPTS"))
    worker_steps = []
    for name, job in declaration["jobs"].items():
        assert not _has_worker_option(job.get("env", {}).get("PYTEST_ADDOPTS"))
        for step in job.get("steps", []):
            if _has_worker_option(step.get("env", {}).get("PYTEST_ADDOPTS")):
                worker_steps.append((name, step.get("name")))
    assert worker_steps == []


def assert_bridge_xdist_stays_test_only_without_ci_worker_override() -> None:
    declaration = _workflow("ci.yml")
    assert "smoke" not in declaration["jobs"]
    assert_no_workers_in_required_workflow("ci.yml")
    with (ROOT / "plugins/agent-bridge/pyproject.toml").open("rb") as source:
        project = tomllib.load(source)["project"]
    assert project["optional-dependencies"]["dev"].count("pytest-xdist>=3.8,<4") == 1
    assert not any("pytest-xdist" in requirement.casefold() for requirement in project["dependencies"])


def assert_xdist_is_a_worktrees_test_dependency_not_a_runtime_requirement() -> None:
    with (ROOT / "plugins" / "agent-worktrees" / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)["project"]
    assert project["optional-dependencies"]["dev"].count("pytest-xdist>=3.8,<4") == 1
    assert not any("pytest-xdist" in requirement.casefold() for requirement in project["dependencies"])


def assert_manual_ci_uses_serial_units_without_legacy_promotion_inputs() -> None:
    declaration = _workflow("ci.yml")
    events = declaration.get("on", declaration.get(True))
    inputs = events["workflow_dispatch"].get("inputs", {})
    assert not {"promotion_run", "promotion_phase"} & inputs.keys()
    job = declaration["jobs"]["unit-tests"]
    assert "inputs." not in job["if"]
    assert "github.event_name != 'push'" in job["if"]
    assert_no_workers_in_required_workflow("ci.yml")
    assert_unit_step_preserves_coverage_and_failure_gates("ci.yml")
