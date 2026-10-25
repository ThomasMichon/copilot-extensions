"""Pure object-boundary controls for late-Main classification."""
from __future__ import annotations

import pytest

import promotion_control as control
import promotion_control_transition as transition
from promotion_unit_tests.test_control_retirement import (
    CURRENT, MAIN, OLD, PRIOR, STATE, objects as objects,
)


@pytest.fixture
def snapshots(objects, monkeypatch):
    root, trees, _state, _receipt, blob, _save, _index, _ancestors, _calls = objects
    state = {
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "paused": False, "pause_reason": None, "last_promotion": None, "last_rollback": None,
    }
    trees[PRIOR][STATE] = blob(state)
    trees[MAIN][STATE] = blob(state)
    monkeypatch.setattr(transition, "_git", control._git)
    return root, trees, state, blob


def inspect(snapshots):
    return transition.inspect_main_transition(repo=snapshots[0], before=PRIOR, after=MAIN)


def test_declared_deletion_and_edit_require_new_execution(snapshots):
    result = inspect(snapshots)
    assert result == {
        "before": PRIOR, "after": MAIN, "kind": "control-only",
        "changed": [".github/promotion-control.json", CURRENT, OLD],
        "paused": False, "requires_fresh_execution": True,
    }


@pytest.mark.parametrize("operation", ["add", "rename"])
def test_trusted_manifest_can_add_or_rename_controls(snapshots, operation):
    _root, trees, _state, blob = snapshots
    new = ".github/workflows/new.yml"
    paths = [CURRENT, new]
    if operation == "add":
        paths.append(OLD)
        trees[MAIN][OLD] = trees[PRIOR][OLD]
    trees[MAIN][new] = blob(b"new control\n")
    trees[MAIN][".github/promotion-control.json"] = blob({
        "schema": "copilot-extensions.promotion-control", "version": 1, "files": paths,
    })
    assert inspect(snapshots)["kind"] == "control-only"


def test_operator_pause_is_distinct_from_promoter_lineage(snapshots):
    _root, trees, state, blob = snapshots
    state.update(paused=True, pause_reason="owner repair")
    trees[MAIN][STATE] = blob(state)
    result = inspect(snapshots)
    assert result["paused"] and result["requires_fresh_execution"]
    assert STATE in result["changed"]


def test_identical_tree_does_not_claim_a_control_change(snapshots):
    _root, trees, _state, _blob = snapshots
    trees[MAIN] = dict(trees[PRIOR])
    result = inspect(snapshots)
    assert result["kind"] == "unchanged" and not result["requires_fresh_execution"]


@pytest.mark.parametrize("path", [
    "payload.py", "uv.lock", ".github/CODEOWNERS", ".github/settings.yml",
    ".github/coverage-baselines/unit.json", ".github/workflows/unknown.yml",
])
def test_payload_baseline_or_undeclared_governance_change_refuses_recomposition(snapshots, path):
    _root, trees, _state, blob = snapshots
    trees[MAIN][path] = blob(b"unclassified\n")
    with pytest.raises(transition.ControlPreservationError, match="payload or unclassified"):
        inspect(snapshots)


@pytest.mark.parametrize("field,value", [
    ("last_promotion", {"dev_head": "d" * 40}),
    ("last_rollback", {"reason": "superseded"}),
    ("unknown_policy", True), ("version", True), ("paused", 1), ("pause_reason", []),
])
def test_promoter_lineage_unknown_state_and_invalid_pause_are_not_controls(snapshots, field, value):
    _root, trees, state, blob = snapshots
    state[field] = value
    trees[MAIN][STATE] = blob(state)
    with pytest.raises(transition.ControlPreservationError):
        inspect(snapshots)


@pytest.mark.parametrize("before,after", [
    (True, 1), (False, 0), (1, 1.0), ([True], [1]), ({"enabled": False}, {"enabled": 0}),
    (float("nan"), float("nan")), (float("inf"), float("inf")),
])
def test_nonoperator_state_comparison_preserves_json_types_and_finite_values(snapshots, before, after):
    _root, trees, state, blob = snapshots
    trees[PRIOR][STATE] = blob({**state, "unknown_policy": before})
    trees[MAIN][STATE] = blob({**state, "unknown_policy": after})
    with pytest.raises(transition.ControlPreservationError):
        inspect(snapshots)


@pytest.mark.parametrize("change", ["ownership-removed", "missing-file", "mode", "alias", "ancestry"])
def test_untrusted_or_incomplete_transition_fails(snapshots, objects, change):
    _root, trees, _state, _blob = snapshots
    if change == "ownership-removed":
        del trees[MAIN][".github/promotion-control.json"]
    elif change == "missing-file":
        del trees[MAIN][CURRENT]
    elif change == "mode":
        trees[MAIN][CURRENT] = ("100755", *trees[MAIN][CURRENT][1:])
    elif change == "alias":
        trees[MAIN][".github/Workflows/alias.yml"] = trees[MAIN][CURRENT]
    else:
        objects[7].remove(PRIOR)
    with pytest.raises(transition.ControlPreservationError):
        inspect(snapshots)


@pytest.mark.parametrize("before,after", [("main", MAIN), (PRIOR, "HEAD"), (None, MAIN)])
def test_mutable_or_invalid_input_is_rejected_before_object_access(objects, before, after):
    with pytest.raises(transition.ControlPreservationError):
        transition.inspect_main_transition(repo=objects[0], before=before, after=after)
    assert not objects[-1]
