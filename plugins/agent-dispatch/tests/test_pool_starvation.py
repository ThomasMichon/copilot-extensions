"""Lane starvation: a full pool held only by tasks that stopped progressing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import current_task, place, pool, publish, record_native, task

q = placement_controls.q


def held(q: TaskQueue, *, suspend: bool) -> str:
    publish(q, pool())
    t = task(q)
    reservation, _ = place(q, t)
    record_native(q, reservation)
    q.record_spawn(reservation.key, session_handle="native", startup_complete=True, startup_revision=1)
    started = q.start(
        t.id, "host-a/worktree", owner_session_id="native",
        expected_generation=current_task(q, t.id).generation, now=101,
    )
    assert started.owner is not None
    if suspend:
        q.suspend(t.id, started.owner, reason="intentional wait", cooldown_seconds=None, now=102)
    return t.id


def test_a_full_pool_held_by_a_suspended_task_starves_queued_work(q: TaskQueue) -> None:
    holder = held(q, suspend=True)
    waiting = task(q)
    (starved,) = q.pool_starvation(now=1000)
    assert (starved["registration_id"], starved["machine"], starved["env"]) == ("worker", "host-a", "linux")
    assert (starved["max_concurrent"], starved["load"], starved["queued"]) == (1, 1, 1)
    assert starved["oldest_queued_age"] == 1000 - current_task(q, waiting.id).created_at
    assert [(h["task_id"], h["task_status"]) for h in starved["holders"]] == [(holder, "suspended")]


def test_a_progressing_holder_is_not_starvation(q: TaskQueue) -> None:
    held(q, suspend=False)
    task(q)
    assert q.pool_starvation(now=1000) == []


def test_a_suspended_holder_with_nothing_waiting_is_not_starvation(q: TaskQueue) -> None:
    held(q, suspend=True)
    assert q.pool_starvation(now=1000) == []


def test_the_coordinator_advertises_and_serves_pool_starvation(q: TaskQueue) -> None:
    holder = held(q, suspend=True)
    task(q)
    client = TestClient(create_app(q, token=None, enable_mcp=False, verification_interval=0))
    assert client.get("/health").json()["pool_starvation"] == 1
    (starved,) = client.get("/pool-starvation").json()["pools"]
    assert [h["task_id"] for h in starved["holders"]] == [holder]
