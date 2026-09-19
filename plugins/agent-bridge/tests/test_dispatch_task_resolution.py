"""Tests for the pure dispatch-task session-ranking helper.

*resolve-by-any-origin-reference* (``visions/plugins/agent-bridge``).
"""

from __future__ import annotations

from agent_bridge.dispatch_task_resolution import (
    candidate_session_ids,
    task_worktree_id,
)


def test_current_owner_session_tried_first():
    task = {"owner_session_id": "session-current"}
    attachments = [
        {"session_id": "session-old-2", "detached_at": 200.0},
        {"session_id": "session-old-1", "detached_at": 100.0},
    ]
    assert candidate_session_ids(task, attachments) == [
        "session-current",
        "session-old-2",
        "session-old-1",
    ]


def test_no_current_owner_falls_through_to_history():
    """A released task has no current owner -- history is all there is."""
    task = {"owner_session_id": None}
    attachments = [
        {"session_id": "session-b", "detached_at": 50.0},
        {"session_id": "session-a", "detached_at": 10.0},
    ]
    assert candidate_session_ids(task, attachments) == ["session-b", "session-a"]


def test_current_owner_deduped_against_its_own_open_history_row():
    """The still-open history row for the current owner is the SAME session
    -- it must not be tried twice."""
    task = {"owner_session_id": "session-current"}
    attachments = [
        {"session_id": "session-current", "detached_at": None},
        {"session_id": "session-old", "detached_at": 10.0},
    ]
    assert candidate_session_ids(task, attachments) == ["session-current", "session-old"]


def test_empty_task_and_history_yields_no_candidates():
    assert candidate_session_ids({}, []) == []


def test_malformed_entries_are_skipped_not_fatal():
    task = {"owner_session_id": 12345}  # wrong type -- never a real session id
    attachments = [
        {"session_id": None},
        {"not_a_session_field": "x"},
        "not even a dict",
        {"session_id": "session-real"},
    ]
    assert candidate_session_ids(task, attachments) == ["session-real"]


def test_task_worktree_id_reads_target_worktree():
    assert task_worktree_id({"target_worktree": "wt-1"}) == "wt-1"
    assert task_worktree_id({"target_worktree": None}) is None
    assert task_worktree_id({}) is None
    assert task_worktree_id({"target_worktree": 42}) is None
