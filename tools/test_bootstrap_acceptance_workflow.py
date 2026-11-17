"""Native acceptance packets retain independent cold-preparation budgets."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import re
import shlex
from typing import Any

import pytest
from _pytest.mark.expression import Expression


@pytest.mark.parametrize("runner", ["ubuntu-latest", "windows-latest"])
def test_prepared_python_packets_do_not_share_the_whole_update_job_budget(runner: str) -> None:
    yaml = importlib.import_module("yaml")
    root = Path(__file__).resolve().parents[1]
    workflow: dict[str, Any] = yaml.safe_load(
        (root / ".github" / "workflows" / "bootstrap-update-native-acceptance.yml").read_text(),
    )
    owners: dict[str, str] = {}
    jobs = workflow["jobs"]
    for name, job in jobs.items():
        if job["runs-on"] != runner:
            continue
        for step in job["steps"]:
            command = step.get("run", "")
            for packet in (
                "tests/test_update_python_path.py",
                "installation-context --test-file tests/test_powershell_bootstrap.py",
                "agent-codespaces --test-file tests/test_bootstrap_python.py",
            ):
                if packet in command:
                    assert packet not in owners
                    owners[packet] = name
    whole, sdk, evaluator = (
        owners[packet] for packet in (
            "tests/test_update_python_path.py",
            "installation-context --test-file tests/test_powershell_bootstrap.py",
            "agent-codespaces --test-file tests/test_bootstrap_python.py",
        )
    )
    assert whole != sdk == evaluator
    for name in (whole, sdk):
        assert jobs[name]["timeout-minutes"] == 5
        assert jobs[name]["env"] == jobs[whole]["env"]
        assert jobs[name]["steps"][:4] == jobs[whole]["steps"][:4]


def test_windows_behavioral_shards_preserve_every_selected_case_exactly_once() -> None:
    yaml = importlib.import_module("yaml")
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "bootstrap-update-native-acceptance.yml").read_text(),
    )
    job = workflow["jobs"]["bootstrap-update-windows"]
    command = job["steps"][-1]["run"]
    expression = shlex.split(command)[-1]
    marker = "${{ matrix.selection || 'test or not test' }}"
    assert marker in expression
    baseline = Expression.compile(expression.replace(marker, "test or not test"))
    shards = [
        Expression.compile(expression.replace(marker, row["selection"]))
        for row in job["strategy"]["matrix"]["include"]
    ]
    candidates = {
        f"{relative}::{node.name}"
        for relative in re.findall(r"--test-file (\S+)", command)
        for node in ast.walk(ast.parse((root / "worktree-manager" / relative).read_text()))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }

    def selected(selector: Expression) -> set[str]:
        result = set()
        for candidate in candidates:
            def matches(name: str, /, **kwargs: str | int | bool | None) -> bool:
                return not kwargs and name.casefold() in candidate.casefold()

            if selector.evaluate(matches):
                result.add(candidate)
        return result

    expected = selected(baseline)
    packets = [selected(shard) for shard in shards]
    assert expected and all(packets)
    assert set.union(*packets) == expected
    assert sum(map(len, packets)) == len(expected)
    native = {case for case in expected if "test_bootstrap_update_native.py::" in case}
    native_packets = [packet & native for packet in packets if packet & native]
    caller_loss = {
        case for case in native
        if "coalesces_pending_work" in case or "latest_waiter_preserves_active_worker" in case
    }
    assert len(native_packets) == 2 and caller_loss in native_packets
