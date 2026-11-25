"""An obsolete claim that no body accepted still has an automatic terminal move."""

from __future__ import annotations

import json

import pytest

from agent_dispatch.queue import SpawnState, Status, TaskError, TaskQueue
from agent_dispatch.queue_pool_startup import OWNERLESS_CLOSEOUT_ACTION
from agent_dispatch.queue_relevance import RelevanceScope
from tests import test_pool_placement as placement_controls
from tests.test_pool_startup_policy import renew
from tests.test_pool_placement import place, pool, publish, task
from tests.test_task_relevance import report

q = placement_controls.q


def obsolete_unstarted_claim(q: TaskQueue):
    publish(q, pool())
    t = task(q, source="emitter", origin_ref="review-source")
    r, _ = place(q, t)
    report(q, t.id, scope=RelevanceScope(t.repo, t.source, t.origin_ref))
    return q.get(t.id), r


def test_ownerless_closeout_exhausts_to_abandonment_and_frees_capacity(q: TaskQueue) -> None:
    t, r = obsolete_unstarted_claim(q)
    closeout = q.relevance_snapshot(t.id)["closeout"]
    assert closeout["owner"] is None and closeout["owner_session_id"] is None
    assert t.status == Status.CLAIMED and t.owner is None
    assert q.list_wakes(t.id) == []
    with pytest.raises(TaskError, match="deadline has not expired"):
        q.exhaust_pool_startup(r.key, generation=t.generation, startup_revision=1, now=699)

    settled = q.exhaust_pool_startup(r.key, generation=t.generation, startup_revision=1, now=700)

    assert settled.status == Status.ABANDONED and settled.generation == t.generation
    assert settled.owner is None and settled.claimed_at is None
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and retained.release_requested
    assert not retained.startup_complete and not retained.startup_fenced
    assert q.pool_startup(t.id).exhaustion_reason == "startup_timeout"
    with q._connect() as conn:
        ack = json.loads(conn.execute(
            "SELECT acknowledgement FROM task_relevance_closeouts WHERE task_id=?", (t.id,),
        ).fetchone()[0])
    assert ack["finish_action"] == OWNERLESS_CLOSEOUT_ACTION
    assert ack["reservation_key"] == r.key and ack["at"] == 700
    assert not any(event["to_status"] == "started" for event in q.events(t.id))
    with pytest.raises(TaskError, match="changed"):
        q.exhaust_pool_startup(r.key, generation=t.generation, startup_revision=1, now=701)

    q.request_spawn_release(r.key, finished_startup_revision=1, now=701)
    q.retire_spawn(r.key, exact_absence=True, now=702)
    renew(q, 703)
    replacement, reserved = place(q, task(q), now=703)
    assert reserved and replacement.key != r.key


def test_owned_closeout_still_requires_its_owner_not_expiry(q: TaskQueue) -> None:
    t, r = obsolete_unstarted_claim(q)
    with q._connect() as conn:
        conn.execute(
            "UPDATE task_relevance_closeouts SET owner='host-a/worktree',"
            " owner_session_id='native' WHERE task_id=?", (t.id,),
        )
    with pytest.raises(TaskError, match="owner closeout"):
        q.exhaust_pool_startup(r.key, generation=t.generation, startup_revision=1, now=700)
    assert q.get(t.id).status == Status.CLAIMED
