"""Complete, disjoint CI partitions retain the existing runner and deadlines."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest
import yaml

from ci_plugin_partitions import partition_files, select_backend, smoke_matrix

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


def test_single_job_override_preserves_all_plugins_and_default_partition_policy():
    plugins = ["agent-dispatch", "agent-bridge", "copilot-extensions-harness"]
    rows = smoke_matrix(plugins, single_job_plugins=frozenset({"agent-dispatch"}))
    assert [row["plugin"] for row in rows] == plugins
    assert all(row["partition"] == 0 and row["partitions"] == 1 for row in rows)
    assert all(row["name"] == row["plugin"] for row in rows)
    assert len(smoke_matrix(plugins)) == 5
    assert smoke_matrix([], single_job_plugins=frozenset({"agent-dispatch"})) == []


@pytest.mark.parametrize("override", [None, "agent-dispatch", frozenset({False}), frozenset({"../outside"})])
def test_invalid_single_job_override_refuses_instead_of_omitting_work(override):
    with pytest.raises(ValueError):
        smoke_matrix(["agent-dispatch"], single_job_plugins=override)


def test_real_matrix_cli_single_job_mode_is_explicit():
    script = REPO / "tools" / "ci_plugin_partitions.py"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    base = [sys.executable, str(script), "matrix", '["agent-dispatch","agent-bridge"]']
    default = subprocess.run(
        base, capture_output=True, text=True, timeout=10, creationflags=flags,
    )
    selected = subprocess.run(
        [*base, "--single-job-plugin", "agent-dispatch"],
        capture_output=True, text=True, timeout=10, creationflags=flags,
    )
    assert default.returncode == selected.returncode == 0
    assert len(json.loads(default.stdout)) == 4
    assert len(json.loads(selected.stdout)) == 2


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


@pytest.fixture
def backend_row() -> dict[str, Any]:
    return smoke_matrix(["agent-dispatch"], single_job_plugins=frozenset({"agent-dispatch"}))[0]


@pytest.fixture
def backend_context(backend_row: dict[str, Any]) -> dict[str, Any]:
    head, recipe = "a" * 40, "recipe-v1"
    return {
        "mode": "pr", "job": "smoke", "execution_started": False, "opt_in": True,
        "same_repository": True, "author_write": True, "sender_write": True,
        "trust_current": True, "head": head, "recipe": recipe,
        "provider": "worker-a", "now": 100,
        "readiness": {
            "row": deepcopy(backend_row), "head": head, "recipe": recipe,
            "provider": "worker-a", "provider_ready": True, "coverage_ready": True,
            "coverage": "complete", "observed_at": 95, "max_age_seconds": 20,
            "windows_ram": {
                "required": True, "free_bytes": 500, "required_bytes": 400,
                "observed_at": 99, "max_age_seconds": 5,
            },
        },
    }


def _set_backend_field(context: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    target = context
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(("plugin", "job"), [
    ("agent-dispatch", "smoke"), ("agent-worktrees", "worktrees-smoke"),
])
def test_exact_ready_pr_backend_preserves_the_existing_row(
    backend_context: dict[str, Any], plugin: str, job: str,
) -> None:
    row = smoke_matrix([plugin], single_job_plugins=frozenset({plugin}))[0]
    row["test_command"] = "existing command is not rewritten"
    backend_context["job"] = job
    backend_context["readiness"]["row"] = deepcopy(row)
    before = deepcopy((row, backend_context))
    assert select_backend(row, backend_context) == {
        "backend": "private", "reason": "ready-exact-ordinary-pr-recipe",
    }
    assert (row, backend_context) == before
    assert set(row) == {"plugin", "partition", "partitions", "name", "test_command"}


@pytest.mark.parametrize("mode", [
    "source", "source-main", "candidate", "legacy", "push", "workflow_dispatch", "", None,
])
def test_non_pr_modes_cannot_withdraw_hosted_coverage(
    backend_row: dict[str, Any], backend_context: dict[str, Any], mode: object,
) -> None:
    backend_context["mode"] = mode
    assert select_backend(backend_row, backend_context) == {
        "backend": "hosted", "reason": "not-ordinary-pr",
    }


@pytest.mark.parametrize("key", [
    "same_repository", "author_write", "sender_write", "trust_current", "opt_in",
])
@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_unknown_or_untrusted_authority_defaults_hosted(
    backend_row: dict[str, Any], backend_context: dict[str, Any], key: str, value: object,
) -> None:
    backend_context[key] = value
    result = select_backend(backend_row, backend_context)
    assert result["backend"] == "hosted" and result["reason"]


@pytest.mark.parametrize(("path", "value"), [
    (("head",), "HEAD"),
    (("head",), "refs/pull/1/merge"),
    (("head",), "b" * 39),
    (("head",), None),
    (("recipe",), None),
    (("recipe",), " "),
    (("provider",), ""),
    (("readiness",), None),
    (("readiness", "head"), "b" * 40),
    (("readiness", "recipe"), "another-recipe"),
    (("readiness", "provider"), "another-provider"),
    (("readiness", "row", "name"), "another-job-name"),
    (("readiness", "row", "partition"), 1),
    (("readiness", "row", "partition"), True),
    (("readiness", "provider_ready"), False),
    (("readiness", "provider_ready"), None),
    (("readiness", "provider_ready"), "true"),
    (("readiness", "coverage_ready"), False),
    (("readiness", "coverage_ready"), None),
    (("readiness", "coverage_ready"), 1),
    (("readiness", "coverage"), "shadow"),
    (("readiness", "coverage"), "partial"),
    (("readiness", "coverage"), None),
    (("readiness", "observed_at"), 79),
    (("readiness", "observed_at"), 101),
    (("readiness", "observed_at"), float("nan")),
    pytest.param(("readiness", "observed_at"), 10 ** 1000, id="overflow-clock"),
    (("readiness", "max_age_seconds"), 0),
    (("readiness", "max_age_seconds"), float("inf")),
    (("now",), None),
    (("now",), True),
    (("now",), float("nan")),
    (("readiness", "windows_ram"), None),
    (("readiness", "windows_ram", "required"), None),
    (("readiness", "windows_ram", "required"), "false"),
    (("readiness", "windows_ram", "free_bytes"), None),
    (("readiness", "windows_ram", "free_bytes"), True),
    (("readiness", "windows_ram", "free_bytes"), -1),
    (("readiness", "windows_ram", "free_bytes"), 399),
    (("readiness", "windows_ram", "required_bytes"), 0),
    (("readiness", "windows_ram", "required_bytes"), True),
    (("readiness", "windows_ram", "required_bytes"), "400"),
    (("readiness", "windows_ram", "observed_at"), 94),
    (("readiness", "windows_ram", "observed_at"), 101),
    (("readiness", "windows_ram", "observed_at"), None),
    (("readiness", "windows_ram", "max_age_seconds"), None),
])
def test_incomplete_or_mismatched_readiness_stays_hosted(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
    path: tuple[str, ...], value: object,
) -> None:
    _set_backend_field(backend_context, path, value)
    result = select_backend(backend_row, backend_context)
    assert result["backend"] == "hosted" and result["reason"]


def test_exact_windows_reserve_and_freshness_boundaries(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
) -> None:
    ready = backend_context["readiness"]
    ready["observed_at"] = 80
    ready["windows_ram"].update(free_bytes=400, observed_at=95)
    assert select_backend(backend_row, backend_context)["backend"] == "private"
    ready["windows_ram"]["free_bytes"] = 399
    assert select_backend(backend_row, backend_context)["reason"] == "windows-reserve-insufficient"
    ready["windows_ram"].update(free_bytes=400, observed_at=94.999)
    assert select_backend(backend_row, backend_context)["reason"] == "windows-reserve-not-fresh"


def test_native_provider_requires_an_explicit_reserve_declaration_and_complete_readiness(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
) -> None:
    ready = backend_context["readiness"]
    ready["windows_ram"] = {"required": False}
    assert select_backend(backend_row, backend_context)["backend"] == "private"
    ready["coverage"] = "shadow"
    assert select_backend(backend_row, backend_context)["backend"] == "hosted"
    ready["coverage"] = "complete"
    ready["provider_ready"] = False
    assert select_backend(backend_row, backend_context)["backend"] == "hosted"


@pytest.mark.parametrize(("job", "plugin"), [
    ("full", "agent-worktrees"), ("promotion-gate", "agent-dispatch"),
    ("smoke", "agent-bridge"), ("worktrees-smoke", "agent-dispatch"),
])
def test_only_the_two_existing_pr_recipe_identities_are_eligible(
    backend_row: dict[str, Any], backend_context: dict[str, Any], job: str, plugin: str,
) -> None:
    backend_context["job"] = job
    backend_row["plugin"] = plugin
    backend_context["readiness"]["row"] = deepcopy(backend_row)
    assert select_backend(backend_row, backend_context)["reason"] == "outside-selected-pr-recipes"


@pytest.mark.parametrize(("index", "count"), [(True, 1), (0, False), (-1, 1), (1, 1), (0, 0)])
def test_backend_selection_cannot_repair_or_repartition_an_invalid_row(
    backend_row: dict[str, Any], backend_context: dict[str, Any], index: object, count: object,
) -> None:
    backend_row.update(partition=index, partitions=count)
    backend_context["readiness"]["row"] = deepcopy(backend_row)
    assert select_backend(backend_row, backend_context)["reason"] == "invalid-existing-partition"


def test_mismatched_existing_job_field_stays_hosted(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
) -> None:
    backend_row["job"] = "full"
    backend_context["readiness"]["row"] = deepcopy(backend_row)
    assert select_backend(backend_row, backend_context)["reason"] == "job-identity-mismatch"


def test_selection_is_pure_and_has_no_sticky_private_fallback(
    backend_row: dict[str, Any], backend_context: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = smoke_matrix(["agent-dispatch", "agent-worktrees"])
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("selector read filesystem"))
        private = select_backend(backend_row, backend_context)
        backend_context["readiness"]["provider_ready"] = False
        hosted = select_backend(backend_row, backend_context)
    assert private["backend"] == "private"
    assert hosted["backend"] == "hosted"
    assert set(private) == set(hosted) == {"backend", "reason"}
    assert smoke_matrix(["agent-dispatch", "agent-worktrees"]) == inventory


def test_no_reselection_or_green_hosted_fallback_after_execution_starts(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
) -> None:
    backend_context["execution_started"] = True
    backend_context["readiness"]["provider_ready"] = False
    with pytest.raises(ValueError, match="pre-execution only"):
        select_backend(backend_row, backend_context)


@pytest.mark.parametrize("row", [None, {}])
def test_started_execution_cannot_fall_back_when_row_metadata_is_missing(
    backend_context: dict[str, Any], row: object,
) -> None:
    backend_context["execution_started"] = True
    with pytest.raises(ValueError, match="pre-execution only"):
        select_backend(row, backend_context)


@pytest.mark.parametrize("context", [None, {}, {"execution_started": None}])
def test_missing_execution_context_is_reasoned_hosted(
    backend_row: dict[str, Any], context: object,
) -> None:
    result = select_backend(backend_row, context)
    assert result["backend"] == "hosted" and result["reason"]


def test_missing_row_identity_or_half_partition_defaults_hosted(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
) -> None:
    backend_row["name"] = None
    backend_context["readiness"]["row"] = deepcopy(backend_row)
    assert select_backend(backend_row, backend_context)["reason"] == "row-identity-missing"
    backend_row["name"] = "agent-dispatch"
    backend_row.pop("partitions")
    backend_context["readiness"]["row"] = deepcopy(backend_row)
    assert select_backend(backend_row, backend_context)["reason"] == "invalid-existing-partition"


def test_dispatch_partition_identity_cannot_be_omitted(
    backend_row: dict[str, Any], backend_context: dict[str, Any],
) -> None:
    backend_row.pop("partition")
    backend_row.pop("partitions")
    backend_context["readiness"]["row"] = deepcopy(backend_row)
    assert select_backend(backend_row, backend_context)["reason"] == "invalid-existing-partition"


def test_existing_unpartitioned_heavy_job_can_be_selected(
    backend_context: dict[str, Any],
) -> None:
    row = {"plugin": "agent-worktrees", "name": "agent-worktrees (collect-only + guard tests)"}
    backend_context["job"] = "worktrees-smoke"
    backend_context["readiness"]["row"] = deepcopy(row)
    assert select_backend(row, backend_context)["backend"] == "private"
