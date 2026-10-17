"""Complete, disjoint CI partitions retain the existing runner and deadlines."""

from collections import Counter
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

from ci_plugin_partitions import partition_files, smoke_matrix

REPO = Path(__file__).resolve().parents[1]


def test_matrix_changes_only_dispatch_parallelism():
    rows = smoke_matrix(["agent-dispatch", "agent-bridge", "copilot-extensions-harness"])
    assert Counter(row["plugin"] for row in rows) == {
        "agent-dispatch": 3, "agent-bridge": 1, "copilot-extensions-harness": 1,
    }
    assert [row["partition"] for row in rows[:3]] == [0, 1, 2]
    assert all(row["partitions"] == 3 for row in rows[:3])
    assert all(row["partitions"] == 1 and row["name"] == row["plugin"] for row in rows[3:])
    assert smoke_matrix([]) == []


def test_candidate_qualification_requires_exactly_the_actual_smoke_matrix_names():
    from candidate_qualification_inventory import required_jobs
    from materialized_candidate_inventory import python_suites

    plugins = python_suites(REPO) - {"agent-worktrees"}
    actual = {row["name"] for row in smoke_matrix(sorted(plugins))}
    required, _, _ = required_jobs(REPO)
    assert required & (actual | plugins) == actual
    assert len(actual) == len(plugins) + 2


@pytest.mark.parametrize("plugins", [None, "agent-dispatch", [False], ["../outside"], ["same", "same"]])
def test_invalid_matrix_refuses_instead_of_omitting_work(plugins):
    with pytest.raises(ValueError):
        smoke_matrix(plugins)


def test_partitions_cover_every_actual_dispatch_file_exactly_once_and_keep_groups():
    root = REPO / "plugins" / "agent-dispatch"
    complete = [path.relative_to(root).as_posix() for path in sorted((root / "tests").rglob("test_*.py"))]
    parts = [partition_files(REPO, "agent-dispatch", index, 3) for index in range(3)]
    assert all(parts)
    assert Counter(path for part in parts for path in part) == Counter(complete)
    for start in range(0, len(complete), 25):
        group = set(complete[start:start + 25])
        assert sum(group <= set(part) for part in parts) == 1
    assert max(map(len, parts)) <= (len(complete) + 2) // 3 + 25


def test_exact_runner_test_file_consumer_keeps_original_process_groups():
    spec = importlib.util.spec_from_file_location("ci_partition_runner", REPO / "tools" / "run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original = runner._test_file_groups("agent-dispatch", 25)
    for index in range(3):
        selected = partition_files(REPO, "agent-dispatch", index, 3)
        assert runner._test_file_groups(
            "agent-dispatch", 25, test_files=tuple(selected),
        ) == original[index::3]


@pytest.mark.parametrize("index,count", [(-1, 3), (3, 3), (0, 0), (99999, 100000)])
def test_invalid_or_empty_partition_cannot_fall_back_to_full_suite(index, count):
    with pytest.raises(ValueError):
        partition_files(REPO, "agent-dispatch", index, count)


def test_real_helper_cli_produces_exact_runner_arguments_and_no_empty_success():
    script = REPO / "tools" / "ci_plugin_partitions.py"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [sys.executable, str(script), "files", "--plugin", "agent-dispatch",
         "--partition", "1", "--partitions", "3"],
        capture_output=True, text=True, timeout=10, creationflags=flags,
    )
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert args[::2] == ["--test-file"] * (len(args) // 2)
    assert args[1::2] == partition_files(REPO, "agent-dispatch", 1, 3)
    invalid = subprocess.run(
        [sys.executable, str(script), "files", "--plugin", "agent-dispatch",
         "--partition", "3", "--partitions", "3"],
        capture_output=True, text=True, timeout=10, creationflags=flags,
    )
    assert invalid.returncode == 2 and not invalid.stdout


def test_workflow_matrix_keeps_every_partition_required_and_existing_deadlines():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    smoke = jobs["smoke"]
    assert smoke["name"] == "${{ matrix.name }}"
    assert smoke["strategy"] == {
        "fail-fast": False, "matrix": {"include": "${{ fromJson(needs.discover.outputs.smoke_matrix) }}"},
    }
    assert smoke["timeout-minutes"] == "${{ (matrix.plugin == 'agent-dispatch' || matrix.plugin == 'agent-index' || matrix.plugin == 'agent-bridge') && 12 || 8 }}"
    assert "continue-on-error" not in smoke
    assert "smoke" in jobs["pr-gate"]["needs"] and "smoke" in jobs["promotion-gate"]["needs"]
    command = next(step["run"] for step in smoke["steps"] if step.get("name") == "Test ${{ matrix.plugin }}")
    assert 'files=$(python tools/ci_plugin_partitions.py files' in command
    assert 'python tools/run-plugin-tests.py "$PLUGIN" "${args[@]}"' in command
    assert "timeout" not in command and " -k " not in command and " --guards" not in command
    resolve = next(step["run"] for step in jobs["discover"]["steps"] if step.get("id") == "set")
    assert 'python tools/ci_plugin_partitions.py matrix "$SMOKE"' in resolve
    assert not any("test_ci_plugin_partitions.py" in step.get("run", "") for step in smoke["steps"])
    for identifier in ("test-runner-linux", "test-runner-windows"):
        job = jobs[identifier]
        selector = next(step for step in job["steps"] if step.get("id") == "runner-contracts")
        expression = re.search(r"grep -Eq\s*\\\n\s*'([^']+)'", selector["run"])
        assert expression is not None
        for path in (
            "tools/ci_plugin_partitions.py", "tools/test_ci_plugin_partitions.py",
            "tools/run-plugin-tests.py", "tools/test_run_plugin_tests.py", ".github/workflows/ci.yml",
        ):
            assert re.search(expression[1], path), (identifier, path)
        assert not re.search(expression[1], "docs/unrelated.md")
        contract = next(step for step in job["steps"] if step.get("name") == "Test runner admission and isolation")
        assert contract["if"] == "steps.runner-contracts.outputs.run == 'true'"
        assert contract["run"].count("tools/test_ci_plugin_partitions.py") == 1
        assert "continue-on-error" not in contract
        assert identifier in jobs["pr-gate"]["needs"] and identifier in jobs["promotion-gate"]["needs"]
    selectors = [
        step["run"] for job in jobs.values() for step in job.get("steps", [])
        if step.get("id") == "python-artifact-builder"
    ]
    assert len(selectors) == 2
    assert all("'^tools/(test_)?ci_plugin_partitions\\.py$'" in command for command in selectors)
