"""Suspended pool recovery uses real queue fences, not transport acceptance."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
import httpx

from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.queue import SpawnState, Status, TaskError, TaskQueue
from agent_dispatch.queue_pool_recovery import prepare_recovery_wake, reconcile_recoveries
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import headers, place, pool, publish, record_native, task
from tests.test_pool_placement_client import connected

q = placement_controls.q


def suspended(q: TaskQueue) -> tuple[str, str, int]:
    publish(q, pool())
    t = task(q)
    reservation, _ = place(q, t)
    record_native(q, reservation)
    q.record_spawn(reservation.key, session_handle="native", startup_complete=True, startup_revision=1)
    started = q.start(
        t.id, "host-a/worktree", owner_session_id="native",
        expected_generation=q.get(t.id).generation, now=101,
    )
    q.suspend(t.id, started.owner, reason="intentional wait", cooldown_seconds=None, now=102)
    return t.id, reservation.key, started.generation


def request(q: TaskQueue, task_id: str, now: float = 103) -> int:
    q.resume(task_id, "host-a/worktree", wake_requested=True, now=now)
    return q.pool_startup(task_id).recovery_revision


def affirm(q: TaskQueue, task_id: str, generation: int, revision: int, now: float = 105):
    return q.start(
        task_id, "host-a/worktree", owner_session_id="native", expected_generation=generation,
        expected_owner_session_id="native", pool_recovery_revision=revision, now=now,
    )


def test_intentional_wait_is_not_recovery_and_repeated_request_preserves_episode(q: TaskQueue) -> None:
    task_id, key, generation = suspended(q)
    before = q.get(task_id), q.pool_startup(task_id), q.get_reservation(key)
    assert reconcile_recoveries(q, now=10000) == 0
    assert (q.get(task_id), q.pool_startup(task_id), q.get_reservation(key)) == before
    revision = request(q, task_id)
    pending = q.get(task_id), q.pool_startup(task_id), q.list_wakes(task_id)
    assert pending[0].status == Status.SUSPENDED and pending[0].generation == generation
    assert pending[0].owner_session_id == "native" and pending[0].lease_expires_at is None
    assert request(q, task_id, now=104) == revision
    assert (q.get(task_id), q.pool_startup(task_id), q.list_wakes(task_id)) == pending
    assert TaskQueue(q.db_path).pool_startup(task_id) == pending[1]


def test_delivered_wake_waits_for_current_worker_affirmation(q: TaskQueue) -> None:
    task_id, key, generation = suspended(q)
    revision = request(q, task_id)
    wake = q.claim_due_wake(now=104)
    assert wake.pool_recovery_revision == revision
    q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=104)
    assert reconcile_recoveries(q, now=105) == 1
    budget = q.pool_startup(task_id)
    assert budget.startup_attempts == 1 and budget.reachable_at == 104
    assert budget.decision_deadline == 1904 and budget.next_nudge_at == 404
    assert q.get(task_id).status == Status.SUSPENDED
    with pytest.raises(TaskError, match="recovery revision"):
        q.start(task_id, "host-a/worktree", owner_session_id="native",
                expected_generation=generation, now=105)
    started = affirm(q, task_id, generation, revision)
    assert started.status == Status.STARTED and started.owner == "host-a/worktree"
    assert started.generation == generation and started.lease_expires_at > 105
    reset = q.pool_startup(task_id)
    assert reset.startup_attempts == reset.cooldown_stage == 0
    assert reset.recovery_requested_at is None and reset.recovery_revision == revision
    assert q.get_reservation(key).session_handle == "native"
    assert affirm(q, task_id, generation, revision, now=106) == started


@pytest.mark.parametrize("change", ["revision", "native", "generation", "hold"])
def test_stale_or_held_affirmation_cannot_mutate_recovery(q: TaskQueue, change: str) -> None:
    task_id, key, generation = suspended(q)
    revision = request(q, task_id)
    if change == "hold":
        q.set_hold(task_id, reason="operator hold", actor="operator", now=104)
    before = q.get(task_id), q.pool_startup(task_id), q.get_reservation(key)
    with pytest.raises(TaskError):
        q.start(
            task_id, "host-a/worktree",
            owner_session_id="other" if change == "native" else "native",
            expected_owner_session_id="native",
            expected_generation=generation + int(change == "generation"),
            pool_recovery_revision=revision + int(change == "revision"), now=105,
        )
    assert (q.get(task_id), q.pool_startup(task_id), q.get_reservation(key)) == before


def test_three_known_pre_send_failures_exhaust_without_releasing_capacity(q: TaskQueue) -> None:
    task_id, key, generation = suspended(q)
    revision = request(q, task_id)
    for attempt in range(1, 4):
        wake = q.claim_due_wake(now=103 + attempt)
        q.finish_wake(wake.id, wake.delivery_token, delivered=False, error="pre-send refusal",
                      now=103 + attempt)
        assert q.pool_startup(task_id).startup_attempts == attempt
        assert reconcile_recoveries(q, now=104 + attempt) == 1
    result, reservation = q.get(task_id), q.get_reservation(key)
    assert result.status == Status.QUEUED and result.generation == generation + 1
    assert result.owner is None and result.not_before == 3707
    assert reservation.state == SpawnState.RELEASING and reservation.startup_fenced
    assert reservation.release_requested and reservation.session_handle == "native"
    assert q.pool_startup(task_id).cooldown_stage == 1
    with pytest.raises(TaskError):
        affirm(q, task_id, generation, revision, now=108)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q), now=108)
    assert len(q.list_wakes(task_id)) == 3


def test_unknown_send_survives_restart_then_fences_late_acceptance(q: TaskQueue) -> None:
    task_id, key, generation = suspended(q)
    revision = request(q, task_id)
    wake = q.claim_due_wake(now=104)
    admission = q.admit_execution_wake(wake, provenance=None, now=104)
    assert admission.identity is not None
    q.settle_execution_wake(wake, admission, outcome="unknown", detail="original send interrupted")
    assert q.finish_wake(wake.id, wake.delivery_token, delivered=False, now=105).status == "delivering"
    reopened = TaskQueue(q.db_path)
    assert reopened.recover_inflight_wakes(now=700) == 0
    assert reconcile_recoveries(reopened, now=702) == 0
    assert reconcile_recoveries(reopened, now=703) == 1
    assert reopened.get(task_id).status == Status.QUEUED
    assert reopened.get_reservation(key).state == SpawnState.RELEASING
    with pytest.raises(TaskError):
        affirm(reopened, task_id, generation, revision, now=704)
    reopened.settle_execution_wake(wake, admission, outcome="sent", detail="late original return")
    assert reopened.finish_wake(
        wake.id, wake.delivery_token, delivered=True, now=705,
    ).status == "stale"
    assert reopened.get(task_id).status == Status.QUEUED


def test_due_nudges_are_durable_and_do_not_reset_startup_budget(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    request(q, task_id)
    wake = q.claim_due_wake(now=104)
    q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=104)
    reconcile_recoveries(q, now=105)
    assert reconcile_recoveries(q, now=403) == 0
    assert reconcile_recoveries(TaskQueue(q.db_path), now=404) == 1
    assert len(q.list_wakes(task_id)) == 2
    assert q.pool_startup(task_id).startup_attempts == 1
    assert q.pool_startup(task_id).decision_deadline == 1904
    assert q.get(task_id).status == Status.SUSPENDED
    assert reconcile_recoveries(q, now=1904) == 1
    assert q.get(task_id).status == Status.QUEUED


def test_exhaustion_preserves_later_due_time_and_taskwide_cooldown(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    with q._connect() as conn:
        conn.execute("UPDATE task_pool_assignments SET cooldown_stage=2 WHERE task_id=?", (task_id,))
        conn.execute("UPDATE tasks SET not_before=90000 WHERE id=?", (task_id,))
    request(q, task_id)
    assert reconcile_recoveries(q, now=703) == 1
    assert q.get(task_id).not_before == 90000
    assert q.pool_startup(task_id).cooldown_stage == 3


def test_old_episode_cannot_affirm_a_later_suspension(q: TaskQueue) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    affirm(q, task_id, generation, revision)
    q.suspend(task_id, "host-a/worktree", reason="new monitored wait", now=106)
    with pytest.raises(TaskError):
        affirm(q, task_id, generation, revision, now=107)
    next_revision = request(q, task_id, now=108)
    assert next_revision > revision
    with pytest.raises(TaskError):
        affirm(q, task_id, generation, revision, now=109)
    assert affirm(q, task_id, generation, next_revision, now=109).status == Status.STARTED


def test_real_http_resume_returns_suspended_until_explicit_pool_start(q: TaskQueue) -> None:
    task_id, _, generation = suspended(q)
    client = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    response = client.post(f"/tasks/{task_id}/resume", headers=headers(), json={
        "worker_id": "host-a/worktree", "wake": True,
    })
    assert response.status_code == 200 and response.json()["status"] == Status.SUSPENDED
    revision = q.pool_startup(task_id).recovery_revision
    body = {
        "worker_id": "host-a/worktree", "owner_session_id": "native",
        "expected_owner_session_id": "native", "expected_generation": generation,
        "pool_placement_version": 1, "pool_recovery_revision": revision,
    }
    accepted = client.post(f"/tasks/{task_id}/pool-start", headers=headers(), json=body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == Status.STARTED


def test_cold_recovery_uses_existing_spawn_publication_without_acceptance(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET state=? WHERE key=?", (SpawnState.COLD, key))
    request(q, task_id)
    wake = q.claim_due_wake(now=104)
    prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.SPAWNED
    assert q.get(task_id).status == Status.SUSPENDED
    with pytest.raises(TaskError):
        prepare_recovery_wake(q, replace(wake, pool_recovery_revision=99))


def test_actual_delivery_boundary_retains_unknown_without_a_producer_scope(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    request(q, task_id)
    wake = q.claim_due_wake(now=104)
    assert deliver_execution_wake(q, wake, None, lambda: False) is False
    retained = q.finish_wake(wake.id, wake.delivery_token, delivered=False, now=105)
    assert retained.execution_send_state == "unknown" and retained.status == "delivering"
    assert reconcile_recoveries(q, now=106) == 0
    assert len(q.list_wakes(task_id)) == 1


def test_interrupted_pre_send_attempt_is_not_replayed_or_free(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    request(q, task_id)
    wake = q.claim_due_wake(now=104, lease_seconds=10)
    assert q.recover_inflight_wakes(now=114) == 1
    assert q.list_wakes(task_id)[0].status == "failed"
    assert reconcile_recoveries(TaskQueue(q.db_path), now=115) == 1
    assert q.pool_startup(task_id).startup_attempts == 2
    next_wake = q.claim_due_wake(now=116)
    assert next_wake.id != wake.id


def test_sent_return_lost_before_finish_recovers_delivery_not_another_send(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    request(q, task_id)
    wake = q.claim_due_wake(now=104, lease_seconds=10)
    admission = q.admit_execution_wake(wake, provenance=None, now=104)
    q.settle_execution_wake(wake, admission, outcome="sent", detail="original send accepted")
    assert q.recover_inflight_wakes(now=114) == 1
    assert q.list_wakes(task_id)[0].status == "delivered"
    assert q.claim_due_wake(now=115) is None
    reconcile_recoveries(q, now=115)
    assert q.pool_startup(task_id).startup_attempts == 1
    assert q.get(task_id).status == Status.SUSPENDED


def test_failed_nudge_on_third_startup_attempt_keeps_decision_window(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    request(q, task_id)
    for now in (104, 106):
        wake = q.claim_due_wake(now=now)
        q.finish_wake(wake.id, wake.delivery_token, delivered=False, now=now)
        reconcile_recoveries(q, now=now + 1)
    wake = q.claim_due_wake(now=108)
    q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=108)
    reconcile_recoveries(q, now=109)
    reconcile_recoveries(q, now=408)
    nudge = q.claim_due_wake(now=409)
    q.finish_wake(nudge.id, nudge.delivery_token, delivered=False, now=409)
    assert reconcile_recoveries(q, now=410) == 0
    assert q.get(task_id).status == Status.SUSPENDED
    assert q.pool_startup(task_id).startup_attempts == 3
    assert q.pool_startup(task_id).decision_deadline == 1908
    assert reconcile_recoveries(q, now=708) == 1
    assert q.get(task_id).status == Status.SUSPENDED


@pytest.mark.parametrize("delivered", [None, 104])
def test_hold_pauses_recovery_without_charging_attempts_or_cooldown(
    q: TaskQueue, delivered: float | None,
) -> None:
    task_id, key, generation = suspended(q)
    revision = request(q, task_id)
    if delivered is not None:
        wake = q.claim_due_wake(now=delivered)
        q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=delivered)
    q.set_hold(task_id, reason="intentional operator wait", actor="operator", now=105)
    q.set_hold(task_id, reason="updated explanation", actor="operator", now=200)
    assert q.claim_due_wake(now=10000) is None
    assert reconcile_recoveries(q, now=10000) == 0
    q.clear_hold(task_id, actor="operator", now=10005)
    budget = q.pool_startup(task_id)
    assert budget.startup_attempts == 1 and budget.cooldown_stage == 0
    assert budget.startup_deadline == 10603
    assert budget.decision_deadline == (11804 if delivered is not None else None)
    assert q.get_reservation(key).state == SpawnState.SPAWNED
    assert affirm(q, task_id, generation, revision, now=10006).status == Status.STARTED


def test_worker_can_affirm_after_delivery_before_gc_observes_it(q: TaskQueue) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    wake = q.claim_due_wake(now=104)
    q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=702)
    assert q.pool_startup(task_id).decision_deadline is None
    assert affirm(q, task_id, generation, revision, now=704).status == Status.STARTED


def test_renewed_monitor_fences_old_recovery_and_never_resumes_it(q: TaskQueue) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    q.suspend(task_id, "host-a/worktree", reason="wait until tomorrow", cooldown_seconds=86400, now=104)
    assert q.claim_due_wake(now=105) is None
    assert reconcile_recoveries(q, now=10000) == 0
    assert q.get(task_id).status == Status.SUSPENDED
    assert q.get(task_id).monitor_not_before == 86504
    assert q.pool_startup(task_id).startup_attempts == 1
    with pytest.raises(TaskError):
        affirm(q, task_id, generation, revision)


def test_original_start_request_cannot_send_a_wake_after_worker_affirms(q: TaskQueue) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    affirm(q, task_id, generation, revision)
    assert q.claim_due_wake(now=106) is None
    assert q.list_wakes(task_id)[0].status == "stale"


def test_legacy_pending_resume_gets_new_fenced_episode_not_a_process_replay(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET resume_requested=1 WHERE id=?", (task_id,))
    assert reconcile_recoveries(q, now=103) == 1
    assert q.pool_startup(task_id).recovery_revision == 1
    assert q.pool_startup(task_id).startup_attempts == 1
    assert q.get(task_id).status == Status.SUSPENDED
    assert len(q.list_wakes(task_id)) == 1


def test_legacy_unfinished_startup_is_visible_and_never_replayed(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET resume_requested=1 WHERE id=?", (task_id,))
        conn.execute("UPDATE spawn_reservations SET startup_complete=0 WHERE key=?", (key,))
    assert reconcile_recoveries(q, now=103) == 0
    budget = q.pool_startup(task_id)
    assert budget.outcome_kind == "startup_unknown"
    assert budget.outcome_detail.startswith("legacy suspended recovery retained:")
    assert budget.recovery_requested_at is None and budget.startup_attempts == 0
    assert reconcile_recoveries(q, now=104) == 0
    assert q.pool_startup(task_id) == budget
    assert q.list_wakes(task_id) == []


def test_real_cli_negotiates_recovery_and_never_claims_another_task(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_dispatch import __main__ as cli
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    requests: list[httpx.Request] = []
    monkeypatch.setattr(cli, "_client", lambda *_: connected(api, requests, control=False))
    monkeypatch.setattr(cli, "_resolve_owner", lambda *_args, **_kw: "host-a/worktree")
    args = cli.build_parser().parse_args([
        "start", task_id, "--pool-generation", str(generation),
        "--pool-recovery-revision", str(revision), "--owner-session-id", "native",
    ])
    assert args.func(args) == 0
    assert '"status": "started"' in capsys.readouterr().out
    assert [r.url.path for r in requests] == ["/health", f"/tasks/{task_id}/pool-start"]


@pytest.mark.parametrize("support", [None, [], [True], [2]])
def test_current_worker_refuses_old_or_incompatible_provider_before_recovery_effect(
    q: TaskQueue, support: object,
) -> None:
    from agent_dispatch.client import DispatchUpgradeRequired
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    requests: list[httpx.Request] = []

    def old_provider(req: httpx.Request, response: httpx.Response) -> httpx.Response:
        if req.url.path == "/health":
            body = response.json()
            body["pool_placement"]["suspended_recovery_versions"] = support
            return httpx.Response(200, json=body)
        return response

    with connected(api, requests, control=False, rewrite=old_provider) as worker:
        with pytest.raises(DispatchUpgradeRequired, match="suspended pool recovery"):
            worker.start_pool_task(
                task_id, "host-a/worktree", owner_session_id="native",
                expected_generation=generation, recovery_revision=revision,
            )
    assert [r.url.path for r in requests] == ["/health"]
    assert q.get(task_id).status == Status.SUSPENDED


def test_task_scoped_native_can_affirm_only_its_current_recovery(q: TaskQueue) -> None:
    from agent_dispatch.task_authority import mint_task_token
    from agent_dispatch.task_scope import TASK_SCOPE_HEADER
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    token = mint_task_token("control", None, task_id, "host-a/worktree", "native", generation)
    body = {
        "worker_id": "untrusted-body", "owner_session_id": "untrusted-body",
        "expected_generation": generation, "pool_placement_version": 1,
        "pool_recovery_revision": revision,
    }
    response = api.post(f"/tasks/{task_id}/pool-start", headers={TASK_SCOPE_HEADER: token}, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["owner"] == "host-a/worktree"
    assert response.json()["owner_session_id"] == "native"
    foreign = task(q)
    rejected = api.post(f"/tasks/{foreign.id}/pool-start", headers={TASK_SCOPE_HEADER: token}, json=body)
    assert rejected.status_code == 403


def test_held_steering_is_durable_without_starting_or_charging_recovery(q: TaskQueue) -> None:
    task_id, _, _ = suspended(q)
    q.set_hold(task_id, reason="wait for operator", actor="operator", now=103)
    result = q.submit_steer(
        task_id, fields={"decision": "retain this answer"}, sender="operator",
        wake_requested=True, now=104,
    )
    assert result.status == Status.SUSPENDED and result.hold_reason == "wait for operator"
    assert q.steer_log(task_id)[0]["fields"] == {"decision": "retain this answer"}
    assert q.list_wakes(task_id) == []
    assert q.pool_startup(task_id).recovery_requested_at is None
    assert q.pool_startup(task_id).startup_attempts == 0


@pytest.mark.parametrize("operation", ["resume", "steer"])
def test_later_wake_request_uses_existing_episode_without_resetting_budget(
    q: TaskQueue, operation: str,
) -> None:
    task_id, _, _ = suspended(q)
    q.resume(task_id, "host-a/worktree", wake_requested=False, now=103)
    budget = q.pool_startup(task_id)
    assert q.list_wakes(task_id) == []
    if operation == "resume":
        q.resume(task_id, "host-a/worktree", wake_requested=True, now=104)
    else:
        q.submit_steer(task_id, fields={"answer": "continue"}, wake_requested=True, now=104)
    after = q.pool_startup(task_id)
    assert after.recovery_revision == budget.recovery_revision
    assert after.startup_attempts == budget.startup_attempts
    assert after.startup_deadline == budget.startup_deadline
    assert after.recovery_requested_at == budget.recovery_requested_at
    assert len(q.list_wakes(task_id)) == 1
    assert q.list_wakes(task_id)[0].pool_recovery_revision == budget.recovery_revision
    q.resume(task_id, "host-a/worktree", wake_requested=True, now=105)
    assert len(q.list_wakes(task_id)) == 1


def test_settled_delivery_starts_decision_clock_before_outbox_finish(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    wake = q.claim_due_wake(now=104, lease_seconds=600)
    admission = q.admit_execution_wake(wake, provenance=None, now=104)
    monkeypatch.setattr(q, "_now", lambda now: 702 if now is None else now)
    q.settle_execution_wake(wake, admission, outcome="sent", detail="accepted")
    assert q.list_wakes(task_id)[0].status == "delivering"
    assert reconcile_recoveries(q, now=703) == 1
    assert q.get(task_id).status == Status.SUSPENDED
    assert q.pool_startup(task_id).decision_deadline == 2502
    assert affirm(q, task_id, generation, revision, now=704).status == Status.STARTED


@pytest.mark.parametrize("stale", ["generation", "native"])
def test_stale_repeated_suspend_cannot_invalidate_current_recovery(
    q: TaskQueue, stale: str,
) -> None:
    task_id, key, generation = suspended(q)
    revision = request(q, task_id)
    before = q.get(task_id), q.pool_startup(task_id), q.get_reservation(key)
    with pytest.raises(TaskError, match="incarnation changed"):
        q.suspend(
            task_id, "host-a/worktree", reason="stale wait", now=104,
            expected_generation=generation + int(stale == "generation"),
            expected_owner_session_id="old-native" if stale == "native" else "native",
        )
    assert (q.get(task_id), q.pool_startup(task_id), q.get_reservation(key)) == before
    assert affirm(q, task_id, generation, revision).status == Status.STARTED


def test_task_only_client_discovers_scoped_contract_then_affirms_recovery(q: TaskQueue) -> None:
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.task_authority import mint_task_token
    from agent_dispatch.task_scope import TASK_SCOPE_HEADER
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    token = mint_task_token("control", None, task_id, "host-a/worktree", "native", generation)
    requests = []

    def transport(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        response = api.request(req.method, str(req.url), headers=dict(req.headers), content=req.content)
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

    with DispatchClient("http://testserver", task_token=token, transport=httpx.MockTransport(transport)) as worker:
        result = worker.start_pool_task(
            task_id, "host-a/worktree", owner_session_id="native",
            expected_generation=generation, recovery_revision=revision,
        )
    assert result["status"] == Status.STARTED
    assert [(req.method, req.url.path) for req in requests] == [
        ("GET", f"/tasks/{task_id}/pool-start/contract"), ("POST", f"/tasks/{task_id}/pool-start"),
    ]
    scoped = {TASK_SCOPE_HEADER: token}
    contract = api.get(f"/tasks/{task_id}/pool-start/contract", headers=scoped)
    assert set(contract.json()) == {"pool_placement"}
    assert api.get("/health", headers=scoped).status_code == 403
    foreign = task(q)
    assert api.get(
        f"/tasks/{foreign.id}/pool-start/contract", headers=scoped,
    ).status_code == 403


@pytest.mark.parametrize("status", [404, 405, 501])
def test_task_only_start_refuses_provider_without_scoped_discovery(status: int) -> None:
    from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
    requests = []

    def transport(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return httpx.Response(status, json={"detail": "unsupported route"})

    with DispatchClient(
        "http://testserver", task_token="opaque-grant", transport=httpx.MockTransport(transport),
    ) as worker:
        with pytest.raises(DispatchUpgradeRequired, match="task-scoped pool-start discovery"):
            worker.start_pool_task(
                "task-a", "host-a/worktree", owner_session_id="native",
                expected_generation=1, recovery_revision=1,
            )
    assert [(req.method, req.url.path) for req in requests] == [
        ("GET", "/tasks/task-a/pool-start/contract"),
    ]


@pytest.mark.parametrize("delivery", ["late-finished", "late-settled", "decision-expired"])
def test_hold_cannot_revive_a_budget_expired_before_the_hold(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, delivery: str,
) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    wake = q.claim_due_wake(now=104, lease_seconds=3000)
    delivered = 104 if delivery == "decision-expired" else 704
    if delivery == "late-settled":
        admission = q.admit_execution_wake(wake, provenance=None, now=104)
        monkeypatch.setattr(q, "_now", lambda now: delivered if now is None else now)
        q.settle_execution_wake(wake, admission, outcome="sent", detail="late delivery")
    else:
        q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=delivered)
    held_at, cleared_at = (2000, 3000) if delivery == "decision-expired" else (705, 1000)
    q.set_hold(task_id, reason="hold after expiry", actor="operator", now=held_at)
    q.clear_hold(task_id, actor="operator", now=cleared_at)
    assert q.pool_startup(task_id).startup_deadline == 703
    with pytest.raises(TaskError, match="deadline expired"):
        affirm(q, task_id, generation, revision, now=cleared_at + 1)
    assert reconcile_recoveries(q, now=cleared_at + 1) >= 1
    assert q.get(task_id).status == Status.QUEUED


def test_delivery_during_an_active_hold_uses_only_pre_delivery_hold_time(
    q: TaskQueue,
) -> None:
    task_id, _, generation = suspended(q)
    revision = request(q, task_id)
    wake = q.claim_due_wake(now=104, lease_seconds=3000)
    q.set_hold(task_id, reason="pause before deadline", actor="operator", now=105)
    q.finish_wake(wake.id, wake.delivery_token, delivered=True, now=704)
    q.clear_hold(task_id, actor="operator", now=1000)
    budget = q.pool_startup(task_id)
    assert budget.startup_deadline == 1598
    assert budget.reachable_at == 704 and budget.decision_deadline == 2800
    assert affirm(q, task_id, generation, revision, now=1001).status == Status.STARTED


def test_expired_suspended_worker_grant_requires_exact_trusted_renewal(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch.client import DispatchClient, DispatchError
    from agent_dispatch.task_authority import mint_task_token
    clock = [2000.0]
    monkeypatch.setattr("agent_dispatch.task_scope.time.time", lambda: clock[0])
    task_id, _, generation = suspended(q)
    original = mint_task_token("control", None, task_id, "host-a/worktree", "native", generation)
    clock[0] = 3001.0
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    requests = []

    def transport(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        response = api.request(req.method, str(req.url), headers=dict(req.headers), content=req.content)
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

    with connected(api, []) as trusted:
        with pytest.raises(DispatchError) as waiting:
            trusted.task_grant(
                task_id, "host-a/worktree", "native", expected_generation=generation, expected_epoch=0,
            )
        assert waiting.value.status_code == 409
        revision = request(q, task_id, now=clock[0])
        with DispatchClient(
            "http://testserver", task_token=original, transport=httpx.MockTransport(transport),
        ) as expired:
            with pytest.raises(DispatchError) as denied:
                expired.start_pool_task(
                    task_id, "host-a/worktree", owner_session_id="native",
                    expected_generation=generation, recovery_revision=revision,
                )
            assert denied.value.status_code == 401
        for native, gen, epoch in (("other", generation, 0), ("native", generation + 1, 0),
                                   ("native", generation, 1)):
            with pytest.raises(DispatchError) as stale:
                trusted.task_grant(
                    task_id, "host-a/worktree", native, expected_generation=gen, expected_epoch=epoch,
                )
            assert stale.value.status_code == 409
        grant = trusted.task_grant(
            task_id, "host-a/worktree", "native", expected_generation=generation, expected_epoch=0,
        )
    with DispatchClient(
        "http://testserver", task_token=grant["task_token"], transport=httpx.MockTransport(transport),
    ) as worker:
        with pytest.raises(DispatchError) as self_renewal:
            worker.task_grant(
                task_id, "host-a/worktree", "native", expected_generation=generation, expected_epoch=0,
            )
        assert self_renewal.value.status_code == 403
        assert worker.start_pool_task(
            task_id, "host-a/worktree", owner_session_id="native",
            expected_generation=generation, recovery_revision=revision,
        )["status"] == Status.STARTED


def _suspend_placed(q: TaskQueue, native: str, now: float = 101) -> tuple[str, str]:
    t = task(q)
    reservation, _ = place(q, t)
    record_native(q, reservation, worktree=f"wt-{native}", native=native)
    q.record_spawn(reservation.key, session_handle=native, startup_complete=True, startup_revision=1)
    started = q.start(
        t.id, f"host-a/wt-{native}", owner_session_id=native,
        expected_generation=q.get(t.id).generation, now=now,
    )
    q.suspend(t.id, started.owner, reason="intentional wait", cooldown_seconds=None, now=now + 1)
    return t.id, reservation.key


def _cold(q: TaskQueue, key: str) -> None:
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET state=? WHERE key=?", (SpawnState.COLD, key))


def test_cold_suspended_body_does_not_hold_a_pool_lane(q: TaskQueue) -> None:
    _, key, _ = suspended(q)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    _cold(q, key)
    reservation, _ = place(q, task(q))
    assert reservation.key != key


def test_reactivation_waits_for_a_lane_then_outranks_fresh_placement(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    _cold(q, key)
    running, _ = place(q, task(q))
    request(q, task_id)
    waiting = q.pool_startup(task_id)
    assert waiting.recovery_requested_at is None and q.list_wakes(task_id) == []
    assert q.get(task_id).resume_requested and q.get(task_id).status == Status.SUSPENDED
    _cold(q, running.key)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    assert reconcile_recoveries(q, now=110) == 1
    assert q.pool_startup(task_id).recovery_requested_at == 110
    assert len(q.list_wakes(task_id)) == 1
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_waiting_reactivations_take_freed_lanes_in_request_order(q: TaskQueue) -> None:
    publish(q, pool())
    first, first_key = _suspend_placed(q, "native-1")
    _cold(q, first_key)
    second, second_key = _suspend_placed(q, "native-2", now=111)
    _cold(q, second_key)
    running, _ = place(q, task(q))
    q.resume(second, "host-a/wt-native-2", wake_requested=True, now=120)
    q.resume(first, "host-a/wt-native-1", wake_requested=True, now=121)
    _cold(q, running.key)
    reconcile_recoveries(q, now=125)  # within the catalog publication's validity
    assert q.pool_startup(second).recovery_requested_at == 125
    assert q.pool_startup(first).recovery_requested_at is None
    assert q.list_wakes(first) == []


def test_detached_waiter_completion_waits_for_its_cold_owners_lane(q: TaskQueue) -> None:
    from agent_dispatch.queue_execution_custody import ExecutionSendUnresolved
    task_id, key, _ = suspended(q)
    waiter = q.register_run_waiter(
        task_id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="host-a/worktree", command=["synthetic-wait"],
    )
    _cold(q, key)
    running, _ = place(q, task(q))
    q.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    wake = q.claim_due_run_waiter_wake()
    assert getattr(wake, "pool_recovery_revision", None) is None
    with pytest.raises(ExecutionSendUnresolved, match="free lane"):
        prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.COLD
    _cold(q, running.key)
    prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.SPAWNED
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_cold_wake_still_counts_its_tasks_older_accountable_attempt(q: TaskQueue) -> None:
    from agent_dispatch.queue_execution_custody import ExecutionSendUnresolved
    publish(q, pool(capacity=2))
    task_id, key = _suspend_placed(q, "native-a")
    waiter = q.register_run_waiter(
        task_id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="host-a/wt-native-a", command=["synthetic-wait"],
    )
    _cold(q, key)
    with q._connect() as conn:
        older = dict(conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone())
        older.update(
            key="older-attempt", attempt=older["attempt"] - 1, state=SpawnState.SPAWNED,
            worktree="wt-older", session_handle="older-native",
        )
        conn.execute(
            f"INSERT INTO spawn_reservations ({', '.join(older)}) VALUES ({', '.join('?' * len(older))})",
            tuple(older.values()),
        )
        conn.execute(
            "INSERT INTO pool_reservation_bindings SELECT 'older-attempt', registration_id, machine, env,"
            " capabilities FROM pool_reservation_bindings WHERE reservation_key=?", (key,),
        )
    place(q, task(q))
    q.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    wake = q.claim_due_run_waiter_wake()
    with pytest.raises(ExecutionSendUnresolved, match="free lane"):
        prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.COLD


def test_detached_wake_retaking_a_waiting_reactivations_lane_counts_once(q: TaskQueue) -> None:
    from agent_dispatch.queue_pool_placement import pool_lane_load
    publish(q, pool(capacity=2))
    task_id, key = _suspend_placed(q, "native-a")
    waiter = q.register_run_waiter(
        task_id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="host-a/wt-native-a", command=["synthetic-wait"],
    )
    _cold(q, key)
    running, _ = place(q, task(q))
    other, _ = place(q, task(q))
    q.resume(task_id, "host-a/wt-native-a", wake_requested=True, now=103)
    assert q.pool_startup(task_id).recovery_requested_at is None  # waiting for a lane
    _cold(q, other.key)
    q.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    wake = q.claim_due_run_waiter_wake()
    prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.SPAWNED
    with q._connect() as conn:
        # Keep the waiting entry as an unresolved send would, so counting alone is checked.
        conn.execute("UPDATE task_pool_assignments SET lane_wait_since=103 WHERE task_id=?", (task_id,))
        identity = tuple(conn.execute(
            "SELECT registration_id, machine, env FROM pool_reservation_bindings WHERE reservation_key=?",
            (key,),
        ).fetchone())
        assert pool_lane_load(conn, identity) == 2
    _cold(q, running.key)
    place(q, task(q))


def test_lane_blocked_wake_does_not_starve_a_runnable_wake(q: TaskQueue) -> None:
    import asyncio

    from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
    publish(q, pool())
    blocked_id, blocked_key = _suspend_placed(q, "native-a")
    blocked_waiter = q.register_run_waiter(
        blocked_id, pid=123, host="host-a", start_token="waiter-a",
        resume_worktree="host-a/wt-native-a", command=["synthetic-wait"],
    )
    _cold(q, blocked_key)
    runnable_id, _ = _suspend_placed(q, "native-b")
    runnable_waiter = q.register_run_waiter(
        runnable_id, pid=124, host="host-a", start_token="waiter-b",
        resume_worktree="host-a/wt-native-b", command=["synthetic-wait"],
    )
    for task_id, waiter in ((blocked_id, blocked_waiter), (runnable_id, runnable_waiter)):
        q.recover_dead_run_waiter(
            task_id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
    [blocked] = q.list_run_waiter_wakes(blocked_id)
    with q._connect() as conn:
        conn.execute("UPDATE run_waiter_wakes SET created_at=50 WHERE id=?", (blocked.id,))
    delivered = []

    async def scenario() -> None:
        loop = asyncio.create_task(drain_run_waiter_wakes(
            q, interval=0.01, deliver=lambda _o, task_id, *_a: delivered.append(task_id) or True,
        ))
        try:
            for _ in range(200):
                if delivered:
                    break
                await asyncio.sleep(0.01)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert delivered == [runnable_id]
    [blocked] = q.list_run_waiter_wakes(blocked_id)
    assert blocked.status == "pending" and blocked.attempts == 0 and blocked.not_before > 100
    assert q.get_reservation(blocked_key).state == SpawnState.COLD


def test_lane_waiter_resumed_without_wake_starts_without_waking(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    _cold(q, key)
    running, _ = place(q, task(q))
    q.resume(task_id, "host-a/worktree", wake_requested=False, now=103)
    assert q.pool_startup(task_id).recovery_requested_at is None
    _cold(q, running.key)
    assert reconcile_recoveries(q, now=110) == 1
    assert q.pool_startup(task_id).recovery_requested_at == 110
    assert q.list_wakes(task_id) == []
    q.resume(task_id, "host-a/worktree", wake_requested=True, now=111)
    assert len(q.list_wakes(task_id)) == 1  # a later wake request reaches the started recovery


def _parked_waiter_behind_running(q: TaskQueue) -> tuple[str, str, str]:
    task_id, key, _ = suspended(q)
    _cold(q, key)
    running, _ = place(q, task(q))
    request(q, task_id)
    assert q.pool_startup(task_id).recovery_requested_at is None
    return task_id, key, running.key


def test_lane_waiter_that_can_no_longer_start_releases_its_lane(q: TaskQueue) -> None:
    task_id, key, running_key = _parked_waiter_behind_running(q)
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET cooling_token='cooling' WHERE key=?", (key,))
    _cold(q, running_key)
    reconcile_recoveries(q, now=110)
    assert q.pool_startup(task_id).recovery_requested_at is None
    assert q.list_wakes(task_id) == []
    place(q, task(q))


def test_lane_waiter_with_retired_conversation_holds_no_lane(q: TaskQueue) -> None:
    _, key, running_key = _parked_waiter_behind_running(q)
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET conversation_retired=1 WHERE key=?", (key,))
    _cold(q, running_key)
    place(q, task(q))


def test_dropped_lane_wait_keeps_the_callers_no_wake_choice(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    _cold(q, key)
    running, _ = place(q, task(q))
    q.resume(task_id, "host-a/worktree", wake_requested=False, now=103)
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET cooling_token='cooling' WHERE key=?", (key,))
    _cold(q, running.key)
    reconcile_recoveries(q, now=110)  # cannot start: its lane wait is dropped
    assert q.pool_startup(task_id).recovery_requested_at is None
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET cooling_token=NULL WHERE key=?", (key,))
    reconcile_recoveries(q, now=120)
    assert q.pool_startup(task_id).recovery_requested_at == 120
    assert q.list_wakes(task_id) == []


def test_lane_waiter_admitted_by_a_later_no_wake_resume_still_wakes(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    _cold(q, key)
    running, _ = place(q, task(q))
    q.resume(task_id, "host-a/worktree", wake_requested=True, now=103)
    assert q.pool_startup(task_id).recovery_requested_at is None
    _cold(q, running.key)
    q.resume(task_id, "host-a/worktree", wake_requested=False, now=110)
    assert q.pool_startup(task_id).recovery_requested_at == 110
    assert len(q.list_wakes(task_id)) == 1


def _disable_pools(q: TaskQueue) -> None:
    [scope] = q.pool_catalog_snapshot().scopes
    q.publish_pool_catalog_scope(
        scope.machine, scope.env, scope.publisher_id, generation=scope.generation,
        expected_revision=scope.revision, registration_revision=q.pool_registration_revision(),
        expected_cohort_revision=1, input_revision="disabled", definitions=(),
    )


def test_unavailable_catalog_defers_a_waiting_reactivation(q: TaskQueue) -> None:
    task_id, _, running_key = _parked_waiter_behind_running(q)
    _cold(q, running_key)
    assert reconcile_recoveries(q, now=200) == 0  # the publication expired at 130
    assert q.pool_startup(task_id).recovery_requested_at is None
    assert q.list_wakes(task_id) == []


def test_disabled_pool_defers_its_waiting_reactivation(q: TaskQueue) -> None:
    task_id, key, running_key = _parked_waiter_behind_running(q)
    _cold(q, running_key)
    _disable_pools(q)
    assert reconcile_recoveries(q, now=110) == 0
    assert q.pool_startup(task_id).recovery_requested_at is None
    assert q.list_wakes(task_id) == []
    assert q.get(task_id).resume_requested


def test_disabled_pool_defers_a_recovery_admitted_cold_wake(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    _cold(q, key)
    request(q, task_id)
    assert q.pool_startup(task_id).recovery_requested_at is not None
    _disable_pools(q)
    with pytest.raises(TaskError, match="disabled"):
        q.record_spawn(key, session_handle="native", startup_complete=True, startup_revision=1)
    assert q.get_reservation(key).state == SpawnState.COLD


def test_renewed_suspend_after_a_detached_wake_withdraws_the_resume(q: TaskQueue) -> None:
    publish(q, pool(capacity=2))
    task_id, key = _suspend_placed(q, "native-a")
    waiter = q.register_run_waiter(
        task_id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="host-a/wt-native-a", command=["synthetic-wait"],
    )
    _cold(q, key)
    running, _ = place(q, task(q))
    other, _ = place(q, task(q))
    q.resume(task_id, "host-a/wt-native-a", wake_requested=True, now=103)
    assert q.pool_startup(task_id).recovery_requested_at is None  # waiting for a lane
    _cold(q, other.key)
    q.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    wake = q.claim_due_run_waiter_wake()
    prepare_recovery_wake(q, wake)
    assert q.get(task_id).resume_requested  # the lane was re-taken, the resume intent remains
    q.suspend(task_id, q.get(task_id).owner, reason="intentional wait", cooldown_seconds=None, now=120)
    assert not q.get(task_id).resume_requested
    _cold(q, key)
    _cold(q, running.key)
    assert reconcile_recoveries(q, now=130) == 0
    assert q.list_wakes(task_id) == []


def test_stale_detached_wake_does_not_take_its_cold_owners_lane(q: TaskQueue) -> None:
    task_id, key, _ = suspended(q)
    waiter = q.register_run_waiter(
        task_id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="host-a/worktree", command=["synthetic-wait"],
    )
    _cold(q, key)
    q.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    wake = q.claim_due_run_waiter_wake()
    q.register_run_waiter(
        task_id, pid=124, host="host-a", start_token="waiter-next",
        resume_worktree="host-a/worktree", command=["synthetic-wait"],
    )
    with pytest.raises(TaskError, match="fence advanced"):
        prepare_recovery_wake(q, wake)
    assert q.get_reservation(key).state == SpawnState.COLD
    place(q, task(q))  # the capacity-one lane is still free


def test_cold_restart_keeps_a_lane_wait_recorded_after_it_commits(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import queue_pool_recovery
    publish(q, pool(capacity=2))
    task_id, key = _suspend_placed(q, "native-a")
    waiter = q.register_run_waiter(
        task_id, pid=123, host="host-a", start_token="waiter-start",
        resume_worktree="host-a/wt-native-a", command=["synthetic-wait"],
    )
    _cold(q, key)
    running, _ = place(q, task(q))
    other, _ = place(q, task(q))
    q.resume(task_id, "host-a/wt-native-a", wake_requested=True, now=103)
    _cold(q, other.key)
    q.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )
    restart = queue_pool_recovery._restart_cold

    def restart_then_resume_again(*args, **kwargs):
        restart(*args, **kwargs)
        # A renewed suspension and a new no-wake resume land before any cleanup.
        q.suspend(task_id, q.get(task_id).owner, reason="intentional wait", cooldown_seconds=None, now=110)
        _cold(q, key)
        place(q, task(q))
        q.resume(
            task_id, "host-a/wt-native-a", wake_requested=False, wake_message="new resume", now=111,
        )

    monkeypatch.setattr(queue_pool_recovery, "_restart_cold", restart_then_resume_again)
    prepare_recovery_wake(q, q.claim_due_run_waiter_wake())
    with q._connect() as conn:
        waiting = conn.execute(
            "SELECT lane_wait_since, lane_wait_message, lane_wait_wake FROM task_pool_assignments"
            " WHERE task_id=?", (task_id,),
        ).fetchone()
    assert tuple(waiting) == (111, "new resume", 0)
    assert running.key
