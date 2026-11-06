"""Incoming-generation acknowledgment rules without application processes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    ROOT / "worktree-manager" / "src" / "worktree_manager" / "bootstrap_activation.py"
)
SPEC = importlib.util.spec_from_file_location("manager_activation_unit", SOURCE)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


@pytest.mark.parametrize(
    "record",
    [
        {"action": "cutover", "runtime_generation": "v2", "result": {"ok": True}},
        {
            "action": "noop",
            "runtime_generation": "v2",
            "reason": "already-active-version",
            "route": {"version": "v2"},
        },
    ],
    ids=["started", "already-ready"],
)
def test_incoming_start_acknowledgment_is_accepted(record):
    assert activation.started_outcome(json.dumps(record), "v2") == record


@pytest.mark.parametrize(
    "record",
    [
        {"action": "cutover", "runtime_generation": "v1", "result": {"ok": True}},
        {"action": "cutover", "runtime_generation": "v2", "result": {"ok": 1}},
        {"action": "cutover", "runtime_generation": "v2", "result": {"ok": False}},
        {"action": "noop", "runtime_generation": "v2", "reason": "no-live-daemon"},
        {
            "action": "noop",
            "runtime_generation": "v2",
            "reason": "already-active-version",
            "route": {"version": "v1"},
        },
    ],
    ids=[
        "predecessor",
        "integer-success",
        "failed-start",
        "absent-predecessor",
        "wrong-route",
    ],
)
def test_predecessor_or_unconfirmed_state_cannot_commit_incoming_selection(record):
    assert activation.started_outcome(json.dumps(record), "v2") is None
