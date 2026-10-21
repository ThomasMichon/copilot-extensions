"""Queue closure interleavings without processes or external effects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent_dispatch.execution_effect_adapter import EffectRevocation, ExecutionScope
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_handoff import ExecutionAdmissionClosed
from agent_dispatch.queue_records import TaskError
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

    def create(name="task"):
        return queue.create(
            name, repo=TEST_REPO, source="scheduled",
            producer_scope={"repo": TEST_REPO, "source": "scheduled"},
            producer_id="producer-a", producer_generation=1,
            producer_capability=producer.producer_capability,
            producer_request_id=name,
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
    assert inventory["state"] == "closing"
    assert inventory["acknowledged"] is False
    assert len(inventory["blockers"]) == 3
