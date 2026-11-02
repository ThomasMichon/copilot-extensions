"""Real startup transport controls over durable placement and supervision owners."""

from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.pool_startup_contract import POOL_STARTUP_PATH, pool_startup_contract
from agent_dispatch.queue import SpawnState, Status, TaskQueue
from tests.test_pool_placement import pool, publish, record_native, task
from tests import test_pool_placement_client as caller_controls

q = caller_controls.q
api = caller_controls.api
connected = caller_controls.connected


def claim(q: TaskQueue, api: TestClient, requests: list[httpx.Request]) -> tuple:
    revision = publish(q, pool())
    requested = task(q)
    with connected(api, requests) as client:
        placed = client.place_pool_startup(
            requested.id, reserved_by="allocator", catalog_revision=revision, startup_timeout=42,
        )
    return q.get(requested.id), q.get_reservation(placed["reservation"]["key"])


def ready(q: TaskQueue, reservation) -> None:
    record_native(q, reservation)
    q.record_spawn(
        reservation.key, session_handle="native", startup_complete=True,
        startup_revision=reservation.startup_revision,
    )


def invoke(client: DispatchClient, action: str, task, reservation) -> object:
    if action == "snapshot":
        return client.pool_startup_snapshot(task.id)
    arguments = {"generation": task.generation, "startup_revision": reservation.startup_revision}
    if action != "exhaust":
        arguments["native_id"] = "native"
    return getattr(client, {
        "reachable": "observe_pool_reachable", "nudge": "take_pool_nudge",
        "exhaust": "exhaust_pool_startup",
    }[action])(task.id, reservation.key, **arguments)


def test_configured_deadline_and_replay_are_atomic_and_durable(q: TaskQueue, api: TestClient) -> None:
    requests = []
    t, r = claim(q, api, requests)
    budget = q.pool_startup(t.id)
    assert budget.startup_deadline == 142 and budget.startup_timeout == 42
    with connected(api, requests) as client:
        observed = client.pool_startup_snapshot(t.id)
        assert observed["budget"]["generation"] == t.generation
        assert observed["native_id"] is None and observed["reservation"]["key"] == r.key
        replay = client.place_pool_startup(
            t.id, reserved_by="other", catalog_revision=q.pool_catalog_snapshot().revision,
            startup_timeout=999,
        )
        assert replay["reserved"] is False and replay["configured_startup_timeout"] is None
    assert TaskQueue(q.db_path).pool_startup(t.id) == budget
    ready(q, r)
    q.record_spawn(r.key, startup_complete=False, startup_revision=1, now=110)
    assert q.pool_startup(t.id).startup_timeout == 42
    assert q.pool_startup(t.id).startup_deadline == 152
    assert q.pool_startup(t.id).startup_attempts == 2
    assert len(q.list_reservations(task_id=t.id)) == 1
    assert not any(request.url.path == "/pool-placements" for request in requests)


def test_real_reachable_nudge_and_exhaustion_keep_unknown_capacity(
    q: TaskQueue, api: TestClient, monkeypatch,
) -> None:
    requests = []
    t, r = claim(q, api, requests)
    ready(q, r)
    clock = [101]
    monkeypatch.setattr(q, "_now", lambda now: clock[0] if now is None else now)
    with connected(api, requests) as client:
        observed = invoke(client, "reachable", t, r)
        assert observed["reachable_at"] == 101 and observed["decision_deadline"] == 1901
        clock[0] = 400
        assert invoke(client, "nudge", t, r) is None
        clock[0] = 401
        assert invoke(client, "nudge", t, r)["next_nudge_at"] == 701
        assert invoke(client, "nudge", t, r) is None
        assert q.get(t.id).status == Status.CLAIMED and q.get(t.id).owner is None
        clock[0] = 1901
        exhausted = invoke(client, "exhaust", t, r)
        assert exhausted["status"] == Status.QUEUED and exhausted["generation"] == t.generation + 1
        snapshot = invoke(client, "snapshot", t, r)
        assert snapshot["native_id"] == "native"
        assert snapshot["budget"]["exhaustion_reason"] == "decision_timeout"
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and retained.session_handle == "native"
    assert retained.startup_fenced and retained.release_requested
    assert q.pool_startup(t.id).startup_attempts == 1 and q.pool_startup(t.id).cooldown_stage == 1
    with pytest.raises(DispatchError):
        with connected(api, []) as client:
            client.start_pool_task(
                t.id, "host-a/worktree", owner_session_id="native", expected_generation=t.generation,
            )


def test_missing_native_exhaustion_retains_original_late_return(
    q: TaskQueue, api: TestClient, monkeypatch,
) -> None:
    t, r = claim(q, api, [])
    monkeypatch.setattr(q, "_now", lambda now: 142 if now is None else now)
    with connected(api, []) as client:
        assert invoke(client, "exhaust", t, r)["status"] == Status.QUEUED
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and not retained.startup_fenced
    assert not retained.startup_complete and not retained.exclusive_released
    q.request_spawn_release(
        r.key, session_handle="late-native", finished_startup_revision=1,
    )
    assert q.get_reservation(r.key).session_handle == "late-native"
    assert q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("action", ["reachable", "nudge", "exhaust"])
@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("generation", True), ("generation", 0),
    ("startup_revision", True), ("startup_revision", 0), ("unexpected", 1),
])
def test_strict_effect_body_refuses_without_mutation(
    q: TaskQueue, api: TestClient, action: str, field: str, value: object,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    before = q.pool_startup(t.id), q.get(t.id), q.get_reservation(r.key)
    body = {
        "version": 1, "reservation_key": r.key, "generation": t.generation, "startup_revision": 1,
        **({"native_id": "native"} if action != "exhaust" else {}),
        field: value,
    }
    response = api.post(
        f"{POOL_STARTUP_PATH}/tasks/{t.id}/{action}", json=body,
        headers=caller_controls.placement_controls.headers(),
    )
    assert response.status_code == 422
    assert (q.pool_startup(t.id), q.get(t.id), q.get_reservation(r.key)) == before


@pytest.mark.parametrize("action", ["reachable", "nudge", "exhaust"])
@pytest.mark.parametrize("changed", ["task", "generation", "revision", "held"])
def test_exact_current_binding_refuses_wrong_or_held_claim(
    q: TaskQueue, api: TestClient, monkeypatch, action: str, changed: str,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    if action == "nudge":
        q.observe_pool_reachable(r.key, generation=t.generation, startup_revision=1, native_id="native", now=101)
    target = task(q) if changed == "task" else t
    if changed == "held":
        with q._connect() as conn:
            conn.execute("UPDATE tasks SET hold_reason='operator' WHERE id=?", (t.id,))
    monkeypatch.setattr(q, "_now", lambda now: (1901 if action == "exhaust" else 102) if now is None else now)
    body = {
        "version": 1, "reservation_key": r.key,
        "generation": t.generation + (changed == "generation"),
        "startup_revision": 1 + (changed == "revision"),
        **({"native_id": "native"} if action != "exhaust" else {}),
    }
    before = q.pool_startup(t.id), q.get(t.id), q.get_reservation(r.key)
    response = api.post(
        f"{POOL_STARTUP_PATH}/tasks/{target.id}/{action}", json=body,
        headers=caller_controls.placement_controls.headers(),
    )
    assert response.status_code == 409
    assert (q.pool_startup(t.id), q.get(t.id), q.get_reservation(r.key)) == before


@pytest.mark.parametrize("action", ["snapshot", "reachable", "nudge", "exhaust"])
def test_control_authority_is_required_for_all_startup_operations(
    q: TaskQueue, api: TestClient, action: str,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    before = q.pool_startup(t.id), q.get(t.id), q.get_reservation(r.key)
    with connected(api, [], control=False) as client:
        with pytest.raises(DispatchError) as failure:
            invoke(client, action, t, r)
        assert failure.value.status_code == 403
    assert (q.pool_startup(t.id), q.get(t.id), q.get_reservation(r.key)) == before


@pytest.mark.parametrize("contract", [
    None, {}, {**pool_startup_contract(), "supported_versions": [True]},
    {**pool_startup_contract(), "durable_claim_budget": 1},
    {**pool_startup_contract(), "same_native_decision": False},
    {**pool_startup_contract(), "startup_attempt_limit": True},
    {**pool_startup_contract(), "decision_seconds": 600},
    {**pool_startup_contract(), "exhaustion_retains_capacity": False},
])
def test_incompatible_contract_refuses_before_effect(contract: object) -> None:
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=contract)

    with DispatchClient("http://test", token="data", control_token="control", transport=httpx.MockTransport(send)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.place_pool_startup("task", reserved_by="allocator", catalog_revision=1, startup_timeout=42)
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", f"{POOL_STARTUP_PATH}/contract"),
    ]


@pytest.mark.parametrize("action", ["place", "snapshot", "reachable", "nudge", "exhaust"])
def test_actual_provider_rollback_never_uses_legacy_mutations(
    q: TaskQueue, api: TestClient, action: str,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    old = FastAPI()
    writes = []

    @old.post("/pool-placements")
    @old.post("/spawn-reservations")
    @old.post("/tasks/{task_id}/start")
    def legacy() -> dict:
        writes.append("legacy")
        return {}

    requests = []
    with TestClient(old) as legacy_api:
        def send(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            target = api if request.url.path.endswith("/contract") else legacy_api
            result = target.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
            return httpx.Response(result.status_code, content=result.content)

        with DispatchClient(
            "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
        ) as client:
            with pytest.raises(DispatchUpgradeRequired):
                if action == "place":
                    client.place_pool_startup(
                        t.id, reserved_by="allocator", catalog_revision=1, startup_timeout=42,
                    )
                else:
                    invoke(client, action, t, r)
    assert len(requests) == 2 and not writes
    assert q.get(t.id).status == Status.CLAIMED and q.pool_startup(t.id).reachable_at is None


@pytest.mark.parametrize("action", ["reachable", "nudge", "exhaust"])
@pytest.mark.parametrize("field,value", [
    ("task_id", "wrong"), ("reservation_key", "wrong"),
    ("generation", True), ("startup_revision", 2),
])
def test_corrupted_success_binding_is_unknown_without_replay(
    q: TaskQueue, api: TestClient, monkeypatch, action: str, field: str, value: object,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    if action != "reachable":
        q.observe_pool_reachable(r.key, generation=t.generation, startup_revision=1, native_id="native", now=101)
    clock = {"reachable": 101, "nudge": 401, "exhaust": 1901}[action]
    monkeypatch.setattr(q, "_now", lambda now: clock if now is None else now)
    requests = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.method != "POST":
            return response
        payload = response.json()
        payload["result"]["binding"][field] = value
        return httpx.Response(200, json=payload)

    with connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchError, match="unknown") as failure:
            invoke(client, action, t, r)
        assert failure.value.status_code == 502
    assert len([request for request in requests if request.method == "POST"]) == 1
    assert len(q.list_reservations(task_id=t.id)) == 1
    assert q.pool_startup(t.id).startup_attempts == 1
    assert q.get(t.id).status == (Status.QUEUED if action == "exhaust" else Status.CLAIMED)


@pytest.mark.parametrize("field,value", [
    ("startup_timeout", 0), ("startup_timeout", True), ("version", True),
    ("unexpected", 1),
])
def test_configured_placement_strict_schema_is_pre_effect(
    q: TaskQueue, api: TestClient, field: str, value: object,
) -> None:
    revision = publish(q, pool())
    t = task(q)
    body = {
        "version": 1, "task_id": t.id, "reserved_by": "allocator",
        "catalog_revision": revision, "startup_timeout": 42, field: value,
    }
    response = api.post(
        f"{POOL_STARTUP_PATH}/place", json=body,
        headers=caller_controls.placement_controls.headers(),
    )
    assert response.status_code == 422
    assert q.get(t.id).status == Status.QUEUED and not q.list_reservations(task_id=t.id)


def test_legacy_explicit_caller_keeps_default_timeout_and_response(q: TaskQueue, api: TestClient) -> None:
    revision = publish(q, pool())
    t = task(q)
    with connected(api, []) as client:
        placed = client.place_pool_task(t.id, reserved_by="allocator", catalog_revision=revision)
    assert set(placed) == {"protocol", "version", "reservation", "assignment", "reserved"}
    assert q.pool_startup(t.id).startup_timeout == 600 and q.pool_startup(t.id).startup_deadline == 700


def test_invalid_snapshot_native_and_budget_cannot_authorize_work(q: TaskQueue, api: TestClient) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    original = q.pool_startup_snapshot(t.id)
    for changed in ("native_id", "startup_timeout", "startup_revision", "reservation_key"):
        def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
            if request.url.path.endswith("/contract"):
                return response
            payload = deepcopy(response.json())
            value = payload["result"]
            if changed == "native_id":
                value["native_id"] = True
            elif changed == "startup_timeout":
                value["budget"]["startup_timeout"] = True
            elif changed == "startup_revision":
                value["reservation"]["startup_revision"] = True
            else:
                value["budget"]["reservation_key"] = "different"
            return httpx.Response(200, json=payload)

        with connected(api, [], rewrite=rewrite) as client:
            with pytest.raises(DispatchError, match="unknown"):
                client.pool_startup_snapshot(t.id)
    assert q.pool_startup_snapshot(t.id) == original


def test_transport_loss_remains_unknown_and_never_retries(q: TaskQueue, api: TestClient) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    requests = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost", request=request)
        return response

    with connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(httpx.ReadTimeout):
            invoke(client, "reachable", t, r)
    assert q.pool_startup(t.id).reachable_at == 100
    assert len([request for request in requests if request.method == "POST"]) == 1
    assert q.get(t.id).status == Status.CLAIMED


@pytest.mark.parametrize("kind", ["startup_failed", "startup_rejected", "startup_unknown"])
def test_classified_caller_outcome_retains_claim_and_does_not_prove_absence(
    q: TaskQueue, api: TestClient, kind: str,
) -> None:
    t, r = claim(q, api, [])
    with connected(api, []) as client:
        for _ in range(2):
            outcome = client.record_pool_startup_outcome(
                t.id, r.key, generation=t.generation, startup_revision=1,
                outcome_kind=kind, detail="caller result",
            )
            assert outcome["outcome_kind"] == kind and outcome["outcome_revision"] == 1
    retained = q.get_reservation(r.key)
    assert retained.startup_complete == (kind != "startup_unknown")
    assert not retained.startup_fenced and not retained.exclusive_released
    assert retained.session_handle is None
    assert q.get(t.id).status == Status.CLAIMED and q.get(t.id).owner is None
    assert q.pool_startup(t.id).startup_attempts == 1 and q.pool_startup(t.id).cooldown_stage == 0
    with connected(api, []) as client:
        with pytest.raises(DispatchError):
            client.exhaust_pool_startup(
                t.id, r.key, generation=t.generation, startup_revision=1, reason="attempts",
            )
    assert q.get(t.id).status == Status.CLAIMED


def test_three_real_failed_startups_exhaust_without_waiting_for_deadline(
    q: TaskQueue, api: TestClient,
) -> None:
    t, r = claim(q, api, [])
    record_native(q, r)
    with connected(api, []) as client:
        for revision in (1, 2, 3):
            result = client.record_pool_startup_outcome(
                t.id, r.key, generation=t.generation, startup_revision=revision,
                outcome_kind="startup_failed", detail=f"attempt-{revision}", native_id="native",
            )
            assert result["startup_attempts"] == revision
            if revision < 3:
                with pytest.raises(DispatchError):
                    client.exhaust_pool_startup(
                        t.id, r.key, generation=t.generation, startup_revision=revision, reason="attempts",
                    )
                q.record_spawn(
                    r.key, session_handle="native", startup_complete=False, startup_revision=revision,
                )
                assert q.pool_startup(t.id).outcome_kind is None
        before = q.pool_startup(t.id)
        exhausted = client.exhaust_pool_startup(
            t.id, r.key, generation=t.generation, startup_revision=3, reason="attempts",
        )
        assert exhausted["status"] == Status.QUEUED
    assert before.startup_deadline == 142
    assert exhausted["not_before"] == 3700
    assert q.pool_startup(t.id).exhaustion_reason == "startup_attempts_exhausted"
    assert q.pool_startup(t.id).outcome_detail == "attempt-3"
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and retained.startup_fenced
    assert retained.session_handle == "native" and retained.worktree == "worktree"
    assert not retained.exclusive_released and len(q.list_reservations(task_id=t.id)) == 1
    assert TaskQueue(q.db_path).pool_startup(t.id) == q.pool_startup(t.id)


def test_unknown_caller_can_report_its_later_completed_failure_without_reset(
    q: TaskQueue, api: TestClient,
) -> None:
    t, r = claim(q, api, [])
    with connected(api, []) as client:
        for kind in ("startup_unknown", "startup_failed"):
            client.record_pool_startup_outcome(
                t.id, r.key, generation=t.generation, startup_revision=1,
                outcome_kind=kind, detail="original invocation",
            )
        with pytest.raises(DispatchError):
            client.record_pool_startup_outcome(
                t.id, r.key, generation=t.generation, startup_revision=1,
                outcome_kind="startup_rejected", detail="different invocation",
            )
    assert q.pool_startup(t.id).startup_attempts == 1 and q.get_reservation(r.key).startup_complete
    assert q.pool_startup(t.id).outcome_kind == "startup_failed"


@pytest.mark.parametrize("wrong", ["native", "task", "generation", "revision", "completed"])
def test_caller_failure_requires_actual_current_native_and_original_completion(
    q: TaskQueue, api: TestClient, wrong: str,
) -> None:
    t, r = claim(q, api, [])
    record_native(q, r)
    if wrong == "completed":
        q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    target = task(q) if wrong == "task" else t
    before = q.get(t.id), q.get_reservation(r.key), q.pool_startup(t.id)
    with connected(api, []) as client:
        with pytest.raises(DispatchError):
            client.record_pool_startup_outcome(
                target.id, r.key, generation=t.generation + (wrong == "generation"),
                startup_revision=1 + (wrong == "revision"), outcome_kind="startup_failed",
                detail="caller result", native_id="different" if wrong == "native" else "native",
            )
    assert (q.get(t.id), q.get_reservation(r.key), q.pool_startup(t.id)) == before


def test_reachable_worker_cannot_report_a_startup_failure_or_exhausted_attempts(
    q: TaskQueue, api: TestClient,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    with connected(api, []) as client:
        invoke(client, "reachable", t, r)
        before = q.pool_startup(t.id)
        with pytest.raises(DispatchError):
            client.record_pool_startup_outcome(
                t.id, r.key, generation=t.generation, startup_revision=1,
                outcome_kind="startup_failed", detail="not a worker decline", native_id="native",
            )
        with pytest.raises(DispatchError):
            client.exhaust_pool_startup(
                t.id, r.key, generation=t.generation, startup_revision=1, reason="attempts",
            )
    assert q.pool_startup(t.id) == before and q.get(t.id).status == Status.CLAIMED


@pytest.mark.parametrize("kind", ["startup_failed", "startup_rejected", "startup_unknown"])
def test_classified_original_outcome_cannot_open_reachable_decision_clock(
    q: TaskQueue, api: TestClient, kind: str,
) -> None:
    t, r = claim(q, api, [])
    record_native(q, r)
    with connected(api, []) as client:
        client.record_pool_startup_outcome(
            t.id, r.key, generation=t.generation, startup_revision=1,
            outcome_kind=kind, detail="original caller outcome", native_id="native",
        )
        before = q.get(t.id), q.get_reservation(r.key), q.pool_startup(t.id)
        with pytest.raises(DispatchError):
            invoke(client, "reachable", t, r)
        assert (q.get(t.id), q.get_reservation(r.key), q.pool_startup(t.id)) == before
        budget = q.pool_startup(t.id)
        assert budget.reachable_at is None and budget.decision_deadline is None
        assert budget.next_nudge_at is None and budget.outcome_kind == kind


@pytest.mark.parametrize("changed", ["native", "decision_clock", "outcome_shape"])
def test_mutation_response_validates_native_decision_and_outcome_shape(
    q: TaskQueue, api: TestClient, changed: str,
) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    requests = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.method != "POST":
            return response
        payload = response.json()
        if changed == "native":
            payload["result"]["binding"]["native_id"] = "different"
        elif changed == "decision_clock":
            payload["result"]["observation"]["decision_deadline"] = None
        else:
            payload["result"]["observation"]["outcome_kind"] = "startup_failed"
        return httpx.Response(200, json=payload)

    with connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchError, match="unknown"):
            invoke(client, "reachable", t, r)
    assert q.get(t.id).status == Status.CLAIMED
    assert len([request for request in requests if request.method == "POST"]) == 1


def test_snapshot_after_current_acceptance_reports_only_real_reset(q: TaskQueue, api: TestClient) -> None:
    t, r = claim(q, api, [])
    ready(q, r)
    with connected(api, []) as client:
        client.start_pool_task(t.id, "host-a/worktree", owner_session_id="native", expected_generation=t.generation)
        observed = client.pool_startup_snapshot(t.id)
        assert observed["task"]["status"] == Status.STARTED
        assert observed["budget"]["startup_attempts"] == 0
        assert observed["budget"]["cooldown_stage"] == 0
        assert observed["native_id"] == "native"
        with pytest.raises(DispatchError):
            invoke(client, "exhaust", t, r)


def test_configured_claim_rejection_is_structured_and_does_not_start_an_episode(
    q: TaskQueue, api: TestClient,
) -> None:
    revision = publish(q, pool())
    t = task(q, not_before=200)
    with connected(api, []) as client:
        with pytest.raises(DispatchError) as failure:
            client.place_pool_startup(t.id, reserved_by="allocator", catalog_revision=revision, startup_timeout=42)
    assert failure.value.detail["code"] == "pool_claim_rejected"
    assert q.get(t.id).status == Status.QUEUED and q.pool_startup(t.id) is None
    assert not q.list_reservations(task_id=t.id)
