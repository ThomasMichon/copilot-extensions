"""Tests for `agent_containers.picker` (picker-venue-pivots Phase 2)."""

from __future__ import annotations

from agent_containers.picker import (
    activity_from_live_session,
    claims_summary_for_worktree,
    driving_worktree_id_for,
    live_session_for_venue,
    picker_fields,
    sess_column,
    subtitle_for,
    worktree_status_for_worktree,
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
        "subtitle": "",
        "activity": "",
        "session_id": "",
        "claims_summary": "",
        "sess": "",
        "worktree_id": "",
        "has_driving_worktree": "false",
        "worktree_status": {
            "title": "Worktree status unavailable",
            "status": "unknown",
            "link": None,
            "body": "No tracked driving worktree is recorded for this container.",
        },
    }


def test_picker_fields_shape_when_claimed(monkeypatch):
    import agent_containers.picker as picker

    monkeypatch.setattr(picker, "live_session_for_venue", lambda kind, target: None)
    monkeypatch.setattr(picker, "claims_summary_for_worktree", lambda wt: "PR #2481")
    monkeypatch.setattr(picker, "driving_worktree_id_for", lambda wt: "host-win-20260922-111111-a1c4")
    monkeypatch.setattr(
        picker,
        "worktree_status_for_worktree",
        lambda wt: {"title": "Worktree a1c4", "status": "active", "link": None, "body": "- Claims: PR #2481"},
    )
    fields = picker_fields("box-1", "3bac")
    assert fields["subtitle"] == "→ claimed by 3bac"
    assert fields["claims_summary"] == "PR #2481"
    assert fields["sess"] == "IDLE"
    assert fields["worktree_id"] == "host-win-20260922-111111-a1c4"
    assert fields["has_driving_worktree"] == "true"
    assert fields["worktree_status"]["title"] == "Worktree a1c4"


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
    monkeypatch.setattr(picker, "driving_worktree_id_for", lambda wt: "host-win-20260922-111111-a1c4")
    fields = picker_fields("box-1", "3bac")
    assert fields["subtitle"] == "→ claimed by 3bac - impl: wiring"
    assert fields["activity"] == "impl: wiring"
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


def test_driving_worktree_id_for_degrades_without_agent_worktrees():
    assert driving_worktree_id_for(None) == ""
    assert driving_worktree_id_for("maybe-effort") == ""


def test_worktree_status_for_worktree_unavailable_when_unresolvable():
    payload = worktree_status_for_worktree("maybe-effort")
    assert payload["status"] == "unknown"
    assert "No tracked driving worktree" in payload["body"]

def test_detached_worker_row_resolves_its_supervising_worktree(monkeypatch):
    import agent_containers.picker as picker

    monkeypatch.setattr(picker, "live_session_for_venue", lambda kind, target: {
        "liveness": "active",
        "latest_progress": {"summary": "building"},
        "session_id": "sid-1",
        "venue": {"kind": "container", "target": "box-1",
                  "supervisor_ref": "host/example-harness/host-win-20260925-120000-ab12#sess"},
    })
    monkeypatch.setattr(picker, "driving_worktree_id_for", lambda wt: "")
    fields = picker_fields("box-1", None)
    assert fields["worktree_id"] == "host-win-20260925-120000-ab12"
    assert fields["has_driving_worktree"] == "true"
    assert fields["session_id"] == "sid-1"


def test_supervising_worktree_id_needs_a_qualified_ref():
    from agent_containers.picker import supervising_worktree_id

    assert supervising_worktree_id(None) == ""
    assert supervising_worktree_id({"venue": {"supervisor_ref": "wt-only"}}) == ""
    assert supervising_worktree_id({"venue": {"supervisor_ref": "h/p/wt-1#s"}}) == "wt-1"
