"""Session creation uses its reservation, not the workspace's older assignment."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import sqlite3
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.queue_pool_placement import pool_placement_contract
from agent_dispatch.queue_session_origin import bind, migrate
from tests.test_assignment_cohort import managed_task
from tests.test_pool_placement import place, pool, publish


def test_current_pool_assignment_is_saved_before_body_and_survives_mutable_changes(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task, _ = managed_task(queue, "original")
    publish(queue, pool())
    reservation, _ = place(queue, task)
    origin = reservation.session_origin
    assert reservation.worktree is None and reservation.session_handle is None
    assert reservation.session_origin_reservation_key == reservation.key
    assert reservation.session_origin_transport is None
    assert origin == {
        "task_id": task.id, "reservation_key": reservation.key,
        "cohort": {
            "schema": "dispatch-assignment-cohort", "version": 1,
            "emitter": reservation.allocation_cohort["emitter"],
            "pool": asdict(queue.task_pool_assignment(task.id)),
        },
    }
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET catalog_revision=catalog_revision+1 WHERE task_id=?", (task.id,),
        )
    saved = json.loads(json.dumps(origin))
    origin["cohort"]["pool"]["registration_id"] = "changed-return"
    restarted = TaskQueue(queue.db_path)
    assert restarted.get_reservation(reservation.key).session_origin == saved
    with TestClient(create_app(restarted, enable_mcp=False, verification_interval=0)) as api:
        assert api.get(f"/spawn-reservations/{reservation.key}").json()["session_origin"] == saved
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE spawn_reservations SET session_origin=NULL WHERE key=?", (reservation.key,))


def test_new_acquisition_attempt_in_reused_allocation_has_its_own_origin(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task, _ = managed_task(queue, "workspace-birth", dedup_key="logical")
    first, _ = queue.reserve_spawn(task.id, driver="cli")
    queue.record_spawn_worktree(first.key, "workspace", ownership="created", driver="cli")
    queue.fail_spawn(first.key, detail="native did not start")
    retry, _ = queue.reserve_spawn(task.id, driver="cli")
    assert retry.worktree == "workspace" and retry.worktree_ownership == "reused"
    assert retry.allocation_cohort == first.allocation_cohort
    assert retry.session_origin["reservation_key"] == retry.key
    assert retry.session_origin_reservation_key == retry.key
    queue.fail_spawn(retry.key, detail="no native")
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker")
    queue.complete(task.id, "worker")
    later_task = queue.create("later episode", repo=task.repo, dedup_key="logical")
    later, _ = queue.reserve_spawn(later_task.id, driver="cli")
    assert later.worktree == "workspace"
    assert later.allocation_cohort["emitter"]["task_id"] == task.id
    assert later.session_origin == {
        "task_id": later_task.id, "reservation_key": later.key,
        "cohort": {"schema": "dispatch-assignment-cohort", "version": 1, "emitter": None, "pool": None},
    }


@pytest.mark.parametrize("transport", ["baseline", "origin-v1"])
def test_keyed_ordinary_retry_lost_reply_and_later_episode_keep_original_choice(tmp_path, transport):
    queue = TaskQueue(tmp_path / "queue.db")
    task, _ = managed_task(queue, "original", dedup_key="logical")
    first, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    selected = bind(queue, first.key, startup_revision=1, transport=transport)
    queue.defer_spawn(first.key, detail="creation reply lost")
    restarted = TaskQueue(queue.db_path)
    retry, _ = restarted.reserve_spawn(task.id, driver="ordinary-bridge")
    assert retry.creation_key == first.creation_key
    assert retry.session_origin == first.session_origin
    assert retry.session_origin_reservation_key == first.key
    assert retry.session_origin_transport == transport
    assert bind(restarted, retry.key, startup_revision=1, transport=transport).session_origin == selected.session_origin
    with pytest.raises(TaskError, match="cannot change"):
        bind(restarted, retry.key, startup_revision=1,
             transport="baseline" if transport == "origin-v1" else "origin-v1")
    restarted.fail_spawn(retry.key, detail="retained for later retry")
    restarted.claim_one("worker", task_id=task.id)
    restarted.start(task.id, "worker")
    restarted.complete(task.id, "worker")
    later_task = restarted.create("later episode", repo=task.repo, dedup_key="logical")
    later, _ = restarted.reserve_spawn(later_task.id, driver="ordinary-bridge")
    assert later.creation_key == first.creation_key
    assert later.session_origin == first.session_origin
    assert later.session_origin_transport == transport
    assert later.session_origin["task_id"] != later_task.id


def test_unselected_ordinary_retry_seals_original_and_current_rows(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    first, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    queue.defer_spawn(first.key, detail="before selection")
    retry, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    assert retry.session_origin_transport is None
    bind(queue, retry.key, startup_revision=1, transport="origin-v1")
    assert queue.get_reservation(first.key).session_origin_transport == "origin-v1"
    assert queue.get_reservation(retry.key).session_origin_transport == "origin-v1"


def test_switch_from_acquisition_to_keyed_creation_does_not_inherit_its_session_origin(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    first, _ = queue.reserve_spawn(task.id, driver="cli")
    bind(queue, first.key, startup_revision=1, transport="baseline")
    queue.fail_spawn(first.key, detail="before native")
    ordinary, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    assert ordinary.creation_key == first.creation_key
    assert ordinary.session_origin["reservation_key"] == ordinary.key
    assert ordinary.session_origin_transport is None


def test_legacy_migration_and_keyed_retry_do_not_backfill_origin(tmp_path, monkeypatch):
    from agent_dispatch import queue_session_origin

    queue = TaskQueue(tmp_path / "queue.db")
    task, _ = managed_task(queue, "legacy")
    with monkeypatch.context() as legacy:
        legacy.setattr(queue_session_origin, "retain", lambda *a, **k: None)
        first, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    # A previous runtime inserted the SQL default, not a pending new choice.
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET session_origin_transport='baseline' WHERE key=?", (first.key,),
        )
    restarted = TaskQueue(queue.db_path)
    assert restarted.get_reservation(first.key).session_origin is None
    restarted.defer_spawn(first.key, detail="legacy unknown")
    retry, _ = restarted.reserve_spawn(task.id, driver="ordinary-bridge")
    assert retry.session_origin is None and retry.session_origin_transport == "baseline"
    assert retry.session_origin_reservation_key == first.key
    with pytest.raises(TaskError, match="cannot change"):
        bind(restarted, retry.key, startup_revision=1, transport="origin-v1")
    assert bind(restarted, retry.key, startup_revision=1, transport="baseline").session_origin is None

    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE spawn_reservations (key TEXT, creation_key TEXT, driver TEXT)")
        conn.execute("INSERT INTO spawn_reservations(key) VALUES ('old')")
        migrate(conn)
        migrate(conn)
        assert conn.execute(
            "SELECT session_origin,session_origin_reservation_key,session_origin_transport FROM spawn_reservations",
        ).fetchone() == (None, None, "baseline")


def test_capture_failure_rolls_back_original_pool_claim_and_reservation(tmp_path, monkeypatch):
    from agent_dispatch import queue_session_origin

    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    publish(queue, pool())

    def fail(*args, **kwargs):
        raise TaskError("session snapshot unavailable")

    monkeypatch.setattr(queue_session_origin, "capture_cohort", fail)
    with pytest.raises(TaskError, match="session snapshot unavailable"):
        place(queue, task)
    assert queue.get(task.id) == task
    assert queue.task_pool_assignment(task.id) is None
    assert queue.list_reservations(task_id=task.id) == []


def test_concurrent_wire_choices_have_one_transactional_winner(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    barrier = Barrier(2)

    def choose(transport):
        barrier.wait()
        try:
            return bind(queue, reservation.key, startup_revision=1, transport=transport).session_origin_transport
        except TaskError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as threads:
        outcomes = list(threads.map(choose, ["baseline", "origin-v1"]))
    assert outcomes.count("refused") == 1
    assert queue.get_reservation(reservation.key).session_origin_transport == next(
        item for item in outcomes if item != "refused"
    )


@pytest.mark.parametrize("condition", ["revision", "fenced", "released", "terminal"])
def test_stale_startup_cannot_choose_a_wire_form(tmp_path, condition):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id)
    revision = 2 if condition == "revision" else 1
    if condition == "terminal":
        queue.fail_spawn(reservation.key, detail="no native")
    elif condition != "revision":
        column = "startup_fenced" if condition == "fenced" else "release_requested"
        with queue._connect() as conn:
            conn.execute(f"UPDATE spawn_reservations SET {column}=1 WHERE key=?", (reservation.key,))
    with pytest.raises(TaskError, match="current reserving startup"):
        bind(queue, reservation.key, startup_revision=revision, transport="origin-v1")
    assert queue.get_reservation(reservation.key).session_origin_transport is None


def test_current_http_binding_replay_and_strict_invalid_request(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id)
    with TestClient(create_app(queue, enable_mcp=False, verification_interval=0)) as api:
        support = api.get(f"/tasks/{task.id}/pool-start/contract").json()["pool_placement"]
        assert support["session_origin_binding_versions"] == [1]
        url = f"/spawn-reservations/{reservation.key}/session-origin"
        body = {"startup_revision": 1, "transport": "origin-v1"}
        response = api.post(url, json=body)
        assert response.status_code == 200 and response.json()["session_origin"] == reservation.session_origin
        assert api.post(url, json=body).json() == response.json()
        for invalid in (
            {**body, "startup_revision": True}, {**body, "transport": "future"},
            {**body, "session_origin": {}},
        ):
            assert api.post(url, json=invalid).status_code == 422
        assert api.post(url, json={**body, "transport": "baseline"}).status_code == 409


def test_current_client_lost_binding_reply_recovers_same_owner_without_native_send(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    lost = [True]
    with TestClient(create_app(queue, control_token="control", enable_mcp=False, verification_interval=0)) as api:
        def respond(request):
            response = api.request(
                request.method, str(request.url), content=request.content, headers=request.headers,
            )
            if request.method == "POST" and lost[0]:
                lost[0] = False
                assert response.status_code == 200
                raise httpx.ReadTimeout("binding response lost")
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://testserver", control_token="control", transport=httpx.MockTransport(respond),
        ) as client:
            with pytest.raises(httpx.ReadTimeout):
                client.bind_spawn_session_origin(reservation.key, startup_revision=1, transport="origin-v1")
            stored = client.get_reservation(reservation.key)
            assert stored["session_origin_transport"] == "origin-v1"
            assert stored["session_origin"] == reservation.session_origin
            confirmed = client.bind_spawn_session_origin(
                reservation.key, startup_revision=1, transport="origin-v1",
            )
            assert confirmed["session_origin"] == stored["session_origin"]
            assert confirmed["session_handle"] is None and confirmed["worktree"] is None


@pytest.mark.parametrize("versions", [None, [], [True], [2]])
def test_new_client_refuses_prior_or_invalid_coordinator_before_post(versions):
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={
                **pool_placement_contract(), "session_origin_binding_versions": versions,
            })
        pytest.fail("no binding sent to unsupported provider")

    with DispatchClient("http://fixture", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DispatchError, match="session origin binding"):
            client.bind_spawn_session_origin("reservation", startup_revision=1, transport="origin-v1")
    assert len(calls) == 1
