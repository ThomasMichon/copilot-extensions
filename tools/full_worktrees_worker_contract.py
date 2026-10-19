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
BRIDGE_STEP = "Test ${{ matrix.plugin }}"
BRIDGE_OPTIONS = (
    "${{ matrix.plugin == 'agent-bridge' && inputs.promotion_run != '' "
    "&& '-n 2 --dist=worksteal' || '' }}"
)
OPTIONS = {
    "ci.yml": (
        "${{ matrix.plugin == 'agent-worktrees' && inputs.promotion_run != '' "
        "&& '-n 2 --dist=worksteal' || '' }}"
    ),
    "validate-and-promote.yml": (
        "${{ matrix.plugin == 'agent-worktrees' && '-n 2 --dist=worksteal' || '' }}"
    ),
}
SOURCE_FULL_PLUGINS = (
    "agent-bridge", "agent-codespaces", "agent-containers", "agent-dispatch",
    "agent-logger", "agent-mcp", "agent-ssh", "agent-vault", "agent-worktrees",
)
SOURCE_BASELINE_WORKERS = (
    "${{ (matrix.plugin == 'agent-worktrees' || matrix.plugin == 'agent-dispatch') "
    "&& '2' || '1' }}"
)
SOURCE_COMPLETE_RUN = """plugin="${{ matrix.plugin }}"
python tools/run-plugin-tests.py "$plugin" \\
  --complete-baseline-out "${plugin}.json" \\
  --measured-commit "$DEV_SHA" \\
  --baseline-workers "$BASELINE_WORKERS"
python tools/coverage_guided_selection/completion.py \\
  "${plugin}.json" --repo "$GITHUB_WORKSPACE" --source "$DEV_SHA" \\
  --plugin "$plugin" --workers "$BASELINE_WORKERS" --run-id "$GITHUB_RUN_ID"
"""


def _workflow(name: str) -> dict[str, Any]:
    value = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _has_worker_option(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"-n\s*=?\s*\d|--numprocesses", value))


def _one_pass_source_full(declaration: dict[str, Any], workflow: str) -> bool:
    if workflow != "validate-and-promote.yml":
        return False
    job = declaration["jobs"]["full"]
    steps = [step for step in job["steps"] if step.get("name") == FULL_STEP]
    assert len(steps) == 1
    step = steps[0]
    run = step.get("run", "")
    signals = (
        "--complete-baseline-out", "--baseline-workers", "--measured-commit",
        "coverage_guided_selection/completion.py",
    )
    if not (
        any(signal in run for signal in signals)
        or {"BASELINE_WORKERS", "DEV_SHA"} & step.get("env", {}).keys()
    ):
        return False
    assert job["name"] == "full - ${{ matrix.plugin }}"
    assert job["needs"] == "gate" and job["if"] == "needs.gate.outputs.is_dev == 'true'"
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 30
    assert job["strategy"] == {
        "fail-fast": False, "matrix": {"plugin": list(SOURCE_FULL_PLUGINS)},
    }
    assert "continue-on-error" not in job and "continue-on-error" not in step
    assert step.get("if") is None
    assert step.get("env") == {
        "DEV_SHA": "${{ needs.gate.outputs.sha }}", "BASELINE_WORKERS": SOURCE_BASELINE_WORKERS,
    }
    assert run.replace("\r\n", "\n").strip() == SOURCE_COMPLETE_RUN.strip()
    checkouts = [step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["ref"] == "${{ needs.gate.outputs.sha }}"
    assert checkouts[0]["with"]["fetch-depth"] == 0
    assert "PYTEST_ADDOPTS" not in declaration.get("env", {})
    assert "PYTEST_ADDOPTS" not in job.get("env", {})
    for other in job["steps"]:
        assert "PYTEST_ADDOPTS" not in other.get("env", {})
        if other is not step:
            assert "BASELINE_WORKERS" not in other.get("env", {})
            assert "run-plugin-tests.py" not in other.get("run", "")
            assert "coverage_guided_selection/baseline.py" not in other.get("run", "")
    return True


def assert_full_step_uses_exactly_two_workers_without_narrowing_tests(workflow: str) -> None:
    declaration = _workflow(workflow)
    if _one_pass_source_full(declaration, workflow):
        return
    job = declaration["jobs"]["full"]
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
    one_pass = _one_pass_source_full(declaration, workflow)
    assert not _has_worker_option(declaration.get("env", {}).get("PYTEST_ADDOPTS"))
    worker_steps = []
    baseline_steps = []
    for name, job in declaration["jobs"].items():
        assert not _has_worker_option(job.get("env", {}).get("PYTEST_ADDOPTS"))
        if one_pass:
            assert "BASELINE_WORKERS" not in job.get("env", {})
        for step in job.get("steps", []):
            if _has_worker_option(step.get("env", {}).get("PYTEST_ADDOPTS")):
                worker_steps.append((name, step.get("name")))
            if "BASELINE_WORKERS" in step.get("env", {}):
                baseline_steps.append((name, step.get("name")))
    expected = [] if one_pass else [("full", FULL_STEP)]
    if one_pass:
        assert baseline_steps == [("full", FULL_STEP)]
    if workflow == "ci.yml":
        expected.insert(0, ("smoke", BRIDGE_STEP))
    assert worker_steps == expected


def assert_candidate_bridge_workers_share_the_existing_job_and_limits() -> None:
    declaration = _workflow("ci.yml")
    smoke = declaration["jobs"]["smoke"]
    step = next(step for step in smoke["steps"] if step.get("name") == BRIDGE_STEP)
    assert step["env"]["PYTEST_ADDOPTS"] == BRIDGE_OPTIONS
    assert "python tools/run-plugin-tests.py" in step["run"]
    assert "-k" not in step["run"] and "--guards" not in step["run"]
    assert smoke["runs-on"] == "ubuntu-latest"
    assert smoke["strategy"]["fail-fast"] is False
    assert "continue-on-error" not in smoke and "continue-on-error" not in step
    with (ROOT / "plugins/agent-bridge/pyproject.toml").open("rb") as source:
        project = tomllib.load(source)["project"]
    assert project["optional-dependencies"]["dev"].count("pytest-xdist>=3.8,<4") == 1
    assert not any("pytest-xdist" in requirement.casefold() for requirement in project["dependencies"])


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
