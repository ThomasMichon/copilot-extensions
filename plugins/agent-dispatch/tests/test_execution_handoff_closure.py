"""Queue closure interleavings without processes or external effects."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent_dispatch.execution_effect_adapter import EffectRevocation, ExecutionScope
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_handoff import ExecutionAdmissionClosed
from agent_dispatch.queue_records import TaskError
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from tests._helpers import OTHER_REPO, TEST_REPO


@pytest.fixture
def setup(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    producer = queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-a", expected_generation=0
    )
    with queue._connect() as conn:
        identity = conn.execute(
            "SELECT identity FROM workspace_queue_identity WHERE singleton = 1"
        ).fetchone()["identity"]
    request = EffectRevocation(
        ExecutionScope("installation-a", identity, TEST_REPO, "registration-a"),
        "operation-a", 1, "automatic", "manual",
    )

    def create(name="task", *, claim_as=None, require_verification=False):
        return queue.create(
            name, repo=TEST_REPO, source="scheduled",
            producer_scope={"repo": TEST_REPO, "source": "scheduled"},
            producer_id="producer-a", producer_generation=1,
            producer_capability=producer.producer_capability,
            producer_request_id=name,
            claim_as=claim_as,
            require_verification=require_verification,
        )

    return queue, request, create


def close(queue, request):
    return queue.close_execution_admission(
        request, source="scheduled", expected_producer_generation=1, now=10
    )


def test_execution_handoff_closure_replays_after_restart(setup):
    queue, request, create = setup
    task = create()
    assert close(queue, request).replayed is False
    restarted = TaskQueue(queue.db_path)
    replay = close(restarted, request)
    assert replay.replayed is True
    assert replay.created_at == 10
    assert replay.request == request
    assert restarted.get(task.id) == queue.get(task.id)
    with pytest.raises(ExecutionAdmissionClosed):
        restarted.claim_one("worker", task_id=task.id)


def test_execution_handoff_closure_allows_cleanup_only_waiter_release(setup):
    queue, request, create = setup
    completed = create("cleanup")
    waiting = create("still-waiting")
    for task in (completed, waiting):
        queue.claim_one("worker", task_id=task.id)
        queue.start(task.id, "worker", owner_session_id=f"native-{task.id}")
        queue.suspend(task.id, "worker", reason="waiting")
        waiter = queue.register_run_waiter(
            task.id, pid=101, host="test-host", start_token="token",
            resume_worktree="machine/worktree", command=["sleep", "1"], now=1,
        )
        if task.id == waiting.id:
            queue.recover_dead_run_waiter(
                task.id, generation=waiter["generation"], reason="interrupted",
                message="resume owner", sender="recovery", now=2,
            )
    close(queue, request)
    queue.complete(completed.id, "worker", result_ref="done")
    releases = []
    deliveries = []

    async def scenario():
        drainer = asyncio.create_task(drain_run_waiter_wakes(
            queue, interval=0.01,
            deliver=lambda *args: deliveries.append(args) or True,
            release_claim=lambda *args: releases.append(args) or {"released": True},
        ))
        try:
            for _ in range(200):
                if queue.list_run_waiter_wakes(completed.id)[0].status == "delivered":
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("closed-scope cleanup-only claim release did not drain")
        finally:
            drainer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drainer

    asyncio.run(scenario())
    assert deliveries == []
    assert releases == [(f"{completed.id}:1", "test-host", "worktree", TEST_REPO)]
    assert queue.get(completed.id).status == "completed"
    assert queue.get(waiting.id).status == "suspended"
    assert queue.list_run_waiter_wakes(waiting.id)[0].status == "pending"
    assert queue.claim_due_run_waiter_wake() is None
    assert queue.execution_closure_inventory(request.scope)["acknowledged"] is False


@pytest.mark.parametrize("field,value", [
    ("operation_id", "operation-b"),
    ("generation", 2),
    ("retiring_mode", "manual"),
])
def test_execution_handoff_closure_rejects_conflicting_operation(setup, field, value):
    queue, request, _ = setup
    close(queue, request)
    updates = {field: value}
    if field == "retiring_mode":
        updates["destination_mode"] = "automatic"
    with pytest.raises(TaskError):
        close(queue, replace(request, **updates))
    assert queue.execution_closure_inventory(request.scope)["request"]["operation_id"] == "operation-a"


@pytest.mark.parametrize("field", ["installation", "producer_registration", "coordinator"])
def test_execution_handoff_closure_cannot_replace_qualified_scope(setup, field):
    queue, request, _ = setup
    close(queue, request)
    foreign = replace(request, scope=replace(request.scope, **{field: "other"}))
    with pytest.raises(TaskError):
        close(queue, foreign)
    with pytest.raises(TaskError, match="no execution closure"):
        queue.execution_closure_inventory(foreign.scope)


@pytest.mark.parametrize("generation", [0, True, 2])
def test_execution_handoff_closure_requires_exact_managed_producer(setup, generation):
    queue, request, _ = setup
    with pytest.raises(TaskError):
        queue.close_execution_admission(
            request, source="scheduled", expected_producer_generation=generation
        )
    with pytest.raises(TaskError, match="no execution closure"):
        queue.execution_closure_inventory(request.scope)


def test_execution_handoff_closure_blocks_new_claims_and_reservations(setup):
    queue, request, create = setup
    before = create("before")
    close(queue, request)
    after = create("after")
    assert queue.claim_one("worker", repo=TEST_REPO) is None
    for task in (before, after):
        with pytest.raises(ExecutionAdmissionClosed):
            queue.reserve_spawn(task.id)
        assert queue.get(task.id).generation == 0
    other = queue.create("unrelated", repo=TEST_REPO, source="unrelated")
    assert queue.claim_one("other-worker", task_id=other.id).id == other.id


def test_execution_handoff_closure_preserves_repo_filter_precedence(setup):
    queue, request, create = setup
    task = create()
    close(queue, request)
    assert queue.claim_one("worker", task_id=task.id, repo=OTHER_REPO) is None


def test_execution_handoff_closure_accounts_for_previously_accepted_claim(setup):
    queue, request, create = setup
    task = create()
    claimed = queue.claim_one("worker", task_id=task.id)
    close(queue, request)
    with pytest.raises(ExecutionAdmissionClosed):
        queue.start(task.id, "worker")
    inventory = queue.execution_closure_inventory(request.scope)
    assert inventory["tasks"][0]["owner"] == claimed.owner
    assert inventory["tasks"][0]["generation"] == claimed.generation
    assert inventory["acknowledged"] is False
    assert inventory["blockers"]


@pytest.mark.parametrize("transition", ["start", "resume"])
@pytest.mark.parametrize("fenced", [False, True])
def test_execution_handoff_closure_preserves_started_replay(setup, transition, fenced):
    queue, request, create = setup
    task = create()
    queue.claim_one("worker", task_id=task.id)
    if transition == "resume":
        queue.start(task.id, "worker", owner_session_id="native-session")
        queue.suspend(task.id, "worker", reason="waiting")
    before = queue.get(task.id)
    fence = ({
        "expected_generation": before.generation,
        "expected_owner_session_id": before.owner_session_id,
    } if fenced else {})
    method = getattr(queue, transition)
    accepted = method(task.id, "worker", **fence)
    close(queue, request)
    assert method(task.id, "worker", **fence) == accepted
    with pytest.raises(TaskError, match="cannot .* a 'started' task"):
        method(task.id, "other-worker", **fence)
    for stale in (
        {"expected_generation": accepted.generation + 1,
         "expected_owner_session_id": accepted.owner_session_id},
        {"expected_generation": accepted.generation,
         "expected_owner_session_id": "other-session"},
    ):
        with pytest.raises(TaskError, match="cannot .* a 'started' task"):
            method(task.id, "worker", **stale)
    if transition == "resume":
        with pytest.raises(TaskError, match="cannot resume a 'started' task"):
            method(task.id, "worker", adopt_owner_session_id="successor-session")
    assert queue.get(task.id) == accepted
    assert queue.list_wakes(task.id) == []
    assert queue.execution_closure_inventory(request.scope)["acknowledged"] is False


def test_execution_handoff_closure_keeps_reserved_spawn_visible(setup):
    queue, request, create = setup
    task = create()
    reservation, acquired = queue.reserve_spawn(task.id)
    assert acquired
    close(queue, request)
    inventory = queue.execution_closure_inventory(request.scope)
    assert inventory["reservations"][0]["key"] == reservation.key
    assert inventory["acknowledged"] is False
    assert inventory["inventory_complete"] is True


def test_execution_handoff_closure_keeps_terminal_task_allocation_history(setup):
    queue, request, create = setup
    task = create()
    reservation, _ = queue.reserve_spawn(task.id)
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    queue.complete(task.id, "worker")
    close(queue, request)
    inventory = queue.execution_closure_inventory(request.scope)
    assert inventory["tasks"][0]["status"] == "completed"
    assert inventory["reservations"][0]["key"] == reservation.key
    assert inventory["acknowledged"] is False


def test_execution_handoff_closure_cannot_rebind_scope_to_another_source(setup):
    queue, request, _ = setup
    close(queue, request)
    queue.handoff_producer_scope(
        TEST_REPO, "other", producer_id="producer-b", expected_generation=0
    )
    with pytest.raises(TaskError, match="conflicts with the retained operation"):
        queue.close_execution_admission(
            request, source="other", expected_producer_generation=1
        )
    assert queue.execution_closure_inventory(request.scope)["source"] == "scheduled"


@pytest.mark.parametrize("claim_before_close", [False, True])
def test_execution_handoff_closure_retains_pending_and_delivering_wakes(
    setup, claim_before_close
):
    queue, request, create = setup
    task = create()
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    queue.suspend(task.id, "worker", reason="waiting")
    queue.resume(task.id, "worker", wake_requested=True, now=100)
    wake = queue.list_wakes(task.id)[0]
    if claim_before_close:
        assert queue.claim_due_wake(now=100).id == wake.id
    close(queue, request)
    assert queue.claim_due_wake(now=101) is None
    inventory = queue.execution_closure_inventory(request.scope)
    assert inventory["wakes"][0]["id"] == wake.id
    assert inventory["wakes"][0]["status"] == (
        "delivering" if claim_before_close else "pending"
    )
    assert inventory["acknowledged"] is False


def test_execution_handoff_closure_blocks_resume_without_discarding_history(setup):
    queue, request, create = setup
    task = create()
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    queue.suspend(task.id, "worker", reason="waiting")
    before = queue.get(task.id)
    close(queue, request)
    with pytest.raises(ExecutionAdmissionClosed):
        queue.resume(task.id, "worker", wake_requested=True)
    assert queue.get(task.id) == before
    assert queue.list_wakes(task.id) == []


def test_execution_handoff_closure_inventory_is_not_limited_to_first_page(setup):
    queue, request, create = setup
    tasks = [create(f"task-{index}") for index in range(205)]
    close(queue, request)
    inventory = queue.execution_closure_inventory(request.scope)
    assert {row["id"] for row in inventory["tasks"]} == {task.id for task in tasks}
    assert inventory["acknowledged"] is False


def test_execution_handoff_closure_racing_claim_has_one_serial_order(setup):
    queue, request, create = setup
    task = create()

    def claim():
        try:
            return queue.claim_one("worker", task_id=task.id)
        except ExecutionAdmissionClosed:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_future = pool.submit(claim)
        closure_future = pool.submit(close, queue, request)
        claimed = claimed_future.result()
        closure_future.result()
    inventory = queue.execution_closure_inventory(request.scope)
    assert len(inventory["tasks"]) == 1
    assert inventory["tasks"][0]["owner"] == (claimed.owner if claimed else None)
    with pytest.raises(ExecutionAdmissionClosed):
        queue.reserve_spawn(task.id)


def test_execution_handoff_closure_empty_inventory_is_not_acknowledgement(setup):
    queue, request, _ = setup
    close(queue, request)
    inventory = queue.execution_closure_inventory(request.scope)
    assert inventory["tasks"] == inventory["reservations"] == inventory["wakes"] == []
    assert inventory["run_waiter_wakes"] == []
    assert inventory["state"] == "closing"
    assert inventory["acknowledged"] is False
    assert len(inventory["blockers"]) == 3


def test_execution_handoff_closure_blocks_create_claim_but_preserves_replay(setup):
    queue, request, create = setup
    accepted = create("accepted", claim_as="worker")
    close(queue, request)
    with pytest.raises(ExecutionAdmissionClosed):
        create("new", claim_as="worker")
    assert create("accepted", claim_as="worker") == accepted
    queued = create("new")
    assert queued.status == "queued" and queued.generation == 0
    other = queue.create(
        "unrelated", repo=TEST_REPO, source="unrelated", claim_as="other"
    )
    assert other.status == "claimed" and other.generation == 1


@pytest.mark.parametrize("wake_requested", [False, True])
@pytest.mark.parametrize("headless", [False, True])
def test_execution_handoff_closure_blocks_steering_resume(
    setup, wake_requested, headless
):
    queue, request, create = setup
    task = create()
    if headless:
        reservation, _ = queue.reserve_spawn(task.id)
        queue.record_spawn(reservation.key, session_handle="local-body:test-session")
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    queue.suspend(task.id, "worker", reason="waiting")
    before = queue.get(task.id)
    close(queue, request)
    with pytest.raises(ExecutionAdmissionClosed):
        queue.submit_steer(
            task.id, fields={"answer": "continue"}, wake_requested=wake_requested,
            idempotency_key="new-answer",
        )
    assert queue.get(task.id) == before
    assert queue.steer_receipt(task.id, "new-answer") is None
    assert queue.list_wakes(task.id) == []
    other = queue.create("unrelated", repo=TEST_REPO, source="unrelated")
    queue.claim_one("other-worker", task_id=other.id)
    queue.start(other.id, "other-worker", owner_session_id="other-session")
    queue.suspend(other.id, "other-worker", reason="waiting")
    assert queue.submit_steer(
        other.id, fields={"answer": "continue"}, wake_requested=wake_requested
    ).status == "started"


def test_execution_handoff_closure_preserves_exact_steering_replay(setup):
    queue, request, create = setup
    task = create()
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    queue.suspend(task.id, "worker", reason="waiting")
    answer = {"fields": {"answer": "continue"}, "wake_requested": True,
              "idempotency_key": "accepted-answer"}
    accepted = queue.submit_steer(task.id, **answer)
    receipt = queue.steer_receipt(task.id, "accepted-answer")
    close(queue, request)
    assert queue.submit_steer(task.id, **answer) == accepted
    assert queue.steer_receipt(task.id, "accepted-answer") == receipt
    with pytest.raises(ExecutionAdmissionClosed):
        queue.submit_steer(task.id, **{**answer, "idempotency_key": "new-answer"})


def _detached_wake(queue, task):
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    queue.suspend(task.id, "worker", reason="waiting")
    waiter = queue.register_run_waiter(
        task.id, pid=101, host="test-host", start_token="test-token",
        resume_worktree="test/worker", command=["example-command"], now=100,
    )
    queue.recover_dead_run_waiter(
        task.id, generation=waiter["generation"], reason="waiter exited",
        message="continue", sender="test-recovery", now=101,
    )
    return queue.list_run_waiter_wakes(task.id)[0]


@pytest.mark.parametrize("claim_before_close", [False, True])
def test_execution_handoff_closure_inventories_detached_wake_leases(
    setup, claim_before_close
):
    queue, request, create = setup
    task = create()
    wake = _detached_wake(queue, task)
    if claim_before_close:
        wake = queue.claim_due_run_waiter_wake(now=102)
    close(queue, request)
    assert queue.claim_due_run_waiter_wake(now=103) is None
    inventory = queue.execution_closure_inventory(request.scope)
    [record] = inventory["run_waiter_wakes"]
    assert record["id"] == wake.id
    assert record["status"] == ("delivering" if claim_before_close else "pending")
    assert record["delivery_token"] == wake.delivery_token
    assert record["delivery_expires_at"] == wake.delivery_expires_at
    assert record["owner_session_id"] == "native-session"
    assert inventory["acknowledged"] is False
    other = queue.create("unrelated", repo=TEST_REPO, source="unrelated")
    other_wake = _detached_wake(queue, other)
    assert queue.claim_due_run_waiter_wake(now=104).id == other_wake.id
    assert queue.claim_due_run_waiter_wake(now=105) is None


@pytest.mark.parametrize("accepted_before_close", [False, True])
def test_execution_handoff_closure_fences_submission_rejection(
    setup, accepted_before_close
):
    queue, request, create = setup
    task = create(require_verification=True)
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="native-session")
    submitted = queue.complete(task.id, "worker", result={"goal": "unfinished"})
    rejection = {
        "reason": "goal remains open",
        "expected_generation": submitted.generation,
        "expected_owner_session_id": submitted.owner_session_id,
        "expected_completed_by": submitted.completed_by,
        "expected_updated_at": submitted.updated_at,
    }
    if accepted_before_close:
        queue.reject_submission(task.id, **rejection)
    close(queue, request)
    before = (
        queue.get(task.id), queue.steer_log(task.id),
        queue.list_wakes(task.id), queue.events(task.id),
    )
    if accepted_before_close:
        replay = queue.reject_submission(task.id, **rejection)
        assert replay.task == before[0] and replay.event_type is None
    else:
        with pytest.raises(ExecutionAdmissionClosed):
            queue.reject_submission(task.id, **rejection)
    assert (
        queue.get(task.id), queue.steer_log(task.id),
        queue.list_wakes(task.id), queue.events(task.id),
    ) == before
