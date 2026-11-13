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
    before_versions = dict(tracking_write._VERB_VERSIONS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)
    tracking_write._VERB_VERSIONS.clear()
    tracking_write._VERB_VERSIONS.update(before_versions)


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


@pytest.mark.parametrize("verb,evidence", [
    ("follow_up_resolve", {"result_ref": "https://example.com/results/accepted"}),
    ("follow_up_dismiss", {"reason": "Transferred to a tracked objective"}),
])
def test_legacy_settlement_preserves_independent_obligations_and_is_idempotent(
    record_path, monkeypatch, verb, evidence,
):
    from agent_worktrees import tracking_disposition_write

    record = tracking.load_record(record_path)
    record.kind = "bridge"
    record.status = "completed"
    record.summary = "Retain the original objective"
    record.follow_up = True
    tracking.add_follow_up(record, "Still needs implementation", save=False)
    tracking.save_record(record, record_path)
    before = tracking.load_record(record_path)
    events = []
    monkeypatch.setattr(tracking_followup_write.activity, "log_event",
                        lambda *a, **k: events.append((a, k)))
    args = {
        "worktree_id": record.worktree_id, "yaml_path": str(record_path),
        "follow_up_id": "legacy", **evidence,
    }
    apply = tracking_write._VERBS[verb]
    result = apply(args)
    saved = tracking.load_record(record_path)
    assert result["ok"] is True
    assert saved.follow_up is False
    assert saved.follow_ups[0] == before.follow_ups[0]
    assert tracking.effective_open_follow_up_count(saved) == 1
    assert (saved.status, saved.summary, saved.sessions, saved.resources) == (
        before.status, before.summary, before.sessions, before.resources,
    )
    assert len(events) == 1 and events[0][1]["follow_up_id"] == "legacy"
    unchanged = record_path.read_bytes()
    assert apply(args) == result
    assert record_path.read_bytes() == unchanged and len(events) == 1
    assert tracking_disposition_write.apply_status_disposition({
        "worktree_id": record.worktree_id, "yaml_path": str(record_path),
        "summary": "A late writer must not change this", "follow_up": False,
    })["error"] == "terminal_managed"
    assert record_path.read_bytes() == unchanged


@pytest.mark.parametrize("blocker", ["effort", "finalizing", "orphaned", "evidence", "absent"])
def test_legacy_refusal_does_not_mutate_record(record_path, blocker):
    from agent_worktrees.effort_focus import ActiveEffort

    record = tracking.load_record(record_path)
    record.follow_up = blocker != "absent"
    if blocker == "effort":
        record.active_effort = ActiveEffort("efforts/active/example/README.md", "owner", "slice")
    elif blocker in {"finalizing", "orphaned"}:
        record.status = blocker
    tracking.save_record(record, record_path)
    before = record_path.read_bytes()
    result = tracking_followup_write.apply_follow_up_resolve({
        "worktree_id": record.worktree_id, "yaml_path": str(record_path),
        "follow_up_id": "legacy",
        "result_ref": "" if blocker == "evidence" else "https://example.com/result",
    })
    assert result["error"] == ("not_found" if blocker == "absent" else "legacy_refused")
    assert record_path.read_bytes() == before


@pytest.mark.parametrize("provider_version", [1, 2])
def test_legacy_dispatch_negotiates_running_provider_before_writing(
    record_path, provider_version,
):
    record = tracking.load_record(record_path)
    record.follow_up = True
    tracking.save_record(record, record_path)
    served = []

    def compute(kind, payload):
        served.append(payload["verb"])
        return tracking_write.compute(kind, payload)

    server = tracking_write.start_server(compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        lock_data["tracking_write_verb_versions"]["follow_up_resolve"] = provider_version
        result = tracking_write.dispatch(
            "follow_up_resolve",
            {
                "worktree_id": record.worktree_id, "yaml_path": str(record_path),
                "follow_up_id": "legacy", "result_ref": "https://example.com/result",
            },
            read_lock_data=lambda: lock_data, ensure_monitor=None,
            min_version=2, boot_wait_s=0,
        )
    finally:
        server.close()
    assert result["follow_up"]["state"] == "resolved"
    assert served == (["follow_up_resolve"] if provider_version == 2 else [])
    assert tracking.load_record(record_path).follow_up is False


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
