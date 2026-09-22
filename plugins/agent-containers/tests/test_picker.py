"""Tests for `agent_containers.picker` (picker-venue-pivots Phase 2)."""

from __future__ import annotations

from agent_containers.picker import (
    activity_from_live_session,
    claims_summary_for_worktree,
    live_session_for_venue,
    picker_fields,
    sess_column,
    subtitle_for,
)


def test_subtitle_for_claimed_vs_free():
    assert subtitle_for("box-1", "3bac") == "claimed by 3bac"
    assert subtitle_for("box-1", None) == ""


def test_claims_summary_for_worktree_degrades_without_agent_worktrees():
    """agent-containers has no hard dependency on agent-worktrees; the
    lookup must degrade to `""` rather than raise."""
    assert claims_summary_for_worktree(None) == ""
    assert claims_summary_for_worktree("some-worktree-id") == ""


def test_live_session_for_venue_degrades_without_agent_bridge():
    """agent-containers has no hard dependency on agent-bridge either."""
    assert live_session_for_venue("container", "box-1") is None
    assert live_session_for_venue("container", "") is None


def test_sess_column_vocabulary():
    assert sess_column({"liveness": "active"}, "3bac") == "LIVE"
    assert sess_column({"liveness": "stalled"}, "3bac") == "LIVE"
    assert sess_column({"liveness": "idle"}, "3bac") == "IDLE"
    assert sess_column(None, "3bac") == "IDLE"
    assert sess_column(None, None) == ""


def test_activity_from_live_session_composes_phase_and_summary():
    assert activity_from_live_session(None) == ""
    assert activity_from_live_session({"latest_progress": None}) == ""
    assert (
        activity_from_live_session({"latest_progress": {"summary": "spinning up"}})
        == "spinning up"
    )
    assert (
        activity_from_live_session(
            {"latest_progress": {"phase": "setup", "summary": "spinning up"}}
        )
        == "setup: spinning up"
    )


def test_picker_fields_shape_when_unclaimed():
    assert picker_fields("free-1", None) == {
        "subtitle": "", "claims_summary": "", "sess": "",
    }


def test_picker_fields_shape_when_claimed(monkeypatch):
    import agent_containers.picker as picker

    monkeypatch.setattr(picker, "live_session_for_venue", lambda kind, target: None)
    monkeypatch.setattr(picker, "claims_summary_for_worktree", lambda wt: "PR #2481")
    fields = picker_fields("box-1", "3bac")
    assert fields["subtitle"] == "claimed by 3bac"
    assert fields["claims_summary"] == "PR #2481"
    assert fields["sess"] == "IDLE"


def test_picker_fields_appends_live_activity(monkeypatch):
    import agent_containers.picker as picker

    monkeypatch.setattr(
        picker,
        "live_session_for_venue",
        lambda kind, target: {
            "liveness": "active",
            "latest_progress": {"phase": "impl", "summary": "wiring"},
        },
    )
    fields = picker_fields("box-1", "3bac")
    assert fields["subtitle"] == "claimed by 3bac - impl: wiring"
    assert fields["sess"] == "LIVE"


def test_picker_fields_activity_without_claim_uses_container_name(monkeypatch):
    import agent_containers.picker as picker

    monkeypatch.setattr(
        picker,
        "live_session_for_venue",
        lambda kind, target: {
            "liveness": "active",
            "latest_progress": {"summary": "spinning up"},
        },
    )
    fields = picker_fields("box-1", None)
    assert fields["subtitle"] == "box-1 - spinning up"
