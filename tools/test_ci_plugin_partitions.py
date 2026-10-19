"""Complete, disjoint CI partitions retain the existing runner and deadlines."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml

from ci_plugin_partitions import partition_files, select_backend, smoke_matrix
from _promotion_workflow_test_support import source_workflow
from test_promotion_artifact_readiness import (
    generated_materializer_checkout as generated_materializer_checkout,
)

REPO = Path(__file__).resolve().parents[1]


def test_matrix_partitions_only_declared_large_suites():
    rows = smoke_matrix([
        "agent-dispatch", "agent-pull-requests", "agent-bridge", "copilot-extensions-harness",
    ])
    assert Counter(row["plugin"] for row in rows) == {
        "agent-dispatch": 3, "agent-pull-requests": 2,
        "agent-bridge": 1, "copilot-extensions-harness": 1,
    }
    assert [row["partition"] for row in rows[:3]] == [0, 1, 2]
    assert all(row["partitions"] == 3 for row in rows[:3])
    assert [row["partition"] for row in rows[3:5]] == [0, 1]
    assert all(row["partitions"] == 2 for row in rows[3:5])
    assert all(row["partitions"] == 1 and row["name"] == row["plugin"] for row in rows[5:])
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


@pytest.fixture
def isolated_partition_cli(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "ci_plugin_partitions.py"
    shutil.copyfile(REPO / "tools/ci_plugin_partitions.py", script)
    assert {path.name for path in tools.iterdir()} == {"ci_plugin_partitions.py"}
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"}
    }

    def run(*arguments):
        return subprocess.run(
            [sys.executable, "-S", str(script), *arguments],
            cwd=tmp_path, env=environment, capture_output=True, text=True,
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    return tmp_path, run


def test_isolated_legacy_matrix_and_files_cli_need_only_the_single_script(isolated_partition_cli):
    root, run = isolated_partition_cli
    matrix = run("matrix", '["agent-dispatch","agent-bridge"]')
    assert matrix.returncode == 0, matrix.stdout + matrix.stderr
    assert Counter(row["plugin"] for row in json.loads(matrix.stdout)) == {
        "agent-dispatch": 3, "agent-bridge": 1,
    }
    tests = root / "plugins/agent-dispatch/tests"
    tests.mkdir(parents=True)
    for index in range(26):
        (tests / f"test_{index:03d}.py").write_text("def test_fixture(): pass\n")
    files = run("files", "--plugin", "agent-dispatch", "--partition", "1", "--partitions", "2")
    assert files.returncode == 0, files.stdout + files.stderr
    assert files.stdout.splitlines() == ["--test-file", "tests/test_025.py"]
    assert {path.name for path in (root / "tools").iterdir()} == {"ci_plugin_partitions.py"}


def test_isolated_candidate_cli_reports_missing_helpers_without_legacy_fallback(isolated_partition_cli):
    _, run = isolated_partition_cli
    result = run(
        "candidate-smoke", '["agent-dispatch","agent-bridge"]',
        "--event", "workflow_dispatch", "--promotion-run", "123-1",
        "--phase", "candidate", "--profile", "complete-once-v1",
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "candidate-smoke helper dependency is unavailable: materialized_candidate_inventory" in result.stderr
    assert "Traceback" not in result.stderr


def test_candidate_qualification_requires_exactly_the_actual_smoke_matrix_names():
    from candidate_complete_once import complete_once_plan
    from candidate_qualification_inventory import required_jobs
    from materialized_candidate_inventory import python_suites

    suites = python_suites(REPO)
    text = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    plan = complete_once_plan(text, suites)
    plugins = suites - {"agent-worktrees"} if plan is None else set(plan["smoke"])
    workflow = yaml.safe_load(text)
    name = workflow["jobs"]["smoke"]["name"]
    if name == "${{ matrix.name }}":
        actual = {row["name"] for row in smoke_matrix(sorted(plugins))}
    else:
        assert name == "${{ matrix.plugin }}", "unaccounted qualification smoke matrix"
        actual = plugins
    required, _, _ = required_jobs(REPO)
    assert required & (actual | plugins) == actual
    extra = (
        (2 if "agent-dispatch" in plugins else 0)
        + (1 if "agent-pull-requests" in plugins else 0)
    ) if name == "${{ matrix.name }}" else 0
    assert len(actual) == len(plugins) + extra


@pytest.mark.parametrize("plugins", [None, "agent-dispatch", [False], ["../outside"], ["same", "same"]])
def test_invalid_matrix_refuses_instead_of_omitting_work(plugins):
    with pytest.raises(ValueError):
        smoke_matrix(plugins)


@pytest.mark.parametrize("plugin,count", [("agent-dispatch", 3), ("agent-pull-requests", 2)])
def test_partitions_cover_every_actual_file_exactly_once_and_keep_groups(plugin, count):
    root = REPO / "plugins" / plugin
    complete = [path.relative_to(root).as_posix() for path in sorted((root / "tests").rglob("test_*.py"))]
    parts = [partition_files(REPO, plugin, index, count) for index in range(count)]
    assert all(parts)
    assert Counter(path for part in parts for path in part) == Counter(complete)
    for start in range(0, len(complete), 25):
        group = set(complete[start:start + 25])
        assert sum(group <= set(part) for part in parts) == 1
    assert max(map(len, parts)) <= (len(complete) + count - 1) // count + 25


@pytest.mark.parametrize("plugin,count", [("agent-dispatch", 3), ("agent-pull-requests", 2)])
def test_exact_runner_test_file_consumer_keeps_original_process_groups(plugin, count):
    spec = importlib.util.spec_from_file_location("ci_partition_runner", REPO / "tools" / "run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original = runner._test_file_groups(plugin, 25)
    for index in range(count):
        selected = partition_files(REPO, plugin, index, count)
        assert runner._test_file_groups(
            plugin, 25, test_files=tuple(selected),
        ) == original[index::count]


def test_pr_owner_smoke_subgroups_preserve_every_assigned_file():
    spec = importlib.util.spec_from_file_location("pr_owner_group_runner", REPO / "tools" / "run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    maximum = runner._FILES_PER_SUBSUITE_OVERRIDES["agent-pull-requests"]
    assert maximum == 10
    for index in range(2):
        selected = partition_files(REPO, "agent-pull-requests", index, 2)
        groups = runner._test_file_groups(
            "agent-pull-requests", maximum, test_files=tuple(selected),
        )
        assert all(0 < len(group) <= 10 for group in groups)
        actual = [
            path.relative_to(REPO / "plugins" / "agent-pull-requests").as_posix()
            for group in groups for path in group
        ]
        assert actual == selected


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
    from test_public_toolchain_source import _artifact_scope

    workflow = source_workflow(REPO, "ci.yml")
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
    for identifier in ("checks", "windows-python-artifact-builder"):
        scope_jobs, expression = _artifact_scope(identifier, REPO)
        assert scope_jobs == jobs
        for path in (
            "tools/ci_plugin_partitions.py", "tools/test_ci_plugin_partitions.py",
            "tools/promotion_admission_evidence.py",
        ):
            assert re.search(expression, path), (identifier, path)
        assert not re.search(expression, "docs/unrelated.md")


def test_generated_partition_contract_keeps_independent_main_controller_unchanged(
    generated_materializer_checkout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_public_toolchain_source import _retain_main_ci

    generated = _retain_main_ci(generated_materializer_checkout, REPO)
    controller = generated / ".github/workflows/ci.yml"
    preserved = controller.read_bytes()
    monkeypatch.setattr(sys.modules[__name__], "REPO", generated)
    test_workflow_matrix_keeps_every_partition_required_and_existing_deadlines()
    assert controller.read_bytes() == preserved


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
