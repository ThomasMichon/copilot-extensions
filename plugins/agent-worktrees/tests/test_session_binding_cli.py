"""Focused CLI coverage for the public session-binding query."""

from __future__ import annotations

import json

from agent_worktrees import __main__ as m


def test_session_binding_found(monkeypatch, capfd):
    monkeypatch.setattr(
        m.sessions,
        "mux_binding_for_session",
        lambda sid: {
            "worktree_id": "wt-example",
            "session_name": "wt-wt-example",
            "pane_id": "%4",
            "pane_pid": 100,
            "pane_start_time": "pane-start",
            "copilot_pid": 200,
            "copilot_start_time": "copilot-start",
        },
    )

    assert m.main([
        "session-binding", "--session-id", "session-1", "--json",
    ]) == 0
    assert json.loads(capfd.readouterr().out) == {
        "version": 1,
        "found": True,
        "session_id": "session-1",
        "worktree_id": "wt-example",
        "mux_session": "wt-wt-example",
        "pane_id": "%4",
        "pane_pid": 100,
        "pane_start_time": "pane-start",
        "copilot_pid": 200,
        "copilot_start_time": "copilot-start",
    }


def test_session_binding_not_found(monkeypatch, capfd):
    monkeypatch.setattr(
        m.sessions, "mux_binding_for_session", lambda sid: None,
    )

    assert m.main([
        "session-binding", "--session-id", "missing", "--json",
    ]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["found"] is False
    assert result["session_id"] == "missing"
    assert all(
        result[key] is None
        for key in (
            "worktree_id", "mux_session", "pane_id", "pane_pid",
            "pane_start_time", "copilot_pid", "copilot_start_time",
        )
    )
