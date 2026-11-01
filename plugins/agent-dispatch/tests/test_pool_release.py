"""Pool retirement preserves historical native and explicit workspace custody."""

from __future__ import annotations

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_dispatch import tracking
from agent_dispatch.ordinary_native_bindings import is_native_retired
from agent_dispatch.queue import SpawnReservation, SpawnState, Status, TaskError, TaskQueue
from agent_dispatch.queue_execution_custody import (
    ExecutionSendUnresolved, require_execution_send_reconciliation, reservation_transport_accountable,
)
from agent_dispatch.queue_pool_placement import reservation_accountable
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, retired_predecessor_retry, task
from tests.test_supervisor import QueueBackedClient

q = placement_controls.q


def carried_retry(q, *, exclusive_key=None):
    publish(q, pool(capacity=2))
    t = task(q, exclusive_key=exclusive_key)
    old, _ = place(q, t)
    record_native(q, old)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(old.key)
    q.yield_task(t.id, "host-a/worktree")
    retry, _ = place(q, q.get(t.id))
    record_native(q, retry)
    return t, old, retry


@pytest.mark.parametrize("exclusive_key", [None, "shared"])
def test_historical_release_does_not_resurrect_assignment_or_drop_capacity(q, exclusive_key):
    t, old, retry = carried_retry(q, exclusive_key=exclusive_key)
    assignment = q.task_pool_assignment(t.id)
    released = q.request_spawn_release(old.key, finished_startup_revision=1)
    assert released.state == SpawnState.FAILED and released.native_release_pending
    assert not released.exclusive_released
    with q._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (old.key,)).fetchone()
        assert not is_native_retired(row)
    replay, reserved = place(q, q.get(t.id))
    assert replay.key == retry.key and not reserved
    assert q.task_pool_assignment(t.id) == assignment
    assert [r.key for r in q.list_reservations(task_id=t.id) if r.state in SpawnState.ACTIVE] == [retry.key]
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with pytest.raises(TaskError, match="pending retirement"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
    with pytest.raises(TaskError, match="pending retirement"):
        q.record_spawn(retry.key, session_handle="native")
    with pytest.raises(TaskError, match="carried workspace cleanup"):
        q.retire_spawn(old.key, exact_absence=True, conclusion_state="complete")
    assert q.get_reservation(old.key) == released
    retired = q.retire_spawn(old.key, exact_absence=True)
    assert retired.state == SpawnState.FAILED and not retired.native_release_pending
    assert retired.conversation_retired and retired.session_handle == "native"
    assert place(q, task(q))[1]
    with pytest.raises(TaskError, match="retired native"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
    assert q.task_pool_assignment(t.id) == assignment


@pytest.mark.parametrize("state", [
    SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.RELEASING,
    SpawnState.FAILED, SpawnState.SETTLED, SpawnState.DEFERRED, SpawnState.REARMED,
])
def test_release_cannot_overwrite_retained_native_even_for_terminal_pool(q, state):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    record_native(q, r)
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    if state == SpawnState.FAILED:
        q.fail_spawn(r.key)
    elif state == SpawnState.SETTLED:
        q.settle_spawn(r.key)
    elif state == SpawnState.RELEASING:
        q.request_spawn_release(r.key)
    else:
        with q._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET state=? WHERE key=?", (state, r.key))
    before = q.get_reservation(r.key)
    with pytest.raises(TaskError, match="retained native"):
        q.request_spawn_release(
            r.key, session_handle="different-native", detail="must roll back",
            finished_startup_revision=1,
        )
    assert q.get_reservation(r.key) == before
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_historical_release_does_not_apply_successors_changed_routing(q):
    t, old, retry = carried_retry(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET target_machine='other' WHERE id=?", (t.id,))
    released = q.request_spawn_release(old.key, session_handle="native")
    assert released.native_release_pending and released.session_handle == "native"
    assert q.task_pool_assignment(t.id).reservation_key == retry.key


def test_release_retains_native_when_workspace_response_was_not_recorded(q):
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    released = q.request_spawn_release(
        r.key, session_handle="late-native", worktree="returned-worktree",
        finished_startup_revision=1,
    )
    assert released.session_handle == "late-native" and released.worktree == "returned-worktree"
    with pytest.raises(TaskError, match="retained native"):
        q.request_spawn_release(r.key, session_handle="another-native")
    assert q.get_reservation(r.key) == released


def test_historical_release_and_current_replay_serialize_without_rebinding(q):
    t, old, retry = carried_retry(q, exclusive_key="shared")
    barrier = Barrier(2)

    def release():
        barrier.wait(timeout=5)
        return q.request_spawn_release(old.key, finished_startup_revision=1)

    def replay():
        barrier.wait(timeout=5)
        return place(q, q.get(t.id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        release_result = executor.submit(release)
        replay_result = executor.submit(replay)
        historical = release_result.result(timeout=10)
        current, reserved = replay_result.result(timeout=10)
    assert historical.native_release_pending and historical.state == SpawnState.FAILED
    assert current.key == retry.key and not reserved
    assert q.task_pool_assignment(t.id).reservation_key == retry.key
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("legacy_reserve", [False, True])
def test_spare_capacity_cannot_overtake_a_historical_sibling_exclusive_holder(q, pending, legacy_reserve):
    publish(q, pool(capacity=3))
    old_task = task(q, exclusive_key="shared")
    old, _ = place(q, old_task)
    record_native(q, old)
    q.record_spawn(old.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.fail_spawn(old.key)
    if pending:
        q.request_spawn_release(old.key)
    sibling = task(q, exclusive_key="shared")
    before = q.get_reservation(old.key)
    with pytest.raises(TaskError, match="accountable historical execution"):
        if legacy_reserve:
            q.reserve_spawn(sibling.id, reserved_by="legacy", driver="cli")
        else:
            place(q, sibling)
    assert q.get(sibling.id) == sibling
    assert q.task_pool_assignment(sibling.id) is None
    assert q.list_reservations(task_id=sibling.id) == []
    assert q.get_reservation(old.key) == before
    if not pending:
        q.request_spawn_release(old.key)
    q.retire_spawn(old.key, exact_absence=True)
    assert place(q, sibling)[1]


def test_pending_historical_retirement_blocks_a_fresh_same_task_attempt(q):
    publish(q, pool(capacity=3))
    t = task(q)
    old, _ = place(q, t)
    record_native(q, old)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(old.key)
    q.yield_task(t.id, "host-a/worktree")
    q.request_spawn_release(old.key, finished_startup_revision=1)
    before = q.get(t.id)
    with pytest.raises(TaskError, match="pending historical native retirement"):
        place(q, before)
    assert q.get(t.id) == before
    assert q.task_pool_assignment(t.id).reservation_key == old.key
    assert len(q.list_reservations(task_id=t.id)) == 1


@pytest.mark.parametrize("verdict", [tracking.LIVE, tracking.UNKNOWN, tracking.GONE])
def test_pre_acceptance_failure_probes_its_recorded_execution_host(q, verdict):
    publish(q, pool(capacity=2))
    t = task(q)
    old, _ = place(q, t)
    record_native(q, old)
    q.record_spawn(old.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.fail_spawn(old.key)
    q.request_spawn_release(old.key)
    assert q.get(t.id).owner is None and q.get(t.id).owner_session_id is None
    assert q.attachment_history(t.id) == []
    probes = []
    supervisor = Supervisor(
        QueueBackedClient(q), machine="observer", supervisor_id="allocator",
        spawn_fn=lambda _: pytest.fail("no spawn"),
        verdict_fn=lambda *args: probes.append(args) or verdict,
        attempt_conclusion_fn=lambda *args, **kw: pytest.fail("no workspace conclusion"),
    )
    assert supervisor.release_requested_bodies() == int(verdict == tracking.GONE)
    assert probes == [("worktree", "host-a", "native")]
    assert q.get_reservation(old.key).native_release_pending is (verdict != tracking.GONE)


@pytest.mark.parametrize("recorded", [None, "", 3])
def test_recorded_execution_host_preserves_legacy_fallback_but_rejects_invalid_values(q, recorded):
    t, old, _ = carried_retry(q)
    supervisor = Supervisor(QueueBackedClient(q), machine="observer", spawn_fn=lambda _: (False, {}))
    snapshot = {**asdict(q.get_reservation(old.key)), "execution_machine": recorded}
    assert supervisor._retained_cli_machine(snapshot, asdict(q.get(t.id))) == (
        "host-a" if recorded is None else None
    )


@pytest.mark.parametrize("terminal", [False, True])
def test_release_retains_a_late_handleless_replacement_without_retired_marker(q, terminal):
    _, _, retry = retired_predecessor_retry(q)
    q.record_spawn_worktree(retry.key, "worktree", ownership="reused",
                            execution_machine="host-a", driver="cli")
    if terminal:
        q.fail_spawn(retry.key)
    q.request_spawn_release(retry.key)
    released = q.request_spawn_release(
        retry.key, session_handle="replacement", finished_startup_revision=1,
    )
    assert released.session_handle == "replacement" and not released.conversation_retired
    assert released.release_requested and released.native_release_pending is terminal
    assert place(q, task(q))[1]
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.retire_spawn(retry.key, exact_absence=True)
    assert place(q, task(q))[1]


@pytest.mark.parametrize("selector", ["affinity", "explicit", "required"])
def test_unrelated_cleanup_preserves_required_worktrees_and_optional_affinity(q, selector):
    publish(q, pool(capacity=2))
    old_task = task(q, target_worktree="wanted")
    old, _ = place(q, old_task)
    record_native(q, old, worktree="wanted")
    q.record_spawn(old.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.fail_spawn(old.key, conclusion_state="pending")
    q.request_spawn_release(old.key)
    q.retire_spawn(old.key, exact_absence=True)
    before = q.get_reservation(old.key)
    options = {"target_worktree": "wanted"} if selector == "explicit" else {"affinity": {"worktree": "wanted"}}
    if selector == "required":
        options["requires"] = ["worktree:wanted"]
    t = task(q, **options)
    if selector != "affinity":
        with pytest.raises(TaskError, match="explicit target worktree|required worktree"):
            place(q, t)
        assert q.get(t.id) == t and q.get(t.id).status == Status.QUEUED
        assert q.task_pool_assignment(t.id) is None
        assert q.list_reservations(task_id=t.id) == []
    else:
        reservation, reserved = place(q, t)
        assert reserved and reservation.worktree is None
        assert q.get(t.id).status == Status.CLAIMED
    assert q.get_reservation(old.key) == before


@pytest.mark.parametrize("verdict", [tracking.LIVE, tracking.UNKNOWN, tracking.GONE])
@pytest.mark.parametrize("finished", [False, True])
def test_supervisor_observes_historical_native_without_concluding_successor(q, verdict, finished):
    t, old, retry = carried_retry(q, exclusive_key="shared")
    q.request_spawn_release(old.key, finished_startup_revision=1 if finished else None)
    probes, conclusions = [], []
    client = QueueBackedClient(q)
    supervisor = Supervisor(
        client, machine="host-a", supervisor_id="allocator", max_concurrent=2,
        spawn_fn=lambda _: pytest.fail("no spawn"),
        verdict_fn=lambda *args: probes.append(args) or verdict,
        attempt_conclusion_fn=lambda *args, **kw: conclusions.append((args, kw)),
    )
    assert len(supervisor._active_reservations()) == 2
    retired = finished and verdict == tracking.GONE
    assert supervisor.release_requested_bodies() == int(retired)
    assert probes == ([("worktree", "host-a", "native")] if finished else [])
    assert conclusions == []
    assert q.get_reservation(old.key).native_release_pending is not retired
    assert q.get_reservation(retry.key).state == SpawnState.SPAWNED
    assert q.task_pool_assignment(t.id).reservation_key == retry.key


def test_reopen_does_not_turn_historical_release_intent_into_retirement(q):
    _, old, _ = carried_retry(q, exclusive_key="shared")
    q.request_spawn_release(old.key)
    from agent_dispatch.queue import TaskQueue
    reopened = TaskQueue(q.db_path)
    assert asdict(reopened.get_reservation(old.key)) == asdict(q.get_reservation(old.key))
    with reopened._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (old.key,)).fetchone()
        assert not is_native_retired(row)


def failed_pool_attempts(q: TaskQueue) -> tuple[str, list[SpawnReservation]]:
    publish(q, pool(capacity=4))
    t = task(q, exclusive_key="shared")
    attempts = []
    for _ in range(3):
        r, _ = place(q, q.get(t.id))
        record_native(q, r)
        q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)
        q.fail_spawn(r.key)
        q.yield_task(t.id, "host-a/worktree")
        attempts.append(q.get_reservation(r.key))
    return t.id, attempts


def test_rearm_cannot_certify_a_live_shared_pool_native(q: TaskQueue) -> None:
    task_id, attempts = failed_pool_attempts(q)
    before = q.get(task_id)
    assignment = q.task_pool_assignment(task_id)
    with pytest.raises(TaskError, match="accountable original reservations"):
        q.rearm_spawn(task_id, permitted=True, reason="three failed attempts")
    assert q.get(task_id) == before
    assert q.task_pool_assignment(task_id) == assignment
    assert q.list_reservations(task_id=task_id) == attempts
    with q._connect() as conn:
        rows = conn.execute("SELECT * FROM spawn_reservations WHERE task_id=?", (task_id,)).fetchall()
        assert all(reservation_accountable(conn, row) and not is_native_retired(row) for row in rows)
    sibling = task(q, exclusive_key="shared")
    with pytest.raises(TaskError, match="accountable historical execution"):
        place(q, sibling)
    assert q.list_reservations(task_id=sibling.id) == []
    assert place(q, task(q))[1]
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_rearm_preserves_pool_history_after_qualified_shared_native_retirement(q: TaskQueue) -> None:
    task_id, attempts = failed_pool_attempts(q)
    latest = attempts[-1]
    q.request_spawn_release(latest.key)
    q.retire_spawn(latest.key, exact_absence=True)
    result = q.rearm_spawn(task_id, permitted=True, reason="native retired")
    assert result["rearmed"] == 3 and result["next_attempt"] == 4
    histories = q.list_reservations(task_id=task_id)
    assert all(r.state == SpawnState.REARMED and r.session_handle == "native" for r in histories)
    assert q.task_pool_assignment(task_id).reservation_key == latest.key
    assert place(q, task(q, exclusive_key="shared"))[1]


def test_fresh_authorization_cannot_retire_earlier_pool_native(q: TaskQueue) -> None:
    t, old, retry = carried_retry(q, exclusive_key="shared")
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(retry.key)
    q.yield_task(t.id, "host-a/worktree")
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET session_handle=NULL WHERE key=?", (retry.key,))
        conn.execute("UPDATE spawn_reservations SET reserved_at=99 WHERE key=?", (old.key,))
    before = q.get(t.id)
    histories = q.list_reservations(task_id=t.id)
    with pytest.raises(TaskError, match="accountable original reservations"):
        q.authorize_fresh_acquisition(
            t.id, reservation_key=retry.key, worktree="worktree",
            expected_generation=before.generation, permitted=True,
            confirmed_no_live_holder=True, reason="operator recovery",
        )
    assert q.get(t.id) == before and q.list_reservations(task_id=t.id) == histories
    assert not q.get_reservation(old.key).conversation_retired


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_successor_send_does_not_recount_a_retired_predecessor(q: TaskQueue, state: str) -> None:
    t, old, retry = retired_predecessor_retry(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET source='test-producer' WHERE id=?", (t.id,))
        conn.execute(
            "INSERT INTO execution_send_custody"
            " (scope_key, send_id, repo, source, producer_generation, task_id, reservation_key,"
            " task_generation, kind, identity, state, detail, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scope", "successor-send", t.repo, "test-producer", 1, t.id, retry.key,
             q.get(t.id).generation, "launch", "{}", state, "transport in flight", 100, 100),
        )
        predecessor = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (old.key,)).fetchone()
        current = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (retry.key,)).fetchone()
        assert not reservation_accountable(conn, predecessor)
        assert reservation_accountable(conn, current)
        with pytest.raises(ExecutionSendUnresolved, match="successor-send"):
            require_execution_send_reconciliation(conn, t.id, exclusive_key=t.exclusive_key)
    assert place(q, task(q))[1]
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


@pytest.mark.parametrize("state", ["pending", "unknown"])
@pytest.mark.parametrize("outbox", ["wake_outbox", "run_waiter_wakes"])
def test_successor_wake_accounts_its_native_without_recounting_retired_predecessor(
    q: TaskQueue, state: str, outbox: str,
) -> None:
    t, old, retry = retired_predecessor_retry(q)
    record_native(q, retry, native="replacement")
    q.start(t.id, "host-a/worktree", owner_session_id="replacement",
            expected_generation=q.get(t.id).generation)
    q.suspend(t.id, "host-a/worktree", reason="external wait")
    if outbox == "wake_outbox":
        q.resume(t.id, "host-a/worktree", wake_requested=True, wake_message="continue")
    else:
        waiter = q.register_run_waiter(
            t.id, pid=123, host="host-a", start_token="synthetic-waiter",
            resume_worktree="host-a/worktree", command=["synthetic-wait"],
        )
        q.recover_dead_run_waiter(
            t.id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
    with q._connect() as conn:
        conn.execute(f"UPDATE {outbox} SET execution_send_state=? WHERE task_id=?", (state, t.id))
        predecessor = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (old.key,)).fetchone()
        current = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (retry.key,)).fetchone()
        assert not reservation_transport_accountable(conn, predecessor)
        assert reservation_transport_accountable(conn, current)
        assert not reservation_accountable(conn, predecessor)
        with pytest.raises(ExecutionSendUnresolved, match="unresolved execution wake"):
            require_execution_send_reconciliation(conn, t.id, exclusive_key=t.exclusive_key)
        # Without a named native, a same-workspace send remains ambiguous,
        # not proof that historical effects have gone away.
        conn.execute(f"UPDATE {outbox} SET owner_session_id=NULL WHERE task_id=?", (t.id,))
        assert reservation_transport_accountable(conn, predecessor)
