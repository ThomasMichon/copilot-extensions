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


class FakeProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


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


def _loop(
    client, *, spawned=None, project="test-project", **kwargs
) -> tuple[SatelliteWorkIntake, list]:
    spawned = spawned if spawned is not None else []

    def fake_spawn(task_id, **_kw):
        spawned.append(task_id)
        return FakeProc(0)

    loop = SatelliteWorkIntake(
        client,
        machine="book2",
        project=project,
        spawn_fn=fake_spawn,
        clock=FakeClock(),
        **kwargs,
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
    client = FakeClient(active=[{"id": "running"}], queued=[{"id": "t1"}])
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
        return FakeProc(0)

    loop = SatelliteWorkIntake(
        client,
        machine="book2",
        project="test-project",
        spawn_fn=fake_spawn,
        clock=clock,
        trigger_ttl=50.0,
        max_concurrent=1,
    )
    loop.tick()
    assert spawned == ["t1"]
    clock.advance(51)
    loop.tick()
    assert spawned == ["t1", "t1"]


def test_recently_triggered_entry_cleared_once_observed_active():
    # t1 was triggered last tick and hasn't shown up as claimed/started yet
    # -- still counted (recent_triggers). Once the coordinator confirms it
    # active, the placeholder must be dropped so it doesn't ALSO keep
    # squatting on a slot after the real task finishes and drops off the
    # active list.
    client = FakeClient(queued=[{"id": "t1"}, {"id": "t2"}])
    loop, spawned = _loop(client, max_concurrent=1)
    loop.tick()
    assert spawned == ["t1"]

    # Next tick: t1 now shows as claimed (coordinator-confirmed) and no
    # longer appears in `queued`.
    client._active = [{"id": "t1"}]
    client._queued = [{"id": "t2"}]
    result = loop.tick()
    # capacity: max_concurrent(1) - active(1) - recent_triggers(0, cleared) = 0
    assert result["spawned"] == []
    assert result["skipped_at_capacity"] is True

    # Once t1 finishes and drops off the active list entirely, capacity
    # frees up for t2 -- proving the stale trigger didn't linger.
    client._active = []
    result = loop.tick()
    assert result["spawned"] == ["t2"]


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


def test_spawn_exception_releases_the_slot_immediately():
    client = FakeClient(queued=[{"id": "t1"}, {"id": "t2"}])

    def flaky_spawn(task_id, **_kw):
        if task_id == "t1":
            raise RuntimeError("embody failed to launch")
        return FakeProc(0)

    loop, _ = _loop(client, spawned=[], max_concurrent=1)
    loop._spawn_fn = flaky_spawn
    result = loop.tick()
    # t1's spawn attempt raised and released its slot -- t2 gets the
    # capacity instead of the whole tick going to waste.
    assert result["spawned"] == ["t2"]


def test_spawn_nonzero_returncode_counts_as_failure_not_success():
    # spawn_embodied_worker runs with check=False, so a failed
    # `agent-worktrees embody` invocation comes back as an ordinary
    # CompletedProcess with a nonzero returncode, not a raised exception.
    client = FakeClient(queued=[{"id": "t1"}, {"id": "t2"}])

    def half_flaky_spawn(task_id, **_kw):
        return FakeProc(1) if task_id == "t1" else FakeProc(0)

    loop, _ = _loop(client, max_concurrent=1)
    loop._spawn_fn = half_flaky_spawn
    result = loop.tick()
    assert result["spawned"] == ["t2"]
    # t1's slot was released, not left squatting for the TTL.
    assert "t1" not in loop._recent_triggers


def test_list_calls_scoped_to_this_machine_and_capped_active_limit():
    client = FakeClient(queued=[])
    loop, _ = _loop(client, max_concurrent=3)
    loop.tick()
    assert client.calls[0] == {
        "status": "claimed,started",
        "target_machine": "book2",
        "repo": None,
        "limit": 200,
    }
    assert client.calls[1] == {
        "status": "queued",
        "target_machine": "book2",
        "repo": None,
        "limit": 200,
    }


def test_active_list_limit_covers_a_cap_above_the_endpoint_default():
    client = FakeClient(queued=[])
    loop, _ = _loop(client, max_concurrent=500)
    loop.tick()
    assert client.calls[0]["limit"] == 500


def test_queued_list_limit_matches_remaining_capacity_above_endpoint_default():
    client = FakeClient(active=[], queued=[])
    loop, _ = _loop(client, max_concurrent=250)
    loop.tick()
    assert client.calls[1]["limit"] == 250


def test_queued_list_limit_never_below_the_endpoint_default():
    # Even a small capacity must request at least the endpoint's own
    # default page size -- see the oldest-first fairness note.
    client = FakeClient(queued=[])
    loop, _ = _loop(client, max_concurrent=2)
    loop.tick()
    assert client.calls[1]["limit"] == 200


def test_queued_tasks_considered_oldest_first_within_the_page():
    # The `/tasks` endpoint orders newest-first with no oldest-first option
    # -- this loop must re-sort the page itself so an older queued task
    # isn't starved behind a stream of newer ones inside the same response.
    client = FakeClient(
        queued=[
            {"id": "new", "created_at": 300.0},
            {"id": "old", "created_at": 100.0},
            {"id": "mid", "created_at": 200.0},
        ]
    )
    loop, spawned = _loop(client, max_concurrent=1)
    loop.tick()
    assert spawned == ["old"]


def test_queued_list_limit_padded_past_recently_triggered_count():
    # If the first `capacity` rows the coordinator would otherwise return
    # are all already in `_recent_triggers` (still awaiting confirmation),
    # a request sized to exactly `capacity` could come back entirely
    # client-side-suppressed and leave a slot idle even though a newer
    # eligible task exists further down the queue -- the request must be
    # padded past the in-flight count so those newer rows are still in
    # the returned page (and past the endpoint's own 200 floor, once
    # capacity + in-flight exceeds it).
    client = FakeClient(queued=[])
    loop, _ = _loop(client, max_concurrent=250)
    # White-box: seed 100 still-pending triggers directly, as if 100 spawns
    # were triggered on a prior tick and not yet coordinator-confirmed.
    loop._recent_triggers = {f"pending-{i}": 1000.0 for i in range(100)}
    loop.tick()
    # capacity = 250 - active(0) - recent_triggers(100) = 150
    assert client.calls[-1]["limit"] == 150 + 100


# -- spawn timeout -------------------------------------------------------


def test_spawn_timeout_passed_through_to_spawn_fn():
    captured = []

    def fake_spawn(task_id, **kw):
        captured.append(kw.get("timeout"))
        return FakeProc(0)

    client = FakeClient(queued=[{"id": "t1"}])
    loop = SatelliteWorkIntake(
        client,
        machine="book2",
        project="test-project",
        spawn_timeout=15.0,
        spawn_fn=fake_spawn,
        clock=FakeClock(),
    )
    loop.tick()
    assert captured == [15.0]


def test_spawn_timeout_defaults_to_a_bounded_value():
    from agent_dispatch.satellite_work_intake import DEFAULT_SPAWN_TIMEOUT_S

    captured = []

    def fake_spawn(task_id, **kw):
        captured.append(kw.get("timeout"))
        return FakeProc(0)

    client = FakeClient(queued=[{"id": "t1"}])
    loop = SatelliteWorkIntake(
        client, machine="book2", project="test-project",
        spawn_fn=fake_spawn, clock=FakeClock(),
    )
    loop.tick()
    assert captured == [DEFAULT_SPAWN_TIMEOUT_S]


def test_spawn_timeout_expiry_releases_the_slot():
    # A hung `agent-worktrees embody` process is exactly what the bounded
    # timeout guards against -- subprocess.run raises TimeoutExpired, which
    # `_try_spawn` must treat like any other launch failure.
    client = FakeClient(queued=[{"id": "t1"}])

    def hanging_spawn(task_id, **kw):
        raise TimeoutError("embody did not return within the bound")

    loop = SatelliteWorkIntake(
        client,
        machine="book2",
        project="test-project",
        spawn_fn=hanging_spawn,
        clock=FakeClock(),
    )
    result = loop.tick()
    assert result["spawned"] == []
    assert "t1" not in loop._recent_triggers


def test_satellite_repo_scopes_the_discovery_queries():
    client = FakeClient(queued=[])
    loop, _ = _loop(client, repo="aperture-labs")
    loop.tick()
    assert client.calls[0]["repo"] == "aperture-labs"
    assert client.calls[1]["repo"] == "aperture-labs"


# -- project resolution -------------------------------------------------------


def test_explicit_project_override_used_for_every_task():
    captured = []

    def fake_spawn(task_id, **kw):
        captured.append(kw["project"])
        return FakeProc(0)

    client = FakeClient(queued=[{"id": "t1", "repo": "some/other-repo"}])
    loop = SatelliteWorkIntake(
        client,
        machine="book2",
        project="explicit-project",
        spawn_fn=fake_spawn,
        clock=FakeClock(),
    )
    loop.tick()
    assert captured == ["explicit-project"]


def test_project_derived_from_task_repo_when_unconfigured(monkeypatch):
    import agent_dispatch.embody as embody_mod

    captured = []

    def fake_spawn(task_id, **kw):
        captured.append(kw["project"])
        return FakeProc(0)

    def fake_project_for_task(task):
        return f"derived-{task['repo']}"

    monkeypatch.setattr(embody_mod, "project_for_task", fake_project_for_task)

    client = FakeClient(queued=[{"id": "t1", "repo": "acme"}])
    loop = SatelliteWorkIntake(
        client, machine="book2", spawn_fn=fake_spawn, clock=FakeClock()
    )
    loop.tick()
    assert captured == ["derived-acme"]


def test_unresolvable_project_fails_the_spawn_and_releases_the_slot():
    # No explicit project, and the task carries no repo lane to derive one
    # from -- must degrade to a per-task spawn failure (never a silent
    # CWD-discovery fallback, since this loop runs from a daemon/service
    # context with no meaningful CWD).
    client = FakeClient(queued=[{"id": "t1"}])
    spawn_calls = []

    def fake_spawn(task_id, **kw):
        spawn_calls.append(task_id)
        return FakeProc(0)

    loop = SatelliteWorkIntake(
        client, machine="book2", spawn_fn=fake_spawn, clock=FakeClock()
    )
    result = loop.tick()
    assert result["spawned"] == []
    assert spawn_calls == []  # spawn_fn never even reached
    assert "t1" not in loop._recent_triggers


def test_requires_machine():
    with pytest.raises(ValueError):
        SatelliteWorkIntake(FakeClient(), machine="")
