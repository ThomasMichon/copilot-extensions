"""Tests for :mod:`agent_dispatch.satellite_work_intake` (the
``satellite-agent-exposure`` effort's Phase 3 claim-loop / work-intake).
"""

from __future__ import annotations

import pytest

from agent_dispatch.satellite_work_intake import SatelliteWorkIntake


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeClient:
    """A minimal stand-in for :class:`agent_dispatch.client.DispatchClient`:
    just enough ``list(**params)`` behavior to drive the loop deterministically."""

    def __init__(self, *, active=None, queued=None):
        self._active = active or []
        self._queued = queued or []
        self.calls: list[dict] = []

    def list(self, **params):
        self.calls.append(params)
        if params.get("status") == "claimed,started":
            return list(self._active)
        if params.get("status") == "queued":
            return list(self._queued)
        return []


def _loop(client, *, spawned=None, **kwargs) -> tuple[SatelliteWorkIntake, list]:
    spawned = spawned if spawned is not None else []

    def fake_spawn(task_id, **_kw):
        spawned.append(task_id)
        return object()

    loop = SatelliteWorkIntake(
        client, machine="book2", spawn_fn=fake_spawn, clock=FakeClock(), **kwargs
    )
    return loop, spawned


def test_spawns_for_each_queued_task_up_to_capacity():
    client = FakeClient(queued=[{"id": "t1"}, {"id": "t2"}, {"id": "t3"}])
    loop, spawned = _loop(client, max_concurrent=2)
    result = loop.tick()
    assert spawned == ["t1", "t2"]
    assert result["spawned"] == ["t1", "t2"]
    assert result["skipped_at_capacity"] is False


def test_no_capacity_when_already_at_active_cap():
    client = FakeClient(
        active=[{"id": "running"}], queued=[{"id": "t1"}]
    )
    loop, spawned = _loop(client, max_concurrent=1)
    result = loop.tick()
    assert spawned == []
    assert result["spawned"] == []
    assert result["skipped_at_capacity"] is True
    # Queued list is never even read once capacity is already exhausted.
    assert all(c.get("status") != "queued" for c in client.calls)


def test_recently_triggered_task_not_re_spawned_within_ttl():
    client = FakeClient(queued=[{"id": "t1"}])
    loop, spawned = _loop(client, max_concurrent=1, trigger_ttl=100.0)
    loop.tick()
    assert spawned == ["t1"]
    # Same task still queued (not yet claimed by the spawned session) --
    # must not spawn a second time inside the TTL window.
    loop.tick()
    assert spawned == ["t1"]


def test_recently_triggered_task_reconsidered_after_ttl_expires():
    client = FakeClient(queued=[{"id": "t1"}])
    clock = FakeClock()
    spawned: list[str] = []

    def fake_spawn(task_id, **_kw):
        spawned.append(task_id)
        return object()

    loop = SatelliteWorkIntake(
        client, machine="book2", spawn_fn=fake_spawn, clock=clock, trigger_ttl=50.0,
        max_concurrent=1,
    )
    loop.tick()
    assert spawned == ["t1"]
    clock.advance(51)
    loop.tick()
    assert spawned == ["t1", "t1"]


def test_active_list_failure_degrades_to_at_capacity():
    class BoomClient:
        def list(self, **params):
            raise RuntimeError("coordinator unreachable")

    loop, spawned = _loop(BoomClient())
    result = loop.tick()
    assert spawned == []
    assert result["skipped_at_capacity"] is True
    assert result["error"] == "list_failed"


def test_queued_list_failure_degrades_to_empty_spawned():
    class PartialBoomClient:
        def list(self, **params):
            if params.get("status") == "claimed,started":
                return []
            raise RuntimeError("coordinator unreachable")

    loop, spawned = _loop(PartialBoomClient())
    result = loop.tick()
    assert spawned == []
    assert result["error"] == "list_failed"


def test_spawn_failure_releases_the_slot_immediately():
    client = FakeClient(queued=[{"id": "t1"}, {"id": "t2"}])
    clock = FakeClock()

    def flaky_spawn(task_id, **_kw):
        if task_id == "t1":
            raise RuntimeError("embody failed to launch")
        return object()

    loop = SatelliteWorkIntake(
        client, machine="book2", spawn_fn=flaky_spawn, clock=clock, max_concurrent=1
    )
    result = loop.tick()
    # t1's spawn attempt failed and released its slot -- t2 gets the capacity
    # instead of the whole tick going to waste.
    assert result["spawned"] == ["t2"]


def test_list_calls_scoped_to_this_machine():
    client = FakeClient(queued=[])
    loop, _ = _loop(client)
    loop.tick()
    assert client.calls[0] == {"status": "claimed,started", "target_machine": "book2"}
    assert client.calls[1] == {"status": "queued", "target_machine": "book2"}


def test_requires_machine():
    with pytest.raises(ValueError):
        SatelliteWorkIntake(FakeClient(), machine="")
