"""Exercise the recorded source proposal or an explicitly prepared controller."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from _promotion_workflow_test_support import source_workflow
from promotion_unit_contract import UnitEvidenceError
from release_controller_contract import (
    CONTROL_REF, HELPER_JOBS, SOURCE_REF, helper_implementations,
    verify_controller_inputs, verify_helper_selection,
)

ROOT = Path(__file__).resolve().parents[1]


def _prepared(path: Path | None = None):
    if path is not None:
        return yaml.safe_load(path.read_bytes())
    return source_workflow(ROOT, "validate-and-promote.yml")


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


def test_source_contract_default_does_not_activate_or_substitute_explicit_main(
    tmp_path, monkeypatch,
):
    import sys

    source = _prepared()
    legacy = deepcopy(source)
    legacy["env"] = {"RELEASE_HELPERS_SHA": "a" * 40}
    preserved = tmp_path / ".github/workflows/validate-and-promote.yml"
    preserved.parent.mkdir(parents=True)
    preserved.write_text(yaml.safe_dump(legacy), encoding="utf-8")
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def recorded_recipe(root, name):
        assert root == tmp_path and name == "validate-and-promote.yml"
        return deepcopy(source)

    monkeypatch.setattr(module, "source_workflow", recorded_recipe)
    verify_helper_selection(_prepared())
    with pytest.raises(UnitEvidenceError, match="manual helper pin"):
        verify_helper_selection(_prepared(preserved))


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


def test_actual_indirect_promoter_requires_its_own_inputs_not_composition_arguments():
    workflow = _prepared()
    sources = _implementations()
    sources["promote_release.py"] += b'\nparser.add_argument("--future-required-input", required=True)\n'
    compose = next(step for step in workflow["jobs"]["compose-candidate"]["steps"]
                   if step.get("id") == "freeze")
    compose["run"] = compose["run"].replace(
        "args=(--repo", "args=(--future-required-input value --repo",
    )
    with pytest.raises(UnitEvidenceError, match="promote: promote_release.py missing required inputs"):
        verify_controller_inputs(workflow, sources)
    promote = next(step for step in workflow["jobs"]["promote"]["steps"]
                   if step.get("id") == "promote")
    promote["run"] = promote["run"].replace(
        "args=(--repo", "args=(--future-required-input value --repo",
    )
    verify_controller_inputs(workflow, sources)
