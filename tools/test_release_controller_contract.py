"""Exercise the prepared controller, not an older preserved/generated workflow."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from promotion_unit_contract import UnitEvidenceError
from release_controller_contract import (
    CONTROL_REF, HELPER_JOBS, SOURCE_REF, helper_implementations,
    verify_controller_inputs, verify_helper_selection,
)

ROOT = Path(__file__).resolve().parents[1]


def _prepared():
    return yaml.safe_load((ROOT / ".github/workflows/validate-and-promote.yml").read_bytes())


def _implementations():
    return {name: (ROOT / "tools" / name).read_bytes()
            for name in helper_implementations(_prepared())}


def test_complete_prepared_controller_uses_its_actual_source_interfaces():
    workflow = _prepared()
    verify_helper_selection(workflow)
    verify_controller_inputs(workflow, _implementations())
    jobs = workflow["jobs"]
    bootstrap = [step["with"] for step in jobs["gate"]["steps"]
                 if step.get("uses", "").startswith("actions/checkout@")]
    assert all(checkout["ref"] == CONTROL_REF and checkout["persist-credentials"] is False
               for checkout in bootstrap)
    assert "RELEASE_HELPERS_SHA" not in str(workflow)


@pytest.mark.parametrize("job_name", HELPER_JOBS)
@pytest.mark.parametrize("wrong_source", [
    "main", "dev", "${{ github.sha }}", "${{ needs.promote.outputs.candidate_sha }}", "a" * 40,
])
def test_prepared_controller_refuses_any_second_helper_generation(job_name, wrong_source):
    workflow = deepcopy(_prepared())
    helper = next(step["with"] for step in workflow["jobs"][job_name]["steps"]
                  if step.get("with", {}).get("path") == "reviewed-helpers")
    assert helper["ref"] == SOURCE_REF
    helper["ref"] = wrong_source
    with pytest.raises(UnitEvidenceError, match="admitted identity"):
        verify_helper_selection(workflow)


def test_prepared_controller_rejects_known_old_complete_source_call_shape():
    workflow = _prepared()
    publisher = workflow["jobs"]["publish-python-artifacts"]
    publisher["steps"] = [
        step for step in publisher["steps"]
        if "promotion_source_archive.py" not in step.get("run", "")
        and not step.get("uses", "").startswith("actions/attest-build-provenance@")
    ]
    for step in publisher["steps"]:
        if step.get("id") == "publish":
            step["run"] = step["run"].replace(
                '--complete-source-dir "$RUNNER_TEMP/complete-source"', "",
            )
    with pytest.raises(UnitEvidenceError):
        verify_controller_inputs(workflow, _implementations())


def test_source_interface_change_cannot_wait_until_release_to_discover_missing_input():
    workflow = _prepared()
    sources = _implementations()
    sources["publish_python_artifacts.py"] += b'\nparser.add_argument("--new-input", required=True)\n'
    with pytest.raises(UnitEvidenceError, match="new-input"):
        verify_controller_inputs(workflow, sources)
