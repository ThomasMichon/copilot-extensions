"""Handleless receipt-native retirement survives an identical late release."""

from __future__ import annotations

import pytest

from agent_dispatch import acquisition_receipts
from agent_dispatch.ordinary_native_bindings import is_native_retired
from agent_dispatch.queue import SpawnReservation, Task, TaskError, TaskQueue
from agent_dispatch.queue_pool_placement import (
    require_pool_recovery_retirement, reservation_accountable,
)
from tests import test_pool_placement as placement_controls
from tests.test_pool_native_receipts import authority, receipt_for
from tests.test_pool_placement import place, pool, publish, task

q = placement_controls.q


def retire_handleless(
    q: TaskQueue, *, disposition: str = "failed", historical: bool = False,
) -> tuple[Task, SpawnReservation]:
    publish(q, pool())
    requested = task(q, exclusive_key="shared", source="test-producer")
    original, _ = place(q, requested)
    receipt = receipt_for(q, requested, original, native="receipt-native")
    acquisition_receipts.bind(q, receipt, evidence=authority(receipt))
    if historical:
        q.fail_spawn(original.key)
    q.request_spawn_release(
        original.key, disposition=disposition, finished_startup_revision=1,
    )
    retired = q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    assert retired.session_handle is None and not retired.native_release_pending
    return requested, retired


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("disposition", ["failed", "settled"])
@pytest.mark.parametrize("include_worktree", [False, True])
def test_same_receipt_native_release_keeps_qualified_retirement_and_free_capacity(
    q: TaskQueue, historical: bool, disposition: str, include_worktree: bool,
) -> None:
    requested, retired = retire_handleless(q, disposition=disposition, historical=historical)
    before = q.get(requested.id)
    assignment = q.task_pool_assignment(requested.id)
    for now in (101.0, 102.0):
        replay = q.request_spawn_release(
            retired.key, session_handle="receipt-native",
            worktree="worktree" if include_worktree else None,
            disposition=disposition, finished_startup_revision=1, now=now,
        )
        assert replay.state == retired.state
        assert replay.session_handle == "receipt-native"
        assert replay.release_requested and not replay.native_release_pending
        assert replay.conversation_retired == retired.conversation_retired
        assert replay.conclusion_state == retired.conclusion_state
        with q._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (retired.key,),
            ).fetchone()
            assert is_native_retired(row) and not reservation_accountable(conn, row)
            require_pool_recovery_retirement(conn, q.get(requested.id))
    assert q.get(requested.id) == before
    assert q.task_pool_assignment(requested.id) == assignment
    replacement, reserved = place(q, task(q, exclusive_key="shared"))
    assert reserved and replacement.key != retired.key
    assert len(q.list_reservations(task_id=requested.id)) == 1


@pytest.mark.parametrize("change", ["native", "worktree"])
def test_different_receipt_release_identity_refuses_without_mutation(
    q: TaskQueue, change: str,
) -> None:
    requested, retired = retire_handleless(q)
    before = q.get(requested.id)
    assignment = q.task_pool_assignment(requested.id)
    with pytest.raises(TaskError, match="native identity|worktree"):
        q.request_spawn_release(
            retired.key, session_handle="other-native" if change == "native" else "receipt-native",
            worktree="other-worktree" if change == "worktree" else "worktree",
        )
    assert q.get_reservation(retired.key) == retired
    assert q.get(requested.id) == before
    assert q.task_pool_assignment(requested.id) == assignment


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_receipt_release_replay_preserves_independent_transport_custody(
    q: TaskQueue, state: str,
) -> None:
    requested, retired = retire_handleless(q)
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO execution_send_custody"
            " (scope_key, send_id, repo, source, producer_generation, task_id, reservation_key,"
            " task_generation, kind, identity, state, detail, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scope", "send", requested.repo, requested.source, 1, requested.id, retired.key,
             q.get(requested.id).generation, "launch", "{}", state, "unresolved", 100, 100),
        )
    replay = q.request_spawn_release(retired.key, session_handle="receipt-native")
    assert not replay.native_release_pending
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with q._connect() as conn:
        assert conn.execute(
            "SELECT state FROM execution_send_custody WHERE send_id = 'send'",
        ).fetchone()["state"] == state
        conn.execute("UPDATE execution_send_custody SET state = 'not_sent' WHERE send_id = 'send'")
    assert place(q, task(q, exclusive_key="shared"))[1]
