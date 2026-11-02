"""Age and a missing identity cannot retire an accountable pool placement."""

from __future__ import annotations

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, TaskError, TaskQueue
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, task
from tests.test_supervisor import QueueBackedClient

q = placement_controls.q


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("startup_closed", [False, True])
def test_empty_released_pool_keeps_capacity_until_exact_absence(
    q: TaskQueue, workspace: bool, startup_closed: bool,
) -> None:
    publish(q, pool())
    requested = task(q, exclusive_key="shared")
    reserved, _ = place(q, requested)
    if workspace:
        q.record_spawn_worktree(
            reserved.key, "worktree", ownership="created", execution_machine="host-a",
            creating_host="host-a", driver="cli",
        )
    released = q.request_spawn_release(
        reserved.key, finished_startup_revision=1 if startup_closed else None,
    )
    before = q.get(requested.id)
    client = QueueBackedClient(q)
    facts = client.get_reservation(reserved.key)
    assert facts["reservation_pool_bound"] is True
    assert facts["reservation_native_session_id"] is None
    supervisor = Supervisor(client, spawn_fn=lambda _: pytest.fail("must not spawn"))
    assert supervisor.recover_stranded_releasing_reservations(now=701) == 0
    with pytest.raises(TaskError, match="pool release.*exact absence"):
        q.fail_spawn(reserved.key, force=True)
    response = TestClient(create_app(q)).post(
        f"/spawn-reservations/{reserved.key}/fail", json={"force": True},
    )
    assert response.status_code == 409 and "exact absence" in response.json()["detail"]
    assert q.get_reservation(reserved.key) == released
    assert q.get(requested.id) == before
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with pytest.raises(TaskError, match="exclusive key"):
        place(q, task(q, exclusive_key="shared"))
    if not startup_closed:
        q.request_spawn_release(reserved.key, finished_startup_revision=1)
    q.retire_spawn(reserved.key, exact_absence=True, conclusion_state="skipped")
    assert place(q, task(q, exclusive_key="shared"))[1]


def test_stale_unassigned_snapshot_cannot_force_retire_a_pool_in_transaction(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish(q, pool())
    requested = task(q)
    reserved, _ = place(q, requested)
    released = q.request_spawn_release(reserved.key, finished_startup_revision=1)
    client = QueueBackedClient(q)
    actual = client.get_reservation(reserved.key)
    monkeypatch.setattr(client, "get_reservation", lambda _key: {
        **actual, "reservation_pool_bound": False,
    })
    calls = []

    def fail(key: str, **kwargs: object) -> dict:
        calls.append(key)
        try:
            return asdict(q.fail_spawn(key, **kwargs))
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    monkeypatch.setattr(client, "fail_spawn", fail)
    supervisor = Supervisor(client, spawn_fn=lambda _: pytest.fail("must not spawn"))
    assert supervisor.recover_stranded_releasing_reservations(now=701) == 0
    assert calls == [reserved.key]
    assert q.get_reservation(reserved.key) == released


@pytest.mark.parametrize("qualified", [False, True])
def test_empty_unassigned_legacy_recovery_requires_qualified_binding_observation(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, qualified: bool,
) -> None:
    requested = task(q)
    reserved, _ = q.reserve_spawn(requested.id)
    released = q.request_spawn_release(reserved.key, finished_startup_revision=1)
    client = QueueBackedClient(q)
    facts = client.get_reservation(reserved.key)
    assert facts["reservation_pool_bound"] is False
    if not qualified:
        facts.pop("reservation_pool_bound")
        monkeypatch.setattr(client, "get_reservation", lambda _key: facts)
    supervisor = Supervisor(client, spawn_fn=lambda _: pytest.fail("must not spawn"))
    assert supervisor.recover_stranded_releasing_reservations(now=701) == int(qualified)
    assert q.get_reservation(reserved.key).state == (
        SpawnState.FAILED if qualified else released.state
    )
