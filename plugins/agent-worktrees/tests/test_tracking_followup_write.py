"""Tests for the ``follow_up_add``/``follow_up_resolve``/``follow_up_dismiss``
verbs (agent-worktrees-authoritative-daemon effort, Phase 3's second
migrated call-site cluster).

Covers the verb functions directly (the frozen-owner rejection, the
reopen-on-add path, the not-found rejections) and one end-to-end proof that
``tracking_write.dispatch`` reaches the exact same functions via a real,
live daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import tracking, tracking_followup_write, tracking_write


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path) -> Path:
    path = tmp_tracking_dir / "wt-fu.yaml"
    tracking.create_new_record(
        "wt-fu", "worktree/wt-fu", "/tmp/wt-fu", "example",
        "machine", "wsl", tmp_tracking_dir,
    )
    return path


def test_importing_the_module_registers_all_three_verbs():
    verbs = tracking_write.registered_verbs()
    assert "follow_up_add" in verbs
    assert "follow_up_resolve" in verbs
    assert "follow_up_dismiss" in verbs


def test_add_journals_an_open_item(record_path):
    result = tracking_followup_write.apply_follow_up_add(
        {"worktree_id": "wt-fu", "yaml_path": str(record_path), "summary": "do the thing"}
    )
    assert result["ok"] is True
    assert result["follow_up"]["summary"] == "do the thing"
    assert result["follow_up"]["state"] == "open"
    assert result["reopened"] is False
    record = tracking.load_record(record_path)
    assert len(record.follow_ups) == 1


def test_add_with_refs(record_path):
    result = tracking_followup_write.apply_follow_up_add(
        {
            "worktree_id": "wt-fu",
            "yaml_path": str(record_path),
            "summary": "fix bug",
            "refs": [{"kind": "issue", "ref": "org/repo#9"}],
        }
    )
    assert result["follow_up"]["refs"] == [{"kind": "issue", "ref": "org/repo#9"}]


def test_add_reopens_a_finalized_owner(record_path):
    record = tracking.load_record(record_path)
    record.status = "finalized"
    tracking.save_record(record, record_path)

    result = tracking_followup_write.apply_follow_up_add(
        {"worktree_id": "wt-fu", "yaml_path": str(record_path), "summary": "resume work"}
    )
    assert result["reopened"] is True
    assert tracking.load_record(record_path).status == "active"


def test_add_rejects_a_finalizing_owner(record_path):
    record = tracking.load_record(record_path)
    record.status = "finalizing"
    tracking.save_record(record, record_path)

    result = tracking_followup_write.apply_follow_up_add(
        {"worktree_id": "wt-fu", "yaml_path": str(record_path), "summary": "x"}
    )
    assert result["error"] == "frozen"
    assert "frozen" in result["message"]
    # Refused -- no follow-up should have been journaled.
    assert tracking.load_record(record_path).follow_ups == []


def test_resolve_marks_an_item_resolved_with_a_result_ref(record_path):
    added = tracking_followup_write.apply_follow_up_add(
        {"worktree_id": "wt-fu", "yaml_path": str(record_path), "summary": "item one"}
    )
    fu_id = added["follow_up"]["id"]

    result = tracking_followup_write.apply_follow_up_resolve(
        {
            "worktree_id": "wt-fu",
            "yaml_path": str(record_path),
            "follow_up_id": fu_id,
            "result_ref": "org/repo#PR",
        }
    )
    assert result["ok"] is True
    assert result["follow_up"]["state"] == "resolved"
    assert result["follow_up"]["result_ref"] == "org/repo#PR"


def test_resolve_rejects_an_unknown_id(record_path):
    result = tracking_followup_write.apply_follow_up_resolve(
        {"worktree_id": "wt-fu", "yaml_path": str(record_path), "follow_up_id": "fu-nope"}
    )
    assert result == {"error": "not_found"}


def test_dismiss_marks_an_item_dismissed_with_a_reason(record_path):
    added = tracking_followup_write.apply_follow_up_add(
        {"worktree_id": "wt-fu", "yaml_path": str(record_path), "summary": "item two"}
    )
    fu_id = added["follow_up"]["id"]

    result = tracking_followup_write.apply_follow_up_dismiss(
        {
            "worktree_id": "wt-fu",
            "yaml_path": str(record_path),
            "follow_up_id": fu_id,
            "reason": "not needed",
        }
    )
    assert result["ok"] is True
    assert result["follow_up"]["state"] == "dismissed"
    assert result["follow_up"]["reason"] == "not needed"


def test_dismiss_rejects_an_unknown_id(record_path):
    result = tracking_followup_write.apply_follow_up_dismiss(
        {
            "worktree_id": "wt-fu",
            "yaml_path": str(record_path),
            "follow_up_id": "fu-nope",
            "reason": "n/a",
        }
    )
    assert result == {"error": "not_found"}


def test_dispatch_reaches_follow_up_add_through_a_live_daemon(record_path):
    """End-to-end: proves ``tracking_write.dispatch`` reaches
    ``apply_follow_up_add`` via an actual ``CoalescingServer``, the
    two-process-shaped path real production traffic will use."""
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        result = tracking_write.dispatch(
            "follow_up_add",
            {"worktree_id": "wt-fu", "yaml_path": str(record_path), "summary": "via daemon"},
            read_lock_data=lambda: lock_data,
            ensure_monitor=None,
        )
    finally:
        server.close()
    assert result["ok"] is True
    assert result["follow_up"]["summary"] == "via daemon"
    record = tracking.load_record(record_path)
    assert len(record.follow_ups) == 1
