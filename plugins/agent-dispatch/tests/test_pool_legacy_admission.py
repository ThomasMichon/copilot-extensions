"""Configured cohorts fence fresh legacy spawning, not existing custody."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.ordinary_bridge import reserve_for_backend
from agent_dispatch.queue import Status, TaskQueue
from agent_dispatch.queue_pool_placement import ConfiguredPoolPlacementRequired
from agent_dispatch.registrar_configuration import (
    ConfiguredSupervisorCohort, SupervisorScopeIntent,
)
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, task

q = placement_controls.q


def observation(state: str = "complete") -> ConfiguredSupervisorCohort:
    digest = hashlib.sha256(b"synthetic configuration").hexdigest()
    if state == "empty":
        return ConfiguredSupervisorCohort("complete", digest, 0, ())
    if state == "unconfirmed":
        return ConfiguredSupervisorCohort(
            "unconfirmed", None, None, (), "pointer-registry-absent",
        )
    if state == "unavailable":
        return ConfiguredSupervisorCohort(
            "unavailable", digest, None, (), "configured-supervisors-invalid",
        )
    return ConfiguredSupervisorCohort(
        "complete", digest, 0,
        (SupervisorScopeIntent("host-a", "host-a", "linux"),),
    )


@pytest.mark.parametrize("state", ["complete", "empty", "unconfirmed", "unavailable"])
@pytest.mark.parametrize("driver", [None, "cli", "ordinary-bridge", "restricted-bridge"])
def test_projected_cohort_refuses_fresh_legacy_reservation_without_mutation(
    q: TaskQueue, state: str, driver: str | None,
) -> None:
    requested = task(q)
    q.observe_pool_configured_cohort(observation(state), expected_revision=0)
    before = q.pool_catalog_snapshot()
    events = q.events(requested.id)
    with pytest.raises(ConfiguredPoolPlacementRequired):
        q.reserve_spawn(requested.id, reserved_by="old-allocator", driver=driver)
    assert q.get(requested.id) == requested
    assert q.events(requested.id) == events
    assert q.list_reservations(task_id=requested.id) == []
    assert q.task_pool_assignment(requested.id) is None
    assert q.pool_catalog_snapshot() == before


@pytest.mark.parametrize("options", [
    {"allow_suspended_reembodiment": True},
    {"allocation_revision": 1},
    {"driver": "cli", "reserved_by": "allocator"},
])
def test_legacy_options_cannot_downgrade_queued_admission(
    q: TaskQueue, options: dict,
) -> None:
    q.observe_pool_configured_cohort(observation(), expected_revision=0)
    requested = task(q)
    with pytest.raises(ConfiguredPoolPlacementRequired):
        q.reserve_spawn(requested.id, **options)
    assert q.get(requested.id) == requested
    assert q.list_reservations(task_id=requested.id) == []


def test_reopen_and_later_unconfirmed_observation_do_not_reenable_legacy(
    q: TaskQueue, tmp_path: Path,
) -> None:
    q.observe_pool_configured_cohort(observation(), expected_revision=0)
    q.observe_pool_configured_cohort(observation("unconfirmed"), expected_revision=1)
    reopened = TaskQueue(tmp_path / "queue.db")
    requested = task(reopened)
    with pytest.raises(ConfiguredPoolPlacementRequired):
        reopened.reserve_spawn(requested.id)
    assert reopened.pool_configured_cohort().revision == 2
    assert reopened.get(requested.id) == requested
    assert reopened.list_reservations(task_id=requested.id) == []


def test_legacy_active_and_exclusive_replay_are_observation_not_spawn(q: TaskQueue) -> None:
    requested = task(q, exclusive_key="shared")
    original, reserved = q.reserve_spawn(requested.id, reserved_by="original")
    assert reserved
    sibling = task(q, exclusive_key="shared")
    q.observe_pool_configured_cohort(observation(), expected_revision=0)
    assert q.reserve_spawn(requested.id, reserved_by="other") == (original, False)
    assert q.reserve_spawn(sibling.id, reserved_by="other") == (original, False)
    assert q.get(requested.id) == requested and q.get(sibling.id) == sibling
    assert q.list_reservations(task_id=sibling.id) == []


@pytest.mark.parametrize("state", ["complete", "unconfirmed"])
def test_manual_claim_start_and_existing_suspended_reembodiment_are_preserved(
    q: TaskQueue, state: str,
) -> None:
    q.observe_pool_configured_cohort(observation(state), expected_revision=0)
    requested = task(q)
    assert q.claim_one("host-a/worktree", task_id=requested.id) is not None
    assert q.start(requested.id, "host-a/worktree").status == Status.STARTED
    q.suspend(requested.id, "host-a/worktree", reason="intentional wait")
    before = q.get(requested.id)
    reservation, reserved = q.reserve_spawn(
        requested.id, allow_suspended_reembodiment=True,
    )
    assert reserved and reservation.task_id == requested.id
    assert q.get(requested.id) == before
    assert q.task_pool_assignment(requested.id) is None


def test_no_projection_keeps_existing_legacy_queued_contract(q: TaskQueue) -> None:
    requested = task(q)
    reserved, created = q.reserve_spawn(requested.id, driver="ordinary-bridge")
    assert created and reserved.task_id == requested.id
    assert q.get(requested.id) == requested
    assert q.pool_configured_cohort().revision == 0
    assert q.task_pool_assignment(requested.id) is None


def test_completed_legacy_failure_does_not_authorize_another_queued_spawn(q: TaskQueue) -> None:
    requested = task(q)
    original, _ = q.reserve_spawn(requested.id)
    q.fail_spawn(original.key)
    q.observe_pool_configured_cohort(observation(), expected_revision=0)
    before = q.get_reservation(original.key)
    task_before = q.get(requested.id)
    with pytest.raises(ConfiguredPoolPlacementRequired):
        q.reserve_spawn(requested.id)
    assert q.get_reservation(original.key) == before
    assert len(q.list_reservations(task_id=requested.id)) == 1
    assert q.get(requested.id) == task_before


@pytest.mark.parametrize("path", ["/spawn-reservations", "/spawn-reservations/revisions"])
def test_old_http_caller_gets_explicit_refusal_not_pool_or_legacy_fallback(
    q: TaskQueue, path: str,
) -> None:
    q.observe_pool_configured_cohort(observation(), expected_revision=0)
    requested = task(q)
    client = TestClient(create_app(
        q, token=None, control_token="control", enable_mcp=False,
        verification_interval=0,
    ))
    body = {"task_id": requested.id, "driver": "ordinary-bridge", "reserved_by": "old"}
    if path.endswith("/revisions"):
        body["allocation_revision"] = 1
    response = client.post(path, json=body)
    assert response.status_code == 409
    assert response.json()["detail"] == ConfiguredPoolPlacementRequired().detail()
    assert q.get(requested.id) == requested
    assert q.list_reservations(task_id=requested.id) == []
    assert client.get("/health").json()["pool_placement"][
        "configured_cohort_reservation_fencing_versions"
    ] == [1]
    assert client.get("/health").json()["pool_placement"]["automatic_lane_migration"] is False


@pytest.mark.parametrize("ordinary,restricted", [(False, False), (True, False), (False, True)])
def test_actual_ordinary_client_preserves_refusal_without_replaying_another_path(
    q: TaskQueue, ordinary: bool, restricted: bool,
) -> None:
    from dataclasses import asdict

    q.observe_pool_configured_cohort(observation(), expected_revision=0)
    requested = task(q)
    app = TestClient(create_app(
        q, token=None, control_token=None, enable_mcp=False, verification_interval=0,
    ))
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        response = app.post(request.url.path, json=json.loads(request.content))
        return httpx.Response(response.status_code, json=response.json())

    with DispatchClient(
        "http://example.test", token=None, transport=httpx.MockTransport(respond),
    ) as client:
        with pytest.raises(DispatchError) as refusal:
            reserve_for_backend(
                client, asdict(requested), "allocator", ordinary=ordinary, restricted=restricted,
            )
    assert refusal.value.status_code == 409
    assert refusal.value.detail == ConfiguredPoolPlacementRequired().detail()
    assert calls == ["/spawn-reservations"]
    assert q.get(requested.id) == requested
    assert q.list_reservations(task_id=requested.id) == []


def test_new_explicit_placement_still_commits_claim_before_spawn(q: TaskQueue) -> None:
    publish(q, pool())
    requested = task(q)
    with pytest.raises(ConfiguredPoolPlacementRequired):
        q.reserve_spawn(requested.id)
    reservation, reserved = place(q, requested)
    assert reserved
    assert q.get(requested.id).status == Status.CLAIMED
    assert reservation.worktree is None and reservation.session_handle is None
    assert q.task_pool_assignment(requested.id).reservation_key == reservation.key


def test_projection_and_legacy_reservation_serialize_in_the_same_write_owner(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finish, projecting = Event(), Event(), Event()
    admission = q.require_worker_admission

    def wait_in_reservation() -> None:
        admission()
        entered.set()
        assert finish.wait(5)

    def project() -> None:
        projecting.set()
        q.observe_pool_configured_cohort(observation(), expected_revision=0)

    requested = task(q)
    monkeypatch.setattr(q, "require_worker_admission", wait_in_reservation)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reserve = executor.submit(q.reserve_spawn, requested.id)
        try:
            assert entered.wait(5)
            projection = executor.submit(project)
            assert projecting.wait(5)
        finally:
            finish.set()
        original, reserved = reserve.result(timeout=5)
        projection.result(timeout=5)
    assert reserved and q.reserve_spawn(requested.id) == (original, False)
    with pytest.raises(ConfiguredPoolPlacementRequired):
        q.reserve_spawn(task(q).id)
