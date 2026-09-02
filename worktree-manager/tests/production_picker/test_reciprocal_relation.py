from __future__ import annotations

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine import (
    ACTIVE_SPECS,
    LIST_SPECS,
    PickerScreen,
)


def _raw(**values):
    row = {
        "id": "child",
        "repo": "example",
        "status": "active",
        "started_at": "2026-01-01T00:00:00",
    }
    row.update(values)
    return row


def test_current_reciprocal_relation_is_preserved() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "bound-here", "session_id": "bound"},
        "control": {"state": "controlled-remote"},
        "actions": [],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"] == relation
    assert row["relation"] == "CONTROL"


def test_legacy_controller_row_fails_closed() -> None:
    row = derive.norm(
        _raw(controllers=[{"relation_revision": 1}]),
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["state"] == "ambiguous"
    assert row["reciprocal_relation"]["actions"] == []
    assert row["relation"] == "AMBIG"


def test_ambiguous_payload_cannot_enable_navigation() -> None:
    relation = {
        "version": 1,
        "state": "ambiguous",
        "binding": {"state": "unknown"},
        "control": {"state": "ambiguous"},
        "actions": [{
            "kind": "navigate-worktree",
            "scope": "remote",
            "target": {
                "project": "example",
                "worktree_id": "parent",
            },
        }],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_remote_navigation_requires_exact_machine() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "unbound"},
        "control": {"state": "controlled-remote"},
        "actions": [{
            "kind": "navigate-worktree",
            "scope": "remote",
            "target": {
                "project": "example",
                "worktree_id": "parent",
            },
        }],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_summary_must_match_binding_and_control_axes() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "unbound"},
        "control": {"state": "none"},
        "actions": [],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"


def test_navigation_target_must_match_control_target() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "unbound"},
        "control": {
            "state": "controlled-remote",
            "target": {
                "project": "example",
                "worktree_id": "declared",
                "machine": "remote-host",
            },
        },
        "actions": [{
            "kind": "navigate-worktree",
            "scope": "remote",
            "target": {
                "project": "example",
                "worktree_id": "other",
                "machine": "remote-host",
            },
        }],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_relation_column_is_present_in_both_grids() -> None:
    assert any(spec[0] == "relation" for spec in ACTIVE_SPECS)
    assert any(spec[0] == "relation" for spec in LIST_SPECS)


def test_controller_navigation_requires_exact_loaded_target() -> None:
    target = {
        "raw": {"id": "parent", "repo": "example"},
        "machine": "remote-host",
        "source_id": "machine-ssh:remote-host:windows",
    }
    child = {
        "reciprocal_relation": {
            "actions": [{
                "kind": "navigate-worktree",
                "target": {
                    "project": "example",
                    "worktree_id": "parent",
                    "machine": "remote-host",
                },
            }],
        },
    }
    picker = object.__new__(PickerScreen)
    picker.data = [target]

    assert picker._reciprocal_target_row(child) is target

    picker.data.append({
        **target,
        "source_id": "machine-ssh:remote-host:wsl",
    })
    assert picker._reciprocal_target_row(child) is None
