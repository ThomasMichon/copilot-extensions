"""Cooling excludes every later startup until its physical outcome is published."""

from __future__ import annotations

from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import tracking
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, TaskError
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests.test_supervisor import OwnerResultQueue, QueueBackedClient


def _body(tmp_path, *, suspended=True):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Dormant review", labels=["review"])
    reservation, _ = queue.reserve_spawn(task.id)
    completed = queue.record_spawn(
        reservation.key, session_handle="local-body:original", startup_complete=True,
        startup_revision=reservation.startup_revision,
    )
    if suspended:
        queue.claim_one("original-owner", task_id=task.id)
        queue.start(task.id, "original-owner")
        queue.suspend(task.id, "original-owner", reason="turn ended")
    return queue, task, completed


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("verdict", [tracking.GONE, tracking.LIVE])
def test_pending_suspended_startup_keeps_capacity_before_abandon(tmp_path, cached, verdict):
    queue, task, reservation = _body(tmp_path)
    queue.record_spawn(
        reservation.key, startup_complete=False,
        session_handle=reservation.session_handle, startup_revision=reservation.startup_revision,
    )
    probes = []
    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("no spare capacity"),
        repo=TEST_REPO, local_body_verdict_fn=lambda sid: probes.append(sid) or verdict,
    )
    if cached:
        supervisor._cooled_reservations.add(reservation.key)
    assert queue.get(task.id).status == "suspended"
    assert len(supervisor._active_reservations()) == 1
    assert probes == []


def test_guard_excludes_resume_and_second_cooler_inside_stop_callback(tmp_path):
    queue, task, reservation = _body(tmp_path)
    client = QueueBackedClient(queue)
    calls = []
    concurrent = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("no replacement"), repo=TEST_REPO,
        local_body_verdict_fn=lambda _: tracking.LIVE,
        local_cold_fn=lambda _: pytest.fail("a second cooler must not stop the body"),
    )

    def stop(sid):
        calls.append(sid)
        current = queue.get_reservation(reservation.key)
        assert current.cooling_token
        assert len(concurrent._active_reservations()) == 1
        assert concurrent.cool_dormant_bodies() == 0
        for operation in (
            lambda: queue.record_spawn(
                reservation.key, startup_complete=False,
                session_handle=reservation.session_handle,
                startup_revision=reservation.startup_revision,
            ),
            lambda: queue.record_cold(reservation.key),
            lambda: queue.resume(task.id, "original-owner", reuse_session=True),
            lambda: queue.fail_spawn(reservation.key),
            lambda: queue.request_spawn_release(reservation.key),
        ):
            with pytest.raises(TaskError, match="cooling remains unresolved"):
                operation()
        return True

    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("no replacement"), repo=TEST_REPO,
        local_body_verdict_fn=lambda _: tracking.LIVE, local_cold_fn=stop,
    )
    assert supervisor.cool_dormant_bodies() == 1
    assert calls == ["original"]
    cold = queue.get_reservation(reservation.key)
    assert cold.state == SpawnState.COLD
    assert cold.cooling_token is None
    assert cold.startup_complete is True
    pending = queue.record_spawn(
        reservation.key, session_handle=reservation.session_handle,
        startup_complete=False, startup_revision=cold.startup_revision,
    )
    assert pending.startup_revision == cold.startup_revision + 1
    assert pending.startup_complete is False


def test_cooling_cannot_intervene_before_resumed_task_finalization(tmp_path):
    queue, task, reservation = _body(tmp_path)
    queue.record_cold(reservation.key)
    queue.submit_steer(task.id, fields={"decision": "continue"}, sender="operator")
    client = QueueBackedClient(queue)
    stops = []
    concurrent = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("no replacement"), repo=TEST_REPO,
        local_body_verdict_fn=lambda _: tracking.LIVE,
        local_cold_fn=lambda sid: stops.append(sid) or True,
    )
    publish = client.record_spawn

    def race(key, **kwargs):
        result = publish(key, **kwargs)
        if kwargs.get("startup_complete") is True:
            concurrent.cool_dormant_bodies()
        return result

    client.record_spawn = race
    original = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("same body must resume"), repo=TEST_REPO,
        local_body_target_dir_fn=lambda _: None, local_resume_fn=lambda *_: True,
    )
    assert original.release_resumed_cold_tasks() == 1
    assert stops == []
    assert queue.get(task.id).status == "started"
    current = queue.get_reservation(reservation.key)
    assert current.state == SpawnState.SPAWNED
    assert current.startup_complete is True


def test_steer_during_guarded_cooling_is_retained_without_starting_or_waking(tmp_path):
    queue, task, reservation = _body(tmp_path)
    guarded = queue.claim_spawn_cooling(
        reservation.key, startup_revision=reservation.startup_revision,
        session_handle=reservation.session_handle, state=SpawnState.SPAWNED,
    )
    steered = queue.submit_steer(task.id, fields={"decision": "continue"}, sender="operator")
    assert steered.status == "suspended"
    assert steered.resume_requested is True
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_steer").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM wake_outbox").fetchone()[0] == 0
    assert queue.get_reservation(reservation.key) == guarded


def test_old_cooled_cache_cannot_hide_or_skip_a_completed_new_resume(tmp_path):
    queue, _task, reservation = _body(tmp_path)
    queue.record_cold(reservation.key)
    pending = queue.record_spawn(
        reservation.key, startup_complete=False, session_handle=reservation.session_handle,
        startup_revision=reservation.startup_revision,
    )
    queue.record_spawn(
        reservation.key, startup_complete=True, session_handle=reservation.session_handle,
        startup_revision=pending.startup_revision,
    )
    stopped = []
    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("no replacement"),
        repo=TEST_REPO, local_body_verdict_fn=lambda _: tracking.LIVE,
        local_cold_fn=lambda sid: stopped.append(sid) or True,
    )
    supervisor._cooled_reservations.add(reservation.key)
    assert len(supervisor._active_reservations()) == 1
    assert supervisor.cool_dormant_bodies() == 1
    assert stopped == ["original"]


def test_resume_winning_immediately_before_atomic_claim_prevents_stop(tmp_path):
    queue, _task, reservation = _body(tmp_path)
    client = QueueBackedClient(queue)
    claim = client.claim_spawn_cooling

    def race(key, **kwargs):
        queue.record_cold(key, startup_revision=reservation.startup_revision)
        queue.record_spawn(
            key, startup_complete=False, session_handle=reservation.session_handle,
            startup_revision=reservation.startup_revision,
        )
        return claim(key, **kwargs)

    client.claim_spawn_cooling = race
    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("no replacement"), repo=TEST_REPO,
        local_body_verdict_fn=lambda _: pytest.fail("no stale probe"),
        local_cold_fn=lambda _: pytest.fail("no stop of the resumed process"),
    )
    assert supervisor.cool_dormant_bodies() == 0
    current = queue.get_reservation(reservation.key)
    assert current.startup_revision == reservation.startup_revision + 1
    assert current.cooling_token is None
    assert len(supervisor._active_reservations()) == 1


@pytest.mark.parametrize("outcome", ["false", "error"])
def test_ambiguous_stop_retains_guard_across_coordinator_reopen(tmp_path, outcome):
    queue, task, reservation = _body(tmp_path)

    def stop(_sid):
        if outcome == "error":
            raise OSError("stop acknowledgement lost")
        return False

    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("no replacement"),
        repo=TEST_REPO, local_body_verdict_fn=lambda _: tracking.LIVE,
        local_cold_fn=stop,
    )
    assert supervisor.cool_dormant_bodies() == 0
    reopened = OwnerResultQueue(tmp_path / "tasks.db")
    current = reopened.get_reservation(reservation.key)
    assert current.cooling_token
    with pytest.raises(TaskError, match="cooling remains unresolved"):
        reopened.record_spawn(
            reservation.key, session_handle=reservation.session_handle,
            startup_complete=False, startup_revision=reservation.startup_revision,
        )
    recovery = Supervisor(
        QueueBackedClient(reopened), spawn_fn=lambda _: pytest.fail("guard retains capacity"),
        repo=TEST_REPO, local_body_verdict_fn=lambda _: tracking.GONE,
    )
    recovery._cooled_reservations.add(reservation.key)
    assert len(recovery._active_reservations()) == 1
    assert reopened.get(task.id).status == "suspended"


@pytest.mark.parametrize("cached", [False, True])
def test_queued_legacy_cold_never_recovers_or_frees_capacity(tmp_path, cached):
    queue, task, reservation = _body(tmp_path, suspended=False)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state=?, startup_complete=0 WHERE key=?",
            (SpawnState.COLD, reservation.key),
        )
    before = asdict(queue.get_reservation(reservation.key))
    probes = []
    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("no replacement"),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda sid: probes.append(sid) or tracking.GONE,
        local_cold_fn=lambda _: pytest.fail("legacy startup cannot be stopped"),
    )
    if cached:
        supervisor._cooled_reservations.add(reservation.key)
    assert supervisor.recover_stranded_cold_reservations() == 0
    assert len(supervisor._active_reservations()) == 1
    assert probes == []
    with pytest.raises(TaskError, match="previous startup remains incomplete"):
        queue.defer_spawn(
            reservation.key, startup_revision=reservation.startup_revision,
            expected_generation=task.generation,
        )
    assert asdict(queue.get_reservation(reservation.key)) == before
    other = queue.create("Another eligible review", labels=["review"])
    supervisor.max_concurrent = 1
    assert supervisor.poll_once() == []
    assert asdict(queue.get_reservation(reservation.key)) == before
    assert queue.latest_reservation(task.id).attempt == reservation.attempt
    assert queue.latest_reservation(other.id) is None
    assert queue.get(task.id).status == "queued"
    assert queue.get(task.id).owner is None


def test_guarded_cold_deferral_checks_revision_token_and_task_atomically(tmp_path):
    queue, task, reservation = _body(tmp_path, suspended=False)
    queue.record_cold(reservation.key)
    guarded = queue.claim_spawn_cooling(
        reservation.key, state=SpawnState.COLD, startup_revision=reservation.startup_revision,
        session_handle=reservation.session_handle,
    )
    for fields in (
        {"startup_revision": reservation.startup_revision + 1},
        {"cooling_token": "different-operation"},
        {"expected_generation": task.generation + 1},
    ):
        arguments = {
            "startup_revision": guarded.startup_revision,
            "cooling_token": guarded.cooling_token, "expected_generation": task.generation,
            **fields,
        }
        with pytest.raises(TaskError):
            queue.defer_spawn(reservation.key, **arguments)
        assert queue.get_reservation(reservation.key) == guarded
    deferred = queue.defer_spawn(
        reservation.key, startup_revision=guarded.startup_revision,
        cooling_token=guarded.cooling_token, expected_generation=task.generation,
    )
    assert deferred.state == SpawnState.DEFERRED
    assert deferred.cooling_token is None


def test_cooling_guard_round_trips_client_http_and_storage(tmp_path):
    queue, _task, reservation = _body(tmp_path)
    with TestClient(create_app(queue)) as api:
        def forward(request):
            response = api.request(
                request.method, request.url.path, content=request.content,
                headers={"content-type": "application/json"},
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient("http://testserver", transport=httpx.MockTransport(forward)) as client:
            guarded = client.claim_spawn_cooling(
                reservation.key, state=SpawnState.SPAWNED,
                session_handle=reservation.session_handle,
                startup_revision=reservation.startup_revision,
            )
            assert guarded["cooling_token"] == queue.get_reservation(reservation.key).cooling_token
            cold = client.record_cold(
                reservation.key, cooling_token=guarded["cooling_token"],
                startup_revision=guarded["startup_revision"],
            )
            assert cold["state"] == SpawnState.COLD
            assert cold["cooling_token"] is None
