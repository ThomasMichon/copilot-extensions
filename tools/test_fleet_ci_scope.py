"""Exercise pre-allocation fleet admission and exact coverage partitions."""

import re
from pathlib import Path

import pytest

from ci_job_admission import select_jobs

WORKFLOW = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text()


def test_native_fleet_partitions_preserve_selection_and_deadlines():
    fleet = WORKFLOW.split("\n  fleet-native-linux:\n", 1)[1].split("\n  fleet-native-windows:", 1)[0]
    windows = WORKFLOW.split("\n  fleet-native-windows:\n", 1)[1].split("\n  agent-index-service-linux:", 1)[0]
    assert "runs-on: ubuntu-latest" in fleet
    assert "runs-on: windows-latest" in windows
    assert "partition: &fleet-native-partitions" in fleet
    assert "steps: &fleet-native-steps" in fleet
    assert "partition: *fleet-native-partitions" in windows
    assert "steps: *fleet-native-steps" in windows
    assert "fail-fast: false" in windows
    assert re.findall(r"          - name: ([a-z-]+)", fleet) == [
        "planner", "storage", "artifacts", "client-distribution", "gateway", "gateway-processes",
    ]
    assert re.findall(r"selector: '([^']+)'", fleet) == [
        "not gateway_ and not (artifact or client_distribution or client_diagnostics) "
        "and not (allocation_ or ledger or node_journal or observation_store)",
        "not gateway_ and not (artifact or client_distribution or client_diagnostics) "
        "and (allocation_ or ledger or node_journal or observation_store)",
        "not gateway_ and artifact",
        "not gateway_ and not artifact and (client_distribution or client_diagnostics)",
        "gateway_ and not gateway_processes",
        "gateway_processes",
    ]
    assert "fail-fast: false" in fleet
    assert "continue-on-error" not in fleet
    assert "Detect standalone fleet contract changes" not in fleet
    for job in (fleet, windows):
        assert "needs: discover" in job
        assert "needs.discover.outputs.fleet_contracts == 'true'" in job
        assert "needs.discover.outputs.admission_validated == 'true'" in job
        assert "github.event_name != 'pull_request' ||" in job
    assert '-k "${{ matrix.partition.selector }}"' in fleet
    assert WORKFLOW.count(
        """'-m "not client_distribution_full and not observation_retention_full"'"""
    ) == 1
    assert WORKFLOW.count(
        "run-standalone-tests.py fleet-manager --prepare --admission-wait 60 "
        "--timeout ${{ github.event_name == 'workflow_dispatch' && inputs.promotion_run == '' && 600 || 120 }}"
    ) == 1
    gate = WORKFLOW.split("    name: PR gate (required check)\n", 1)[1]
    needs = gate.split("    needs:\n", 1)[1].split("    runs-on:", 1)[0]
    assert "      - fleet-native-linux\n" in needs
    assert "      - fleet-native-windows\n" in needs
    promotion = WORKFLOW.split("\n  promotion-gate:\n", 1)[1].split("\n  pr-gate:", 1)[0]
    assert "      - fleet-native-linux\n" in promotion
    assert "      - fleet-native-windows\n" in promotion
    assert "(github.event_name != 'workflow_dispatch' || inputs.promotion_run != '')" in fleet
    assert "github.event_name == 'workflow_dispatch' && inputs.promotion_run == '' && 15" in fleet
    assert "&& 15 || 5" in fleet
    assert "&& 15 || 7" in windows
    assert (
        "python tools/seasoned_uv.py --uv uv -- --system pytest pyyaml && "
        "python -m pytest -q tools/test_fleet_ci_scope.py tools/test_ci_job_admission.py"
    ) in fleet
    assert "if: matrix.partition.name == 'planner'" in fleet
    for required in ("installation-context-windows", "vault-snapshot-windows"):
        assert f"      - {required}\n" in needs
        assert f"      - {required}\n" in promotion


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
    ("tools/run-standalone-tests.py.other", True),
    ("unrelated/fleet-manager/config.py", True),
    ("libs/agent-procutil-other/src/process.py", True),
])
def test_shared_admission_preserves_fleet_dependencies_and_widens_unknowns(path, selected):
    assert select_jobs([path], event="pull_request").fleet is selected
