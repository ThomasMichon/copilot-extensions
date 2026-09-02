from __future__ import annotations

from agent_worktrees import reciprocal_presentation
from agent_worktrees import tracking
from agent_worktrees import __main__ as cli
from agent_worktrees.picker_tui import derive
from agent_worktrees.picker_tui.engine import PickerScreen


def _record(
    *,
    sessions: list[tracking.SessionEntry] | None = None,
    controllers: list[tracking.ControllerRelation] | None = None,
    status: str = "active",
) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="child",
        branch="worktree/child",
        worktree_path="/tmp/child",
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status=status,
        completed_at=(
            "2026-01-02T00:00:00" if status == "finalized" else None
        ),
        sessions=sessions or [],
        controllers=controllers or [],
        controller_revision=1 if controllers else 0,
    )


def _controller() -> tracking.ControllerRelation:
    return tracking.ControllerRelation(
        kind="session",
        source="explicit",
        relation_revision=1,
        created_at="2026-01-01T00:00:00",
        controller_ref="host/example/parent#controller",
        controller_session_id="controller",
    )


def test_bound_and_controlled_axes_remain_distinct() -> None:
    record = _record(
        sessions=[
            tracking.SessionEntry("bound", "2026-01-01T00:00:00"),
        ],
        controllers=[_controller()],
    )
    finding = {
        "status": "resolved",
        "relation_state": "active",
        "relation_revision": 1,
        "controller_project": "example",
        "controller_worktree_id": "parent",
        "controller_machine": "host",
        "terminal_session_id": "controller",
        "head_revision": 3,
    }

    result = reciprocal_presentation.derive(record, [finding])

    assert result["state"] == "controlled-elsewhere"
    assert result["binding"]["state"] == "bound-here"
    assert result["control"]["state"] == "controlled-local"
    assert result["actions"][0]["kind"] == "navigate-worktree"


def test_unknown_controller_finding_fails_closed_to_ambiguous() -> None:
    record = _record(controllers=[_controller()])
    finding = {
        "status": "restored-newer",
        "relation_state": "active",
        "relation_revision": 1,
    }

    result = reciprocal_presentation.derive(record, [finding])

    assert result["state"] == "ambiguous"
    assert result["actions"] == []


def test_missing_findings_for_active_controller_are_ambiguous() -> None:
    result = reciprocal_presentation.derive(
        _record(controllers=[_controller()]),
        [],
    )

    assert result["state"] == "ambiguous"
    assert result["control"]["reason"] == "findings-unavailable"


def test_handed_off_binding_is_not_terminal() -> None:
    record = _record(
        sessions=[
            tracking.SessionEntry(
                "first",
                "2026-01-01T00:00:00",
                state="handed-off",
                successor="second",
            ),
            tracking.SessionEntry(
                "second",
                "2026-01-02T00:00:00",
                state="concluded",
                predecessor="first",
            ),
        ],
    )
    record.head_revision = 1
    record.head_transitions = [
        tracking.HeadTransition(
            revision=1,
            session_id="first",
            reason="handoff",
            at="2026-01-01T00:00:00",
        )
    ]

    result = reciprocal_presentation.derive(record, [])

    assert result["state"] == "handed-off"
    assert result["binding"]["terminal_status"] == "controller-terminal"


def test_pending_handoff_precedes_still_active_bound_head() -> None:
    record = _record(
        sessions=[
            tracking.SessionEntry("first", "2026-01-01T00:00:00"),
        ],
    )
    record.handoffs = [
        tracking.SessionHandoff(
            ordinal=1,
            token="handoff",
            predecessor="first",
            state="pending",
            opened_at="2026-01-01T01:00:00",
            candidate="second",
        )
    ]

    result = reciprocal_presentation.derive(record, [])

    assert result["state"] == "handed-off"
    assert result["binding"]["candidate_session_id"] == "second"


def test_unrelated_pending_handoff_does_not_hide_new_bound_head() -> None:
    record = _record(
        sessions=[
            tracking.SessionEntry("first", "2026-01-01T00:00:00"),
            tracking.SessionEntry("new", "2026-01-02T00:00:00"),
        ],
    )
    record.head_revision = 1
    record.head_transitions = [
        tracking.HeadTransition(
            revision=1,
            session_id="new",
            reason="handoff",
            at="2026-01-02T00:00:00",
        )
    ]
    record.handoffs = [
        tracking.SessionHandoff(
            ordinal=1,
            token="other-handoff",
            predecessor="first",
            state="pending",
            opened_at="2026-01-01T01:00:00",
        )
    ]

    result = reciprocal_presentation.derive(record, [])

    assert result["state"] == "bound-here"
    assert result["binding"]["session_id"] == "new"


def test_finalized_record_is_terminal() -> None:
    result = reciprocal_presentation.derive(
        _record(status="finalized"),
        [],
    )

    assert result["state"] == "terminal"


def test_legacy_picker_row_with_controller_is_ambiguous() -> None:
    row = derive.norm(
        {
            "id": "child",
            "repo": "example",
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "controllers": [{"relation_revision": 1}],
        },
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["state"] == "ambiguous"
    assert row["relation"] == "AMBIG"


def test_invalid_presentation_payload_is_inspect_only() -> None:
    row = derive.norm(
        {
            "id": "child",
            "repo": "example",
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "reciprocal_relation": {"version": 99, "state": "bound-here"},
        },
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["state"] == "ambiguous"
    assert row["reciprocal_relation"]["actions"] == []


def test_ambiguous_presentation_cannot_carry_navigation() -> None:
    row = derive.norm(
        {
            "id": "child",
            "repo": "example",
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "reciprocal_relation": {
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
            },
        },
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_remote_navigation_requires_exact_machine() -> None:
    row = derive.norm(
        {
            "id": "child",
            "repo": "example",
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "reciprocal_relation": {
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
            },
        },
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_summary_must_match_binding_and_control_axes() -> None:
    row = derive.norm(
        {
            "id": "child",
            "repo": "example",
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "reciprocal_relation": {
                "version": 1,
                "state": "controlled-elsewhere",
                "binding": {"state": "unbound"},
                "control": {"state": "none"},
                "actions": [],
            },
        },
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["compatibility"] == "invalid"


def test_navigation_target_must_match_control_target() -> None:
    row = derive.norm(
        {
            "id": "child",
            "repo": "example",
            "status": "active",
            "started_at": "2026-01-01T00:00:00",
            "reciprocal_relation": {
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
            },
        },
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_worktree_json_exposes_normalized_relation(monkeypatch) -> None:
    record = _record(controllers=[_controller()])
    finding = {
        "status": "remote",
        "relation_state": "active",
        "relation_revision": 1,
        "controller_project": "example",
        "controller_worktree_id": "parent",
        "controller_machine": "remote-host",
        "remote_session_id": "controller",
    }
    monkeypatch.setattr(cli, "_controller_findings", lambda _record: [finding])

    row = cli._worktree_to_dict(record)

    assert row["reciprocal_relation"]["state"] == "controlled-elsewhere"
    assert row["reciprocal_relation"]["control"]["state"] == "controlled-remote"
    assert row["reciprocal_relation"]["actions"][0]["target"] == {
        "project": "example",
        "worktree_id": "parent",
        "machine": "remote-host",
        "session_id": "controller",
    }


def test_picker_navigation_requires_one_exact_loaded_target() -> None:
    target = {
        "raw": {"id": "parent", "repo": "example"},
        "machine": "host",
        "source_id": "machine-ssh:host:windows",
    }
    rec = {
        "reciprocal_relation": {
            "actions": [{
                "kind": "navigate-worktree",
                "target": {
                    "project": "example",
                    "worktree_id": "parent",
                    "machine": "host",
                },
            }],
        },
    }
    picker = object.__new__(PickerScreen)
    picker.data = [target]

    assert picker._reciprocal_target_row(rec) is target

    picker.data.append(dict(target))
    assert picker._reciprocal_target_row(rec) is None
