"""Owner-gone auto-suspension parks the dead body's reservation and frees its lane."""

from __future__ import annotations

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import SpawnState, Status, TaskError
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, task

q = placement_controls.q


def test_owner_gone_auto_suspend_parks_the_reservation_and_frees_its_lane(
    q: TaskQueue,
) -> None:
    publish(q, pool())
    t = task(q)
    reservation, _ = place(q, t)
    record_native(q, reservation)
    q.record_spawn(
        reservation.key, session_handle="native", startup_complete=True, startup_revision=1,
    )
    before = q.get(t.id)
    assert before is not None
    started = q.start(
        t.id, "host-a/worktree", owner_session_id="native",
        expected_generation=before.generation, now=101,
    )
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q), now=102)

    counts = q.reconcile_liveness(
        lambda *_args: "gone", headless_local_verdict=lambda _session: "gone", now=103,
    )

    assert counts["suspended"] == 1
    current, parked = q.get(t.id), q.get_reservation(reservation.key)
    assert current is not None and parked is not None
    assert current.status == Status.SUSPENDED and current.generation == started.generation
    assert parked.state == SpawnState.COLD and parked.session_handle == "native"
    assert parked.cooling_token is None and not parked.release_requested
    events: list[dict[str, object]] = q.events(t.id)
    assert "reservation cold" in str(events[-1]["note"])
    place(q, task(q), now=104)
