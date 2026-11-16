"""Native acceptance packets retain independent cold-preparation budgets."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest


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
