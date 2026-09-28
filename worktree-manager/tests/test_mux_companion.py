"""Tests for the Mux Companion (visions/mux-companion). Most cover pure
rendering/resolution logic (status explanation text, current-worktree
resolution, action-message text) without any Textual rendering; one
end-to-end test (mux-companion-manual-cutover-diagnostics, #4369) drives a
real compose/mount/button-press cycle via ``run_test()`` to verify the
"Cut over"/"Refresh" wiring itself, not just the underlying pure functions.
"""

from __future__ import annotations

import asyncio

from worktree_manager import engine_client as ec
from worktree_manager import handoff_client as hc
from worktree_manager import mux_companion as companion


def test_closure_explanation_final():
    row = {
        "state": "completed",
        "closure": {"label": "FINAL", "evidence_mode": "refreshed", "blockers": []},
    }
    lines = companion._closure_explanation(row)
    assert lines[0].startswith("FINAL")


def test_closure_explanation_merged_lists_blockers_and_cached_evidence():
    row = {
        "state": "completed",
        "closure": {
            "label": "MERGED",
            "evidence_mode": "cached",
            "blockers": [
                {"code": "held-claims", "count": 2},
                {"code": "open-follow-ups", "count": 1},
            ],
        },
    }
    lines = companion._closure_explanation(row)
    joined = "\n".join(lines)
    assert joined.startswith("MERGED")
    assert "cached/fetch-free" in joined
    assert "held resource claims (2)" in joined
    follow_up_line = next(line for line in lines if "open follow-ups" in line)
    assert "(1)" not in follow_up_line


def test_closure_explanation_merged_without_descriptor_falls_back():
    row = {"state": "completed", "status": "finalized"}
    lines = companion._closure_explanation(row)
    assert lines[0].startswith("MERGED")


def test_closure_explanation_plain_state():
    row = {"state": "wip"}
    lines = companion._closure_explanation(row)
    assert "not yet on the default branch" in lines[0]


def test_closure_explanation_includes_sync_tag():
    row = {"state": "wip", "ahead": 2, "behind": 1}
    lines = companion._closure_explanation(row)
    assert any("2 ahead" in line and "1 behind" in line for line in lines)


def test_fallback_label_unknown_state():
    assert companion._fallback_label({}) == "UNKNOWN"


def test_load_current_worktree_reports_untracked_path(monkeypatch):
    monkeypatch.setattr(
        ec, "current_worktree_status",
        lambda **k: {"version": 1, "error": "not a tracked git worktree"},
    )
    data = companion._load_current_worktree("/nowhere")
    assert data.error == "not a tracked git worktree"
    assert data.worktree == {}
    assert data.sessions == []


def test_load_current_worktree_surfaces_engine_error(monkeypatch):
    def _raise(**kwargs):
        raise ec.EngineError("the agent-worktrees engine is not installed")

    monkeypatch.setattr(ec, "current_worktree_status", _raise)
    data = companion._load_current_worktree("/nowhere")
    assert data.error == "the agent-worktrees engine is not installed"


def test_load_current_worktree_degrades_when_sessions_unavailable(monkeypatch):
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)

    def _raise(*args, **kwargs):
        raise ec.EngineError("boom")

    monkeypatch.setattr(ec, "list_worktree_sessions", _raise)

    data = companion._load_current_worktree("/w")

    assert data.error is None
    assert data.worktree == payload
    assert data.sessions == []


def test_load_current_worktree_fetches_sessions_when_tracked(monkeypatch):
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "completed"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    session_rows = [{"id": "s1", "is_head": True}]
    monkeypatch.setattr(
        ec, "list_worktree_sessions", lambda *a, **k: session_rows
    )

    data = companion._load_current_worktree("/w")

    assert data.worktree == payload
    assert data.sessions == session_rows
    assert data.error is None


def test_load_current_worktree_skips_sessions_when_untracked(monkeypatch):
    payload = {"version": 1, "id": None, "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    calls = []
    monkeypatch.setattr(
        ec, "list_worktree_sessions",
        lambda *a, **k: calls.append(1) or [],
    )

    data = companion._load_current_worktree("/w")

    assert not calls  # no id -> never asks the engine for sessions
    assert data.sessions == []


def test_load_current_worktree_includes_pending_handoff(monkeypatch):
    """#3307 Phase 8: the resolved worktree path feeds the pending-handoff
    lookup, and its result surfaces on the loaded data."""
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    monkeypatch.setattr(ec, "list_worktree_sessions", lambda *a, **k: [])
    seen = {}

    def _fake_pending(project, worktree_path, **kwargs):
        seen["worktree_path"] = worktree_path
        return {"title": "Finish the retry-budget fix", "sessionId": "sess-1"}

    monkeypatch.setattr(hc, "pending_handoff", _fake_pending)

    data = companion._load_current_worktree("/w")

    assert seen["worktree_path"] == "/w"
    assert data.pending_handoff == {
        "title": "Finish the retry-budget fix", "sessionId": "sess-1"}


def test_load_current_worktree_degrades_when_pending_handoff_unavailable(monkeypatch):
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    monkeypatch.setattr(ec, "list_worktree_sessions", lambda *a, **k: [])

    def _raise(*a, **k):
        raise ec.EngineError("boom")

    monkeypatch.setattr(hc, "pending_handoff", _raise)

    data = companion._load_current_worktree("/w")

    assert data.error is None
    assert data.pending_handoff is None


def test_status_text_shows_pending_handoff_headline():
    """#3307 Phase 8: a pending handoff's title renders as a read-only,
    situational-awareness line -- never shown when there's no pending baton."""
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._action_message = None
    app._data = companion._CompanionData(
        worktree={"state": "wip"},
        pending_handoff={"title": "Finish the retry-budget fix"},
    )
    text = app._status_text().plain
    assert "Pending handoff: Finish the retry-budget fix" in text

    app._data = companion._CompanionData(worktree={"state": "wip"})
    assert "Pending handoff" not in app._status_text().plain


def test_status_text_includes_action_message_when_set():
    """#mux-companion-manual-cutover-diagnostics: a "Cut over"/"Refresh"
    outcome renders below the closure explanation, once set."""
    from rich.text import Text

    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._data = companion._CompanionData(worktree={"state": "wip"})
    app._action_message = Text("\u2713 Cut over: head old \u2192 new")
    assert "Cut over: head old" in app._status_text().plain


def test_cut_over_reports_change_and_updates_action_message(monkeypatch):
    """#mux-companion-manual-cutover-diagnostics: pressing "Cut over" shells
    out to handoff_client.trigger_cutover and reflects a real change."""
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(
        worktree={"id": "wt-ab12", "state": "wip"},
        pending_handoff={"title": "Finish the retry-budget fix"},
    )
    calls = []

    def _fake_trigger(project, worktree_id, **kwargs):
        calls.append(worktree_id)
        return {
            "worktree_id": worktree_id, "head_before": "old-sess",
            "head_after": "new-sess", "changed": True,
        }

    monkeypatch.setattr(hc, "trigger_cutover", _fake_trigger)
    monkeypatch.setattr(app, "_refresh_view", lambda **k: None)

    app._cut_over()

    assert calls == ["wt-ab12"]
    assert "old-sess" in app._action_message.plain
    assert "new-sess" in app._action_message.plain


def test_cut_over_reports_no_actionable_handoff(monkeypatch):
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(worktree={"id": "wt-ab12", "state": "wip"})

    monkeypatch.setattr(
        hc, "trigger_cutover",
        lambda project, worktree_id, **k: {"changed": False},
    )
    monkeypatch.setattr(app, "_refresh_view", lambda **k: None)

    app._cut_over()

    assert "no actionable pending handoff" in app._action_message.plain


def test_cut_over_surfaces_engine_error_visibly(monkeypatch):
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(worktree={"id": "wt-ab12", "state": "wip"})

    def _raise(project, worktree_id, **k):
        raise ec.EngineError("the agent-worktrees engine is not installed")

    monkeypatch.setattr(hc, "trigger_cutover", _raise)
    refreshed = []
    monkeypatch.setattr(app, "_refresh_view", lambda **k: refreshed.append(k))

    app._cut_over()

    assert "Cut over failed" in app._action_message.plain
    assert refreshed  # still repaints so the error is actually shown


def test_cut_over_is_a_no_op_without_a_resolved_worktree_id(monkeypatch):
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(worktree={"state": "wip"})  # no "id"
    calls = []
    monkeypatch.setattr(hc, "trigger_cutover", lambda *a, **k: calls.append(1))

    app._cut_over()

    assert not calls


def test_end_to_end_cut_over_button_press_updates_the_live_view(monkeypatch):
    """#mux-companion-manual-cutover-diagnostics: a real compose/mount/
    button-press cycle, not just the pure `_cut_over` function -- verifies
    the "Cut over" button is enabled exactly when a pending handoff exists,
    disabled after a successful cutover clears it, and the lineage table
    reflects the reloaded data."""
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: dict(payload))
    session_calls = {"n": 0}

    def _fake_sessions(*a, **k):
        session_calls["n"] += 1
        if session_calls["n"] == 1:
            return [{"id": "old-sess", "is_head": True, "state": "handed-off"}]
        return [
            {"id": "old-sess", "is_head": False, "state": "handed-off"},
            {"id": "new-sess", "is_head": True, "state": "active"},
        ]

    monkeypatch.setattr(ec, "list_worktree_sessions", _fake_sessions)
    pending_calls = {"n": 0}

    def _fake_pending(*a, **k):
        pending_calls["n"] += 1
        if pending_calls["n"] == 1:
            return {"title": "Finish the retry-budget fix"}
        return None  # the baton is consumed once cut over

    monkeypatch.setattr(hc, "pending_handoff", _fake_pending)
    monkeypatch.setattr(
        hc, "trigger_cutover",
        lambda project, worktree_id, **k: {
            "head_before": "old-sess", "head_after": "new-sess", "changed": True},
    )

    async def run():
        app = companion.MuxCompanionApp(cwd="/w")
        async with app.run_test() as pilot:
            from textual.widgets import Button, DataTable

            cutover_btn = app.query_one("#cutover-btn", Button)
            assert cutover_btn.disabled is False
            table = app.query_one("#lineage", DataTable)
            assert table.row_count == 1

            await pilot.click("#cutover-btn")
            await pilot.pause()

            assert "old-sess" in app._action_message.plain
            assert "new-sess" in app._action_message.plain
            table = app.query_one("#lineage", DataTable)
            assert table.row_count == 2
            assert app.query_one("#cutover-btn", Button).disabled is True

    asyncio.run(run())

