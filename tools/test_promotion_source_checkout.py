"""Keep frozen execution, trusted helper checkout and ancestry validation distinct."""

from copy import deepcopy
from pathlib import Path

import pytest

from _promotion_workflow_test_support import (
    assert_source_owned_boundary, source_workflow,
)

ROOT = Path(__file__).resolve().parent.parent


def jobs():
    return source_workflow(ROOT, "validate-and-promote.yml")["jobs"]


def assert_source_unit_checkout(declared):
    checkouts = [step for step in declared["source-units"]["steps"]
                 if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 2
    source = next(step for step in checkouts if "path" not in step["with"])
    helper = next(step for step in checkouts if step["with"].get("path") == "reviewed-helpers")
    assert source["with"] in ({
        "ref": "${{ needs.gate.outputs.sha }}", "fetch-depth": depth,
        "persist-credentials": False,
    } for depth in (0, "0"))
    assert helper["with"] == {
        "ref": "${{ needs.gate.outputs.sha }}", "path": "reviewed-helpers",
        "persist-credentials": False,
    }


def test_source_unit_checkout_preserves_release_history_and_evidence():
    declared = jobs()
    assert_source_owned_boundary(declared)
    assert_source_unit_checkout(declared)
    units = declared["source-units"]
    assert units["timeout-minutes"] == 5
    assert units["permissions"] == {"contents": "read"}
    assert units["if"] == (
        "needs.gate.outputs.is_dev == 'true' && needs.compose-candidate.outputs.composed == 'true'"
    )
    execute = next(step for step in units["steps"] if step.get("env", {}).get("SOURCE_SHA"))
    assert execute["env"]["SOURCE_SHA"] == "${{ needs.gate.outputs.sha }}"
    assert 'python "$RUNNER_TEMP/candidate/tools/run-promotion-unit-tests.py"' in execute["run"]
    for argument in (
        '--source-sha "$SOURCE_SHA"', '--development-root "$GITHUB_WORKSPACE"',
        '--candidate-tree "$CANDIDATE_TREE"', '--output-dir "$RUNNER_TEMP/promotion-unit-evidence"',
    ):
        assert argument in execute["run"]
    for name in (
        "gate", "compose-candidate", "build-python-artifacts", "python-artifacts", "promote",
        "qualify-candidate", "publish-python-artifacts", "record-promotion",
    ):
        path = "candidate" if name == "qualify-candidate" else None
        checkout = next(step for step in declared[name]["steps"]
                        if step.get("uses") == "actions/checkout@v4"
                        and step["with"].get("path") == path)
        assert checkout["with"]["fetch-depth"] in (0, "0"), name
    candidate = next(step for step in declared["publish-python-artifacts"]["steps"]
                     if step.get("with", {}).get("path") == "candidate")
    assert candidate["with"]["fetch-depth"] in (2, "2")


@pytest.mark.parametrize("field,value", [
    ("fetch-depth", 1),
    ("fetch-depth", None),
    ("ref", "dev"),
    ("ref", None),
    ("persist-credentials", True),
    ("persist-credentials", None),
    ("token", "${{ secrets.RELEASE_TOKEN }}"),
])
def test_source_unit_checkout_refuses_weakened_workflow(field, value):
    declared = deepcopy(jobs())
    assert_source_unit_checkout(declared)
    checkout = next(step for step in declared["source-units"]["steps"]
                    if step.get("uses") == "actions/checkout@v4")
    if value is None:
        checkout["with"].pop(field)
    else:
        checkout["with"][field] = value
    with pytest.raises(AssertionError):
        assert_source_unit_checkout(declared)


def test_source_unit_checkout_refuses_an_extra_checkout():
    declared = deepcopy(jobs())
    assert_source_unit_checkout(declared)
    checkout = next(step for step in declared["source-units"]["steps"]
                    if step.get("uses") == "actions/checkout@v4")
    declared["source-units"]["steps"].append(deepcopy(checkout))
    with pytest.raises(AssertionError):
        assert_source_unit_checkout(declared)


def test_source_checkout_policy_does_not_migrate_preserved_main_control():
    declared = deepcopy(jobs())
    checkout = next(step for step in declared["source-units"]["steps"]
                    if step.get("uses") == "actions/checkout@v4")
    checkout["with"]["fetch-depth"] = 1
    assert_source_owned_boundary(declared)
    with pytest.raises(AssertionError):
        assert_source_unit_checkout(declared)
