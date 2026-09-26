from __future__ import annotations

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine import PickerScreen


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


# ---------------------------------------------------------------------------
# #3307 Phase 5: the retired "R" column's values fold into STATE/LIVE instead
# ---------------------------------------------------------------------------

def test_handed_off_relation_becomes_handoff_state_when_not_live() -> None:
    relation = {
        "version": 1,
        "state": "handed-off",
        "binding": {"state": "handed-off"},
        "control": {"state": "none"},
        "actions": [],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["relation"] == "HANDOFF"
    assert row["state"] == "HANDOFF"
    # Not a FINAL/MERGED descriptor state, so no closure-style override.
    assert row["state_style"] is None


def test_live_session_beats_handoff_relation() -> None:
    """A live process still wins ACTIVE over a stale HANDOFF relation -- a
    successor already resumed, so the handoff itself is no longer the most
    useful thing to show."""
    relation = {
        "version": 1,
        "state": "handed-off",
        "binding": {"state": "handed-off"},
        "control": {"state": "none"},
        "actions": [],
    }

    row = derive.norm(
        _raw(reciprocal_relation=relation, mux_attached=True), "host", "windows")

    assert row["state"] == "ACTIVE"


def test_bucket_places_handoff_state_in_recent() -> None:
    relation = {
        "version": 1,
        "state": "handed-off",
        "binding": {"state": "handed-off"},
        "control": {"state": "none"},
        "actions": [],
    }
    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")
    _active, recent, _completed = derive.bucket([row])
    assert [r["id"] for r in recent] == ["child"]


def test_live_worktree_shows_acp_mode_when_bridge_interface() -> None:
    """BOUND vs CONTROL folds into LIVE as a CLI/ACP interface-mode marker
    (not the reciprocal_relation binding/control axis -- a worktree owned by
    an orchestrating CLI session still reads as CONTROL despite its own live
    session being ordinary CLI, so the mode marker uses ``interface``
    directly)."""
    row = derive.norm(
        _raw(session_bound_live=True, interface="acp"), "host", "windows")
    assert row["sess"] == "ACP"
    assert row["state"] == "ACTIVE"


def test_live_worktree_shows_proc_for_cli_interface() -> None:
    row = derive.norm(
        _raw(session_bound_live=True, interface="cli"), "host", "windows")
    assert row["sess"] == "PROC"


def test_controlled_elsewhere_cli_worktree_still_shows_proc() -> None:
    """The common agent-orchestrated case (this very effort's own worktrees):
    CONTROL no longer implies ACP -- a CLI-interface worktree owned by
    another session still shows PROC, not ACP."""
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "bound-here"},
        "control": {"state": "controlled-remote"},
        "actions": [],
    }
    row = derive.norm(
        _raw(reciprocal_relation=relation, session_bound_live=True,
             interface="cli"),
        "host", "windows")
    assert row["relation"] == "CONTROL"
    assert row["sess"] == "PROC"


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
