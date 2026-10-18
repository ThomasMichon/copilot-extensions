"""Worktrees worker-policy assertions called by the existing CI admission suite."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("ci.yml", "validate-and-promote.yml")
FULL_STEP = "Full suite - ${{ matrix.plugin }}"
OPTIONS = {
    "ci.yml": (
        "${{ matrix.plugin == 'agent-worktrees' && inputs.promotion_run != '' "
        "&& '-n 2 --dist=worksteal' || '' }}"
    ),
    "validate-and-promote.yml": (
        "${{ matrix.plugin == 'agent-worktrees' && '-n 2 --dist=worksteal' || '' }}"
    ),
}


def _workflow(name: str) -> dict[str, Any]:
    value = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _has_worker_option(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"-n\s*=?\s*\d|--numprocesses", value))


def assert_full_step_uses_exactly_two_workers_without_narrowing_tests(workflow: str) -> None:
    job = _workflow(workflow)["jobs"]["full"]
    steps = [step for step in job["steps"] if step.get("name") == FULL_STEP]
    assert len(steps) == 1
    step = steps[0]
    assert step.get("env") == {"PYTEST_ADDOPTS": OPTIONS[workflow]}
    assert step["run"] == "python tools/run-plugin-tests.py ${{ matrix.plugin }}"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["strategy"]["fail-fast"] is False
    assert "continue-on-error" not in job
    assert "continue-on-error" not in step


def assert_workers_do_not_leak_into_guards_collection_or_baselines(workflow: str) -> None:
    declaration = _workflow(workflow)
    assert not _has_worker_option(declaration.get("env", {}).get("PYTEST_ADDOPTS"))
    worker_steps = []
    for name, job in declaration["jobs"].items():
        assert not _has_worker_option(job.get("env", {}).get("PYTEST_ADDOPTS"))
        for step in job.get("steps", []):
            if _has_worker_option(step.get("env", {}).get("PYTEST_ADDOPTS")):
                worker_steps.append((name, step.get("name")))
    assert worker_steps == [("full", FULL_STEP)]


def assert_xdist_is_a_worktrees_test_dependency_not_a_runtime_requirement() -> None:
    with (ROOT / "plugins" / "agent-worktrees" / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)["project"]
    assert project["optional-dependencies"]["dev"].count("pytest-xdist>=3.8,<4") == 1
    assert not any("pytest-xdist" in requirement.casefold() for requirement in project["dependencies"])


def assert_manual_main_validation_does_not_need_unshipped_test_dependencies() -> None:
    declaration = _workflow("ci.yml")
    events = declaration.get("on", declaration.get(True))
    inputs = events["workflow_dispatch"]["inputs"]
    assert inputs["promotion_run"].get("default", "") == ""
    assert " ".join(declaration["jobs"]["full"]["if"].split()) == (
        "github.event_name == 'workflow_dispatch' && "
        "(inputs.promotion_run == '' || inputs.promotion_phase == 'candidate')"
    )
