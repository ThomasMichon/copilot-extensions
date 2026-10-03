"""Tests for the ``session_conclude``/``session_link_succession`` verbs
(agent-worktrees-authoritative-daemon effort, Phase 3's fourth migrated
call-site cluster).

Covers the verb functions directly (the lifecycle-error rejections, the
head-clearing/moving behavior) and one end-to-end proof that
``tracking_write.dispatch`` reaches the exact same functions via a real,
live daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import tracking_session_lifecycle_write, tracking_write
from agent_worktrees.tracking import SessionEntry, WorktreeRecord, load_record, save_record


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path) -> Path:
    path = tmp_tracking_dir / "wt-1.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-1",
        branch="worktree/wt-1",
        worktree_path="/tmp/wt-1",
        repo="test-repo",
        machine="test",
        platform="wsl",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[SessionEntry("solo", "2026-01-01T00:00:00")],
    )
    rec.head_session = "solo"
    save_record(rec, path)
    return path


def test_importing_the_module_registers_both_verbs():
    verbs = tracking_write.registered_verbs()
    assert "session_conclude" in verbs
    assert "session_link_succession" in verbs


def test_conclude_hands_off_and_clears_head(record_path):
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "handed-off",
        }
    )
    assert result["ok"] is True
    assert result["state"] == "handed-off"
    assert result["head_session"] is None
    record = load_record(record_path)
    assert record.session_entry("solo").state == "handed-off"


def test_conclude_rejects_an_unknown_session(record_path):
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "ghost",
            "state": "handed-off",
        }
    )
    assert result["error"] == "lifecycle"
    # Refused -- untouched.
    assert load_record(record_path).session_entry("solo").state == "active"


def test_conclude_only_if_active_no_ops_an_already_concluded_session(record_path):
    """The ``only_if_active`` guard (added for ``handoff_cutover.py``'s
    ``_conclude_retired_predecessor`` repair) must never re-process a
    session whose ``SessionEntry.state`` isn't ``"active"`` -- a repair
    that raced behind some other transition must leave it alone."""
    tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "handed-off",
        }
    )
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "concluded",
            "only_if_active": True,
        }
    )
    assert result == {"ok": True, "skipped": "not_active"}
    assert load_record(record_path).session_entry("solo").state == "handed-off"


def test_conclude_only_if_active_still_concludes_an_active_session(record_path):
    """Confirms the guard is opt-in only and does not block a genuine
    concluding of a currently-``active`` session."""
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "concluded",
            "only_if_active": True,
        }
    )
    assert result["ok"] is True
    assert result["state"] == "concluded"
    assert load_record(record_path).session_entry("solo").state == "concluded"


def test_link_succession_writes_two_way_chain_and_moves_head(record_path):
    record = load_record(record_path)
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    save_record(record, record_path)

    result = tracking_session_lifecycle_write.apply_session_link_succession(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "predecessor": "solo",
            "successor": "new",
            "predecessor_state": "handed-off",
        }
    )
    assert result["ok"] is True
    assert result["head_session"] == "new"
    assert result["predecessor_state"] == "handed-off"
    record = load_record(record_path)
    assert record.session_entry("solo").successor == "new"
    assert record.session_entry("new").predecessor == "solo"
    assert record.head_session == "new"


def test_link_succession_rejects_an_unknown_session(record_path):
    result = tracking_session_lifecycle_write.apply_session_link_succession(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "predecessor": "solo",
            "successor": "ghost",
            "predecessor_state": "handed-off",
        }
    )
    assert result["error"] == "lifecycle"


def test_link_succession_feeds_claim_history_once_for_an_explicit_token_retry(
    record_path,
):
    """worktree-claims-transitive-finalization Phase 3b: a retried call with
    the SAME explicit ``handoff_token`` that's already linked to this exact
    successor is an idempotent replay -- ownership did not change a second
    time, so it must not append a second "reassigned" entry."""
    from agent_worktrees import claim_history, obligations
    from agent_worktrees.tracking import ResourceClaim

    record = load_record(record_path)
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    args = {
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "handed-off",
        "handoff_token": "token-1",
    }
    result1 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_link_succession_feeds_claim_history_once_for_a_concluded_retry(
    record_path,
):
    """The ``concluded`` (or any non-``handed-off``) ``predecessor_state``
    takes `link_succession`'s direct branch, which never touches
    ``record.handoffs`` at all -- idempotency there must be detected from
    the succession link itself already being in place, not a handoff
    token, or a retried call with the identical args duplicates the
    "reassigned" entry even though ownership did not change again."""
    from agent_worktrees import claim_history, obligations
    from agent_worktrees.tracking import ResourceClaim

    record = load_record(record_path)
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    args = {
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "concluded",
        "handoff_token": "token-1",
    }
    result1 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_link_succession_does_not_duplicate_on_a_predecessor_state_repair(
    record_path,
):
    """A second call repairing only the predecessor's recorded state (e.g.
    correcting ``handed-off`` to ``concluded``) for the SAME, already-settled
    predecessor/successor link must not count as a fresh reassignment -- the
    acting successor never changed, only predecessor metadata did."""
    from agent_worktrees import claim_history, obligations
    from agent_worktrees.tracking import ResourceClaim

    record = load_record(record_path)
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    result1 = tracking_session_lifecycle_write.apply_session_link_succession({
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "handed-off",
    })
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession({
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "concluded",
    })
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_dispatch_reaches_session_conclude_through_a_live_daemon(record_path):
    """End-to-end: proves ``tracking_write.dispatch`` reaches
    ``apply_session_conclude`` via an actual ``CoalescingServer``, the
    two-process-shaped path real production traffic will use."""
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        result = tracking_write.dispatch(
            "session_conclude",
            {
                "worktree_id": "wt-1",
                "yaml_path": str(record_path),
                "session_id": "solo",
                "state": "concluded",
            },
            read_lock_data=lambda: lock_data,
            ensure_monitor=None,
        )
    finally:
        server.close()
    assert result["ok"] is True
    record = load_record(record_path)
    assert record.session_entry("solo").state == "concluded"
