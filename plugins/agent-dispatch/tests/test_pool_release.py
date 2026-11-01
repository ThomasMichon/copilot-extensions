"""Pool retirement preserves historical native and explicit workspace custody."""

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_dispatch import tracking
from agent_dispatch.ordinary_native_bindings import is_native_retired
from agent_dispatch.queue import SpawnState, Status, TaskError
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


@pytest.mark.parametrize("explicit", [False, True])
def test_unrelated_cleanup_cannot_discard_an_explicit_target(q, explicit):
    publish(q, pool(capacity=2))
    old_task = task(q, target_worktree="wanted")
    old, _ = place(q, old_task)
    record_native(q, old, worktree="wanted")
    q.record_spawn(old.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.fail_spawn(old.key, conclusion_state="pending")
    q.request_spawn_release(old.key)
    q.retire_spawn(old.key, exact_absence=True)
    before = q.get_reservation(old.key)
    t = task(q, **({"target_worktree": "wanted"} if explicit else {"affinity": {"worktree": "wanted"}}))
    if explicit:
        with pytest.raises(TaskError, match="explicit target worktree"):
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
