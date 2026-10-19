"""Independent fixtures for the dedicated official review setup contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SETUP = """\
name: Copilot Code Review setup
on:
  workflow_dispatch:
permissions: {}
jobs:
  copilot-setup-steps:
    runs-on: ubuntu-latest
    permissions: {}
    steps:
      - name: Complete minimal review setup
        timeout-minutes: 5
        run: echo "Copilot Code Review setup complete."
"""

JOB = ("jobs", "copilot-setup-steps")
BAD_SETUPS: list[tuple[tuple[str, ...], object]] = [
    ((*JOB, "runs-on"), "copilot-extensions-ci"),
    ((*JOB, "runs-on"), "copilot-extensions-review-other"),
    ((*JOB, "runs-on"), "Copilot-Extensions-Review"),
    ((*JOB, "runs-on"), "windows-latest"),
    ((*JOB, "runs-on"), "${{ vars.REVIEW_RUNNER }}"),
    ((*JOB, "runs-on"), ["copilot-extensions-review"]),
    ((*JOB, "runs-on"), {"group": "default", "labels": "copilot-extensions-review"}),
    (("on",), {"pull_request": ""}),
    (("permissions",), {"contents": "write"}),
    (("env",), {"TOKEN": "${{ github.token }}"}),
    (("defaults",), {"run": {"shell": "bash"}}),
    ((*JOB, "permissions"), {"contents": "read"}),
    ((*JOB, "secrets"), "inherit"),
    ((*JOB, "if"), "false"),
    ((*JOB, "continue-on-error"), "true"),
    ((*JOB, "timeout-minutes"), "5"),
    ((*JOB, "steps"), []),
    ((*JOB, "steps"), [{"run": "echo changed"}]),
    ((*JOB, "steps"), [{
        "name": "Complete minimal review setup",
        "timeout-minutes": "5",
        "env": {"TOKEN": "${{ secrets.RELEASE_TOKEN }}"},
        "run": 'echo "Copilot Code Review setup complete."',
    }]),
    ((*JOB, "steps"), [{
        "name": "Complete minimal review setup",
        "timeout-minutes": "5",
        "run": 'echo\n"Copilot Code Review setup complete."',
    }]),
    ((*JOB, "steps"), [{
        "name": "Complete minimal review setup",
        "timeout-minutes": "5",
        "run": 'echo "Copilot Code Review setup complete."; curl example.com',
    }]),
    (("jobs", "extra"), {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}),
    (("jobs", "extra"), {"runs-on": "copilot-extensions-review", "steps": [{"run": "true"}]}),
]


def write_setup(
    directory: Path, *, runner: str = "ubuntu-latest",
    change: tuple[tuple[str, ...], object] | None = None,
) -> Path:
    workflow: dict[str, Any] = yaml.load(SETUP, Loader=yaml.BaseLoader)
    workflow["jobs"]["copilot-setup-steps"]["runs-on"] = runner
    if change is not None:
        keys, value = change
        target = workflow
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    path = directory / "copilot-code-review.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    return path
