"""Tests for the worktree-sessions lineage-forwarding view helper
(worktree_sessions_views.py)."""

from __future__ import annotations

from agent_bridge.worktree_sessions_views import lineage_fields


def test_lineage_fields_forwards_full_envelope():
    envelope = {
        "head_revision": 4,
        "handoffs": [
            {"ordinal": 1, "predecessor": "s1", "candidate": "s2"},
            {"ordinal": 2, "predecessor": "s2", "candidate": "s3"},
        ],
        "controller_revision": 1,
        "controllers": [{"controller_session_id": "s0"}],
        "controller_findings": [
            {"status": "resolved", "lineage": ["s0", "s1", "s2", "s3"]},
        ],
    }
    fields = lineage_fields(envelope)
    assert fields["head_revision"] == 4
    assert len(fields["handoffs"]) == 2
    assert fields["controller_revision"] == 1
    assert fields["controllers"][0]["controller_session_id"] == "s0"
    assert fields["controller_findings"][0]["lineage"] == ["s0", "s1", "s2", "s3"]


def test_lineage_fields_empty_envelope_defaults():
    fields = lineage_fields({})
    assert fields == {
        "head_revision": 0,
        "handoffs": [],
        "controller_revision": 0,
        "controllers": [],
        "controller_findings": [],
    }


def test_lineage_fields_wrong_types_never_raise():
    """A malformed envelope (wrong field types) falls back to defaults
    rather than raising or propagating garbage."""
    envelope = {
        "head_revision": "not-an-int",
        "handoffs": "not-a-list",
        "controller_revision": None,
        "controllers": {"not": "a-list"},
        "controller_findings": 42,
    }
    fields = lineage_fields(envelope)
    assert fields == {
        "head_revision": 0,
        "handoffs": [],
        "controller_revision": 0,
        "controllers": [],
        "controller_findings": [],
    }
