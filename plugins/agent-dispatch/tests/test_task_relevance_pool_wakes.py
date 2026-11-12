"""Cold closeout retains the actual pool lane, original native and send gates."""

import pytest

from agent_dispatch.queue import SpawnState, Status, TaskError
from agent_dispatch.queue_execution_custody import PoolLaneUnavailable
from agent_dispatch.queue_pool_recovery import prepare_recovery_wake, reconcile_recoveries
from agent_dispatch.queue_relevance import RelevanceScope
from tests import test_pool_placement as controls
from tests.test_pool_suspended_recovery import suspended, _cold, _disable_pools
from tests.test_task_relevance import request

q = controls.q


def obsolete(q, task_id):
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET source='emitter', origin_ref='review-source' WHERE id=?", (task_id,),
        )
    snapshot = q.relevance_snapshot(task_id)
    return q.reconcile_task_relevance(task_id, **request(
        q, task_id, scope=RelevanceScope(snapshot["repo"], snapshot["source"], snapshot["origin_ref"]),
    ), now=103)


@pytest.mark.parametrize("cold", [False, True])
def test_suspended_pool_closeout_never_requests_recovery_or_accepts_original_goal(q, cold):
    task_id, key, generation = suspended(q)
    if cold:
        _cold(q, key)
    before = q.pool_startup(task_id)
    accepted = obsolete(q, task_id)
    wake = q.claim_due_wake(now=104)
    # Match the fixture's synthetic clock at the actual delivery admission boundary.
    q._now = lambda now: 104 if now is None else now
    prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.SPAWNED
    assert q.pool_startup(task_id) == before
    assert q.get(task_id).status == Status.SUSPENDED and not q.get(task_id).resume_requested
    assert reconcile_recoveries(q, now=100000) == 0
    assert q.list_wakes(task_id)[0].id == accepted["wake_operation_id"]
    admission = q.admit_execution_wake(wake, provenance=None, now=104)
    q.settle_execution_wake(wake, admission, outcome="sent", detail="original closeout accepted")
    q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=105)
    assert q.take_steer(task_id, "host-a/worktree", now=106)["id"] == accepted["steer_id"]
    with pytest.raises(TaskError):
        q.start(task_id, "host-a/worktree", owner_session_id="native",
                expected_generation=generation, now=107)
    assert q.get(task_id).status == Status.SUSPENDED


@pytest.mark.parametrize("block", ["disabled_pool", "capacity", "token", "hold"])
def test_cold_closeout_cannot_bypass_existing_lane_or_wake_admission(q, block):
    task_id, key, _ = suspended(q)
    _cold(q, key)
    obsolete(q, task_id)
    wake = q.claim_due_wake(now=104)
    q._now = lambda now: 104 if now is None else now
    if block == "disabled_pool":
        _disable_pools(q)
    elif block == "capacity":
        from tests.test_pool_placement import place, task
        place(q, task(q), now=104)
    elif block == "hold":
        q.set_hold(task_id, reason="independent hold", actor="operator", now=104)
    else:
        with q._connect() as conn:
            conn.execute("UPDATE wake_outbox SET delivery_token='other' WHERE id=?", (wake.id,))
    with pytest.raises((TaskError, PoolLaneUnavailable)):
        prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.COLD
    assert q.get(task_id).status == Status.SUSPENDED
    assert q.list_wakes(task_id)[-1].execution_send_state is None
