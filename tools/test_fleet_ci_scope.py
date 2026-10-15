"""Exercise the exact changed-path selectors checked into both native CI jobs."""

import re
from pathlib import Path

import pytest

WORKFLOW = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text()
PATTERNS = re.findall(r"'(\^\(fleet-manager/[^'\n]+)'; then", WORKFLOW)


def test_native_fleet_checks_are_both_conditionally_scoped():
    assert len(PATTERNS) == 2
    assert PATTERNS[0] == PATTERNS[1]
    assert WORKFLOW.count("if: steps.fleet-contracts.outputs.run == 'true'") == 2
    assert WORKFLOW.count(
        """'-m "not client_distribution_full and not observation_retention_full"'"""
    ) == 2
    assert WORKFLOW.count(
        "run-standalone-tests.py fleet-manager --prepare --admission-wait 60 "
        "--timeout ${{ github.event_name == 'workflow_dispatch' && 600 || 120 }}"
    ) == 2


@pytest.mark.parametrize("path,selected", [
    ("fleet-manager/src/fleet_manager/planning.py", True),
    ("fleet-manager/tests/test_planning.py", True),
    ("tools/run-standalone-tests.py", True),
    ("tools/standalone_consumers.py", True),
    ("tools/plugin_test_containment.py", True),
    ("libs/single-instance-lease/src/lease.py", True),
    ("libs/agent-procutil/src/agent_procutil/__init__.py", True),
    ("libs/agent-procutil/tests/test_no_window.py", True),
    ("libs/versioned-runtime/versioned_runtime.py", True),
    ("libs/versioned-runtime/standalone_build.py", True),
    (".github/workflows/ci.yml", True),
    ("plugins/agent-bridge/README.md", False),
    ("visions/machine-fleet/README.md", False),
    ("tools/run-standalone-tests.py.other", False),
    ("unrelated/fleet-manager/config.py", False),
    ("libs/agent-procutil-other/src/process.py", False),
])
def test_actual_workflow_regex_selects_only_fleet_and_runner_inputs(path, selected):
    assert len(PATTERNS) == 2
    for pattern in PATTERNS:
        assert bool(re.search(pattern, path)) is selected
