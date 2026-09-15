"""Tests for the Mux Companion's pure rendering/resolution logic
(visions/mux-companion). v1 is view-only; these tests cover status
explanation text and current-worktree resolution, not any Textual rendering.
"""

from __future__ import annotations

from worktree_manager import engine_client as ec
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
