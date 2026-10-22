"""Reservation retries must not lose an unresolved transport operation."""

from dataclasses import replace

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_custody import ExecutionSendIdentity
from agent_dispatch.queue_records import TaskError
from tests._helpers import TEST_REPO
from tests.test_execution_send_custody import close, enroll


@pytest.fixture
def retained_send(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    producer = queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-a", expected_generation=0
    )
    task = queue.create(
        "task",
        repo=TEST_REPO,
        source="scheduled",
        exclusive_key="execution-lane",
        dedup_key="logical-task",
        producer_scope={"repo": TEST_REPO, "source": "scheduled"},
        producer_id="producer-a",
        producer_generation=1,
        producer_capability=producer.producer_capability,
        producer_request_id="task",
    )
    reservation, won = queue.reserve_spawn(task.id)
    assert won
    scope, runtime_id = enroll(queue)
    identity = ExecutionSendIdentity(
        scope, "send-a", runtime_id, reservation.key, task.generation, "launch"
    )
    return queue, task, identity


def _retain(queue, identity, state):
    queue.admit_execution_send(identity, source="scheduled", expected_producer_generation=1)
    if state != "pending":
        queue.settle_execution_send(identity, outcome=state, detail="transport observation")


@pytest.mark.parametrize("state", ["pending", "unknown"])
@pytest.mark.parametrize("kind", ["launch", "resume", "deliver", "redrive"])
@pytest.mark.parametrize("reservation_state", ["failed", "settled"])
def test_terminal_reservation_cannot_bypass_unresolved_send(
    retained_send, state, kind, reservation_state
):
    queue, task, identity = retained_send
    identity = replace(identity, kind=kind)
    _retain(queue, identity, state)
    terminalize = queue.fail_spawn if reservation_state == "failed" else queue.settle_spawn
    terminalize(identity.reservation_key, detail="transport response unavailable")
    reopened = TaskQueue(queue.db_path)
    with pytest.raises(TaskError, match="unresolved execution send"):
        reopened.reserve_spawn(task.id)
    assert len(reopened.list_reservations(task_id=task.id)) == 1
    assert reopened.get_reservation(identity.reservation_key).state == reservation_state
    close(reopened, identity)
    assert reopened.execution_closure_inventory(identity.scope)["sends"][0]["state"] == state


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_active_reservation_replay_stays_observable_without_new_permission(retained_send, state):
    queue, task, identity = retained_send
    _retain(queue, identity, state)
    reservation, won = queue.reserve_spawn(task.id)
    assert reservation.key == identity.reservation_key and won is False


@pytest.mark.parametrize("state", ["sent", "not_sent"])
def test_reconciled_transport_preserves_existing_reservation_retry_policy(retained_send, state):
    queue, task, identity = retained_send
    _retain(queue, identity, state)
    queue.fail_spawn(identity.reservation_key, detail="existing reservation recovery")
    reservation, won = queue.reserve_spawn(task.id)
    assert won and reservation.attempt == 2
    close(queue, identity)
    assert len(queue.execution_closure_inventory(identity.scope)["sends"]) == 1


def test_settlement_unlocks_retry_without_erasing_history(retained_send):
    queue, task, identity = retained_send
    _retain(queue, identity, "unknown")
    queue.fail_spawn(identity.reservation_key, detail="transport response unavailable")
    with pytest.raises(TaskError, match="unresolved execution send"):
        queue.reserve_spawn(task.id)
    queue.settle_execution_send(
        identity, outcome="not_sent", detail="authoritative transport absence"
    )
    reservation, won = queue.reserve_spawn(task.id)
    assert won and reservation.attempt == 2
    assert queue.get(task.id).generation == task.generation


def test_shared_exclusive_key_cannot_hide_an_older_tasks_unknown_send(retained_send):
    queue, task, identity = retained_send
    _retain(queue, identity, "unknown")
    queue.fail_spawn(identity.reservation_key, detail="transport response unavailable")
    other = queue.create(
        "other task", repo=TEST_REPO, source="other", exclusive_key=task.exclusive_key
    )
    with pytest.raises(TaskError, match="unresolved execution send"):
        queue.reserve_spawn(other.id)
    unrelated = queue.create("unrelated", repo=TEST_REPO, source="other")
    assert queue.reserve_spawn(unrelated.id)[1]


def test_new_task_in_logical_workspace_cannot_hide_unknown_send(retained_send):
    queue, task, identity = retained_send
    _retain(queue, identity, "unknown")
    queue.fail_spawn(identity.reservation_key, detail="transport response unavailable")
    queue.abandon(task.id, permitted=True, reason="logical task finished")
    replacement = queue.create(
        "replacement", repo=TEST_REPO, source="other", dedup_key=task.dedup_key
    )
    assert replacement.id != task.id and replacement.exclusive_key is None
    with pytest.raises(TaskError, match="unresolved execution send"):
        queue.reserve_spawn(replacement.id)
    assert queue.list_reservations(task_id=replacement.id) == []
    queue.settle_execution_send(
        identity, outcome="not_sent", detail="authoritative transport absence"
    )
    reservation, won = queue.reserve_spawn(replacement.id)
    assert won
    assert reservation.creation_key == queue.get_reservation(identity.reservation_key).creation_key


def test_rearm_cannot_release_unknown_send_history(retained_send):
    queue, task, identity = retained_send
    for _ in range(2):
        reservation = queue.latest_reservation(task.id)
        queue.fail_spawn(reservation.key, detail="known earlier launch failure")
        assert queue.reserve_spawn(task.id)[1]
    identity = replace(identity, reservation_key=queue.latest_reservation(task.id).key)
    _retain(queue, identity, "unknown")
    queue.fail_spawn(identity.reservation_key, detail="transport response unavailable")
    before = queue.list_reservations(task_id=task.id)
    with pytest.raises(TaskError, match="unresolved execution send"):
        queue.rearm_spawn(task.id, permitted=True, reason="retry requested")
    assert queue.list_reservations(task_id=task.id) == before


def test_fresh_conversation_permission_is_not_transport_settlement(retained_send):
    queue, task, identity = retained_send
    queue.record_spawn_worktree(identity.reservation_key, "workspace-a", ownership="created")
    _retain(queue, identity, "unknown")
    queue.fail_spawn(identity.reservation_key, detail="transport response unavailable")
    with pytest.raises(TaskError, match="unresolved execution send"):
        queue.authorize_fresh_acquisition(
            task.id,
            reservation_key=identity.reservation_key,
            worktree="workspace-a",
            expected_generation=task.generation,
            permitted=True,
            confirmed_no_live_holder=True,
            reason="independent holder absence",
        )
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(identity.reservation_key).conversation_retired is False
