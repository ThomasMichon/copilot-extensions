from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import __main__ as cli
from agent_worktrees import lineage_surfaces
from agent_worktrees import session_projection
from agent_worktrees import tracking


def _record(
    *,
    sessions: list[tracking.SessionEntry] | None = None,
    controllers: list[tracking.ControllerRelation] | None = None,
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
        status="active",
        completed_at=None,
        sessions=sessions or [],
        controllers=controllers or [],
        lifecycle_revision=4,
        head_revision=4,
        controller_revision=1 if controllers else 0,
    )


def test_worktree_lineage_preserves_fork_and_missing_nodes(monkeypatch) -> None:
    record = _record(sessions=[
        tracking.SessionEntry(
            "first",
            "2026-01-01T00:00:00",
            state="handed-off",
            successor="second",
            relation_revision=2,
        ),
    ])
    record.handoffs = [
        tracking.SessionHandoff(
            ordinal=1,
            token="not-exposed",
            predecessor="first",
            state="linked",
            opened_at="2026-01-01T01:00:00",
            successor="third",
            linked_at="2026-01-01T01:01:00",
        )
    ]
    monkeypatch.setattr(
        session_projection,
        "audit_relation",
        lambda _record, session_id, **_kwargs: {
            "session_id": session_id,
            "role": "bound",
            "status": "missing-session-tree",
        },
    )

    result = lineage_surfaces.worktree_lineage(record)

    assert result["surface"] == "worktree-lineage"
    assert result["revisions"] == {
        "lifecycle": 4,
        "head": 4,
        "controller": 0,
    }
    assert "token" not in result["handoffs"][0]
    assert {
        (edge["source"], edge["target"])
        for edge in result["graph"]["edges"]
    } == {
        ("first", "second"),
        ("first", "third"),
    }
    assert {
        node["session_id"]
        for node in result["graph"]["nodes"]
        if not node["authoritative"]
    } == {"second", "third"}
    assert result["graph"]["findings"][0]["status"] == "ambiguous"
    assert result["graph"]["findings"][0]["successors"] == ["second", "third"]


def test_worktree_lineage_preserves_cycles(monkeypatch) -> None:
    record = _record(sessions=[
        tracking.SessionEntry(
            "first",
            "2026-01-01T00:00:00",
            successor="second",
        ),
        tracking.SessionEntry(
            "second",
            "2026-01-02T00:00:00",
            successor="first",
            predecessor="first",
        ),
    ])
    monkeypatch.setattr(
        session_projection,
        "audit_relation",
        lambda _record, session_id, **_kwargs: {
            "session_id": session_id,
            "status": "current",
        },
    )

    result = lineage_surfaces.worktree_lineage(record)

    finding = next(
        item
        for item in result["graph"]["findings"]
        if item["session_id"] == "first"
    )
    assert finding["status"] == "cycle"
    assert finding["lineage"] == ["first", "second", "first"]


def test_worktree_lineage_bounds_sessions_and_preserves_head(monkeypatch) -> None:
    sessions = [
        tracking.SessionEntry(
            f"session-{index}",
            f"2026-01-01T00:{index % 60:02d}:00",
        )
        for index in range(lineage_surfaces.MAX_WORKTREE_SESSIONS + 2)
    ]
    record = _record(sessions=sessions)
    record.head_session = "session-0"
    monkeypatch.setattr(
        session_projection,
        "audit_relation",
        lambda _record, session_id, **_kwargs: {
            "session_id": session_id,
            "status": "current",
        },
    )

    result = lineage_surfaces.worktree_lineage(record)

    returned = {item["session_id"] for item in result["sessions"]}
    assert len(returned) == lineage_surfaces.MAX_WORKTREE_SESSIONS
    assert "session-0" in returned
    assert "session-1" not in returned
    assert result["bounds"]["sessions"]["omitted"] == 2
    assert result["bounds"]["sessions"]["overflow"] is True


def test_worktree_lineage_bounds_every_growing_collection(monkeypatch) -> None:
    record = _record(sessions=[
        tracking.SessionEntry(
            f"session-{index}",
            "2026-01-01T00:00:00",
        )
        for index in range(lineage_surfaces.MAX_WORKTREE_SESSIONS + 1)
    ])
    record.head_transitions = [
        tracking.HeadTransition(
            revision=index + 1,
            session_id=f"session-{index}",
            reason="bind",
            at="2026-01-01T00:00:00",
        )
        for index in range(lineage_surfaces.MAX_HEAD_TRANSITIONS + 1)
    ]
    record.handoffs = [
        tracking.SessionHandoff(
            ordinal=index + 1,
            token=f"token-{index}",
            predecessor=f"session-{index}",
            state="pending",
            opened_at="2026-01-01T00:00:00",
            candidate=f"candidate-{index}",
        )
        for index in range(lineage_surfaces.MAX_HANDOFFS + 1)
    ]
    record.controllers = [
        tracking.ControllerRelation(
            kind="session",
            source="explicit",
            relation_revision=index + 1,
            created_at="2026-01-01T00:00:00",
            controller_session_id=f"controller-{index}",
            state="ended",
        )
        for index in range(lineage_surfaces.MAX_CONTROLLERS + 1)
    ]
    monkeypatch.setattr(
        session_projection,
        "audit_relation",
        lambda _record, session_id, **_kwargs: {
            "session_id": session_id,
            "status": "current",
        },
    )
    monkeypatch.setattr(
        lineage_surfaces.controller_lineage,
        "controller_findings",
        lambda record, **_kwargs: [
            {"relation_revision": item.relation_revision, "status": "ended"}
            for item in record.controllers
        ],
    )

    result = lineage_surfaces.worktree_lineage(record)

    assert len(result["sessions"]) == lineage_surfaces.MAX_WORKTREE_SESSIONS
    assert len(result["head_transitions"]) == lineage_surfaces.MAX_HEAD_TRANSITIONS
    assert len(result["handoffs"]) == lineage_surfaces.MAX_HANDOFFS
    assert len(result["controllers"]) == lineage_surfaces.MAX_CONTROLLERS
    assert all(
        bound["overflow"]
        for bound in result["bounds"].values()
    )
    assert len(result["graph"]["edges"]) <= (
        2 * lineage_surfaces.MAX_WORKTREE_SESSIONS
        + lineage_surfaces.MAX_HANDOFFS
    )


def test_terminal_resolution_has_explicit_overflow() -> None:
    sessions = [
        tracking.SessionEntry(
            f"session-{index}",
            "2026-01-01T00:00:00",
            successor=f"session-{index + 1}",
        )
        for index in range(lineage_surfaces.MAX_LINEAGE_STEPS + 1)
    ]
    sessions[-1].successor = None
    record = _record(sessions=sessions)

    result = lineage_surfaces.controller_lineage.resolve_terminal_session(
        record,
        "session-0",
        max_steps=lineage_surfaces.MAX_LINEAGE_STEPS,
    )

    assert result["status"] == "overflow"
    assert result["next_session_id"] == (
        f"session-{lineage_surfaces.MAX_LINEAGE_STEPS}"
    )


def test_session_lineage_preserves_restored_overflow_and_retained_relations() -> None:
    record = _record(sessions=[
        tracking.SessionEntry(
            "session-a",
            "2026-01-01T00:00:00",
            relation_revision=2,
        )
    ])
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [{
            "project": "example",
            "worktree_id": "child",
            "role": "bound",
            "relation_revision": 2,
            "head_revision": 4,
            "is_head": True,
            "lifecycle_state": "active",
            "lineage": {
                "predecessor": None,
                "successor": None,
                "handoff_ordinal": None,
            },
        }],
        "relation_tombstones": [{
            "project": "example",
            "worktree_id": "old",
            "role": "controller",
            "relation_revision": 1,
        }],
        "overflow": True,
        "omitted_relations": 3,
    }

    result = lineage_surfaces.session_lineage(
        "session-a",
        projection_reader=lambda _session_id: projection,
        restored_reader=lambda _session_id: True,
        record_loader=lambda _project, _worktree_id: record,
    )

    assert result["projection"] == {
        "status": "incomplete",
        "restored": True,
        "schema_version": 1,
        "overflow": True,
        "omitted_relations": 3,
        "returned_relations": 1,
        "surface_omitted_relations": 0,
        "returned_tombstones": 1,
        "surface_omitted_tombstones": 0,
    }
    assert result["relations"][0]["authority"]["status"] == "restored-incomplete"
    assert result["relations"][0]["lineage"]["status"] == "resolved"
    assert result["relation_tombstones"][0]["worktree_id"] == "old"


def test_session_lineage_reports_missing_record_without_guessing() -> None:
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [{
            "project": "foreign",
            "worktree_id": "missing",
            "role": "controller",
            "relation_revision": 1,
            "controller_revision": 1,
        }],
        "overflow": False,
        "omitted_relations": 0,
    }

    result = lineage_surfaces.session_lineage(
        "session-a",
        projection_reader=lambda _session_id: projection,
        restored_reader=lambda _session_id: False,
        record_loader=lambda _project, _worktree_id: None,
    )

    assert result["relations"][0]["authority"]["status"] == "missing-record"
    assert "worktree" not in result["relations"][0]


def test_session_lineage_accepts_legacy_projection_defaults() -> None:
    result = lineage_surfaces.session_lineage(
        "session-a",
        projection_reader=lambda _session_id: {
            "version": 1,
            "session_id": "session-a",
            "relations": [],
        },
        restored_reader=lambda _session_id: False,
    )

    assert result["projection"]["status"] == "available"
    assert result["projection"]["overflow"] is False
    assert result["projection"]["omitted_relations"] == 0


def test_session_lineage_retains_newest_tombstones_and_reports_overflow() -> None:
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [],
        "relation_tombstones": [
            {
                "project": "example",
                "worktree_id": f"worktree-{index}",
                "role": "controller",
                "relation_revision": index,
            }
            for index in range(
                session_projection.MAX_RELATION_TOMBSTONES + 1
            )
        ],
        "overflow": False,
        "omitted_relations": 0,
    }

    result = lineage_surfaces.session_lineage(
        "session-a",
        projection_reader=lambda _session_id: projection,
        restored_reader=lambda _session_id: False,
    )

    assert result["projection"]["status"] == "incomplete"
    assert result["projection"]["surface_omitted_tombstones"] == 1
    assert result["relation_tombstones"][0]["worktree_id"] == "worktree-1"
    assert result["relation_tombstones"][-1]["worktree_id"] == (
        f"worktree-{session_projection.MAX_RELATION_TOMBSTONES}"
    )


def test_session_lineage_does_not_read_unrelated_controller_projections(
    monkeypatch,
) -> None:
    record = _record(
        sessions=[
            tracking.SessionEntry(
                "session-a",
                "2026-01-01T00:00:00",
                relation_revision=2,
            )
        ],
        controllers=[
            tracking.ControllerRelation(
                kind="session",
                source="explicit",
                relation_revision=1,
                created_at="2026-01-01T00:00:00",
                controller_session_id="other-session",
            )
        ],
    )
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [{
            "project": "example",
            "worktree_id": "child",
            "role": "bound",
            "relation_revision": 2,
            "head_revision": 4,
            "is_head": True,
            "lifecycle_state": "active",
            "lineage": {
                "predecessor": None,
                "successor": None,
                "handoff_ordinal": None,
            },
        }],
        "overflow": False,
        "omitted_relations": 0,
    }
    monkeypatch.setattr(
        lineage_surfaces.controller_lineage,
        "controller_findings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not inspect unrelated session projections")
        ),
    )

    result = lineage_surfaces.session_lineage(
        "session-a",
        projection_reader=lambda _session_id: projection,
        restored_reader=lambda _session_id: False,
        record_loader=lambda _project, _worktree_id: record,
    )

    worktree = result["relations"][0]["worktree"]
    assert worktree["presentation"] == {
        "evaluated": False,
        "reason": "exact-session-scope",
    }


def test_session_lineage_reports_unsupported_projection() -> None:
    def unsupported(_session_id: str):
        raise session_projection.UnsupportedProjectionVersion("future")

    result = lineage_surfaces.session_lineage(
        "session-a",
        projection_reader=unsupported,
        restored_reader=lambda _session_id: False,
    )

    assert result["projection"]["status"] == "unsupported"
    assert result["relations"] == []


def test_lineage_cli_commands_emit_versioned_payloads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    record = _record()
    record_path = tmp_path / "child.yaml"
    captured: list[dict] = []
    monkeypatch.setattr(cli, "_json_output", captured.append)
    monkeypatch.setattr(cli, "_find_tracking_file", lambda _worktree_id: record_path)
    monkeypatch.setattr(tracking, "load_record", lambda _path: record)
    monkeypatch.setattr(
        lineage_surfaces,
        "worktree_lineage",
        lambda _record: {"surface": "worktree-lineage"},
    )
    monkeypatch.setattr(
        lineage_surfaces,
        "session_lineage",
        lambda session_id: {
            "surface": "session-lineage",
            "session_id": session_id,
        },
    )

    assert cli.cmd_worktree_lineage(
        argparse.Namespace(worktree_id="child", json=True)
    ) == 0
    assert cli.cmd_session_lineage(
        argparse.Namespace(session_id="session-a", json=True)
    ) == 0
    assert captured == [
        {"surface": "worktree-lineage"},
        {"surface": "session-lineage", "session_id": "session-a"},
    ]
