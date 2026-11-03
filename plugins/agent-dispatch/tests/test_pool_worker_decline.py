"""A real worker decision retains the claim until its own closeout can settle."""

from __future__ import annotations

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue import Status, TaskError, TaskQueue
from tests import test_pool_placement_client as controls
from tests import test_supervisor_pool_startup as supervision
from tests.test_pool_placement import place, pool, publish, record_native, task

q = controls.q
api = controls.api


def prepared_worker(q: TaskQueue) -> tuple[str, dict]:
    publish(q, pool())
    requested = task(q, excludes=["capability:unrelated"])
    reservation, _ = place(q, requested)
    record_native(q, reservation)
    q.record_spawn(
        reservation.key, session_handle="native", startup_complete=True, startup_revision=1,
    )
    return requested.id, {
        "worker_id": "host-a/worktree", "native_id": "native",
        "generation": q.get(requested.id).generation, "startup_revision": 1,
        "reservation_key": reservation.key,
        "exclusions": ["pool:worker", "worktree:worktree"], "note": "The pool lacks the required tool.",
    }


def snapshot(q: TaskQueue, task_id: str) -> tuple:
    return (
        q.get(task_id), q.task_pool_assignment(task_id), q.pool_startup(task_id),
        q.list_reservations(task_id=task_id), q.pool_worker_decline(task_id),
        q.events(task_id),
    )


def test_decision_is_durable_without_start_routing_release_or_counter_reset(q: TaskQueue) -> None:
    task_id, binding = prepared_worker(q)
    before = snapshot(q, task_id)
    decline = q.prepare_pool_decline(task_id, **binding, now=101)
    after = snapshot(q, task_id)
    assert after[:4] == before[:4]
    assert decline.exclusions == ("pool:worker", "worktree:worktree")
    assert decline.blocker is None and decline.created_at == 101
    assert after[0].status == Status.CLAIMED and after[0].owner is None
    assert after[0].excludes == ["capability:unrelated"]
    assert len(after[5]) == len(before[5]) + 1
    reopened = TaskQueue(q.db_path)
    assert reopened.pool_worker_decline(task_id) == decline
    assert reopened.pool_startup_snapshot(task_id)["worker_decline"] == asdict(decline)
    assert q.prepare_pool_decline(task_id, **binding, now=10000) == decline
    assert snapshot(q, task_id) == after
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


@pytest.mark.parametrize("change", [
    {"worker_id": "host-b/worktree"}, {"worker_id": "host-a/other"},
    {"native_id": "foreign"}, {"generation": 99}, {"generation": True},
    {"startup_revision": 2}, {"startup_revision": True}, {"reservation_key": "foreign"},
    {"exclusions": []}, {"exclusions": ["machine:foreign"]},
    {"exclusions": ["pool:other"]}, {"exclusions": ["worktree:other"]},
    {"exclusions": ["env:linux"]}, {"exclusions": ["capability:unrelated"]},
    {"note": "   "},
])
def test_decline_refuses_foreign_stale_or_unscoped_decisions_without_mutation(
    q: TaskQueue, change: dict,
) -> None:
    task_id, binding = prepared_worker(q)
    before = snapshot(q, task_id)
    with pytest.raises(TaskError):
        q.prepare_pool_decline(task_id, **{**binding, **change})
    assert snapshot(q, task_id) == before


@pytest.mark.parametrize("reason", ["deadline", "held", "steer", "started", "incomplete"])
def test_first_decision_requires_a_current_completed_unheld_startup(q: TaskQueue, reason: str) -> None:
    task_id, binding = prepared_worker(q)
    now = 101
    if reason == "deadline":
        now = 700
    elif reason == "held":
        q.set_hold(task_id, reason="operator hold", actor="operator")
    elif reason == "steer":
        with q._connect() as conn:
            conn.execute("UPDATE tasks SET awaiting_steer=1 WHERE id=?", (task_id,))
    elif reason == "started":
        q.start(task_id, binding["worker_id"], owner_session_id="native",
                expected_generation=binding["generation"])
    else:
        with q._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET startup_complete=0 WHERE key=?",
                         (binding["reservation_key"],))
    before = snapshot(q, task_id)
    with pytest.raises(TaskError):
        q.prepare_pool_decline(task_id, **binding, now=now)
    assert snapshot(q, task_id) == before


def test_closeout_blocker_and_identical_retries_preserve_original_decision(q: TaskQueue) -> None:
    task_id, binding = prepared_worker(q)
    original = q.prepare_pool_decline(task_id, **binding)
    arguments = {key: value for key, value in binding.items() if key not in {"note", "exclusions"}}
    blocked = q.record_pool_decline_blocker(
        task_id, **arguments, detail="A pull request is still open.", now=9999,
    )
    assert blocked.created_at == original.created_at
    assert blocked.blocker == "A pull request is still open." and blocked.updated_at == 9999
    before = snapshot(q, task_id)
    assert q.record_pool_decline_blocker(
        task_id, **arguments, detail=blocked.blocker, now=10000,
    ) == blocked
    assert q.prepare_pool_decline(task_id, **binding, now=10000) == blocked
    assert snapshot(q, task_id) == before
    for changed in ({"note": "Different decision"}, {"exclusions": ["machine:host-a"]}):
        with pytest.raises(TaskError, match="original decision"):
            q.prepare_pool_decline(task_id, **{**binding, **changed}, now=10000)
    with pytest.raises(TaskError, match="incarnation"):
        q.record_pool_decline_blocker(
            task_id, **{**arguments, "native_id": "foreign"}, detail="not mine",
        )
    assert snapshot(q, task_id) == before


def test_pending_decline_blocks_acceptance_exhaustion_retry_and_capacity_release(q: TaskQueue) -> None:
    task_id, binding = prepared_worker(q)
    q.prepare_pool_decline(task_id, **binding)
    before = snapshot(q, task_id)
    key = binding["reservation_key"]
    operations = [
        lambda: q.start(task_id, binding["worker_id"], owner_session_id="native",
                        expected_generation=binding["generation"]),
        lambda: q.exhaust_pool_startup(key, generation=binding["generation"], startup_revision=1, now=10000),
        lambda: q.observe_pool_reachable(key, generation=binding["generation"],
                                        startup_revision=1, native_id="native"),
        lambda: q.request_spawn_release(key),
        lambda: q.fail_spawn(key, force=True, confirmed_absent=True),
        lambda: q.retire_spawn(key, exact_absence=True),
        lambda: q.record_spawn(key, startup_complete=False, startup_revision=1),
    ]
    for operation in operations:
        with pytest.raises(TaskError, match="own-workspace closeout"):
            operation()
        assert snapshot(q, task_id) == before
    assert q.reconcile_liveness(resolver=lambda *args: pytest.fail("unaccepted worker is not probed"))["checked"] == 0


def test_concurrent_decline_is_one_immutable_decision(q: TaskQueue) -> None:
    task_id, binding = prepared_worker(q)
    barrier = Barrier(2)

    def prepare(note: str) -> bool:
        barrier.wait(timeout=5)
        try:
            q.prepare_pool_decline(task_id, **{**binding, "note": note})
            return True
        except TaskError as exc:
            assert "original decision" in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(prepare, ("one reason", "another reason"))) == [False, True]
    assert q.get(task_id).status == Status.CLAIMED


def test_accept_and_decline_are_mutually_exclusive_decisions(q: TaskQueue) -> None:
    task_id, binding = prepared_worker(q)
    barrier = Barrier(2)

    def decide(accept: bool) -> bool:
        barrier.wait(timeout=5)
        try:
            if accept:
                q.start(task_id, binding["worker_id"], owner_session_id="native",
                        expected_generation=binding["generation"])
            else:
                q.prepare_pool_decline(task_id, **binding)
            return True
        except TaskError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(decide, (False, True))) == [False, True]
    assert (q.get(task_id).status == Status.STARTED) is (q.pool_worker_decline(task_id) is None)


def test_explicit_terminal_disposition_preserves_decline_but_does_not_deadlock_retirement(q: TaskQueue) -> None:
    task_id, binding = prepared_worker(q)
    original = q.prepare_pool_decline(task_id, **binding)
    q.abandon(task_id, permitted=True, reason="Explicit terminal disposition")
    retained = q.request_spawn_release(binding["reservation_key"])
    assert retained.release_requested
    assert q.pool_worker_decline(task_id) == original
    assert q.get(task_id).status == Status.ABANDONED


def test_real_transport_prepares_blocker_and_exposes_current_pending_closeout(
    q: TaskQueue, api: TestClient,
) -> None:
    task_id, binding = prepared_worker(q)
    requests = []
    with controls.connected(api, requests, control=False) as client:
        decline = client.prepare_pool_decline(task_id, **binding)
        arguments = {key: value for key, value in binding.items() if key not in {"note", "exclusions"}}
        blocked = client.record_pool_decline_blocker(task_id, **arguments, detail="Unmerged source remains.")
        observed = client.get(task_id)
    assert observed["worker_decline"] == blocked
    assert blocked["exclusions"] == decline["exclusions"]
    assert observed["status"] == Status.CLAIMED and observed["excludes"] == ["capability:unrelated"]
    assert [r.method for r in requests] == ["GET", "POST", "GET", "POST", "GET"]
    url = f"/tasks/{task_id}/pool-decline"
    assert api.post(url, json={"version": 1, **binding}).status_code == 401
    assert api.post(url, headers=controls.placement_controls.headers(),
                    json={"version": True, **binding}).status_code == 422
    assert api.post(url, headers=controls.placement_controls.headers(),
                    json={"version": 1, **binding, "finalized": True}).status_code == 422
    from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
    token = mint_task_scope_token(
        "control", task_id=task_id, worker_id=binding["worker_id"], owner_session_id="native",
        generation=binding["generation"], epoch=0,
    )
    assert api.post(url, headers={TASK_SCOPE_HEADER: token},
                    json={"version": 1, **binding}).status_code == 403


@pytest.mark.parametrize("contract", [None, [], [True], [2]])
def test_unsupported_preparation_never_writes_or_downgrades(contract: object) -> None:
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        from agent_dispatch.queue_pool_placement import pool_placement_contract
        return httpx.Response(200, json={"pool_placement": {
            **pool_placement_contract(), "worker_decline_preparation_versions": contract,
        }})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(send)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.prepare_pool_decline(
                "task", "host/worktree", native_id="native", generation=1, startup_revision=1,
                reservation_key="reservation", exclusions=["worktree:worktree"], note="Not for this worker.",
            )
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("lost", [False, True])
def test_lost_or_malformed_response_retains_decision_without_replay(
    q: TaskQueue, api: TestClient, lost: bool,
) -> None:
    task_id, binding = prepared_worker(q)
    requests = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.method != "POST":
            return response
        if lost:
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return httpx.Response(200, json={"decline": {}})

    with controls.connected(api, requests, control=False, rewrite=rewrite) as client:
        with pytest.raises(httpx.ReadTimeout if lost else DispatchError):
            client.prepare_pool_decline(task_id, **binding)
    assert q.pool_worker_decline(task_id) is not None
    assert q.get(task_id).status == Status.CLAIMED
    assert sum(request.method == "POST" for request in requests) == 1


def test_existing_supervision_retains_decline_instead_of_nudging_or_exhausting(
    q: TaskQueue, api: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, clock, requested, reservation, probes, nudges, _ = supervision.setup(q, api, monkeypatch)
    q.prepare_pool_decline(
        requested.id, "host-a/worktree", native_id="native", generation=q.get(requested.id).generation,
        startup_revision=1, reservation_key=reservation.key,
        exclusions=["worktree:worktree"], note="Not for this worker.",
    )
    before = snapshot(q, requested.id)
    with owner.client:
        assert supervision.cycle(owner, clock, 10000) == 0
    assert not probes and not nudges and snapshot(q, requested.id) == before
