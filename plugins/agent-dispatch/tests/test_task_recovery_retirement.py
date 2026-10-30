"""Use supported reservation retirement rather than synthetic marker-only proofs."""

import pytest

from agent_dispatch.restricted_delivery import record
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.mark.parametrize("driver", ["cli", "restricted-bridge"])
@pytest.mark.parametrize("operation", ["detach_agent", "reset_proposed", "reset_queued"])
def test_recovery_honors_each_drivers_completed_exact_absence_release(tmp_path, driver, operation):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("work", exclusive_key="execution-key")
    reservation, _ = queue.reserve_spawn(
        task.id, reserved_by="supervisor", driver=driver,
    )
    worker = f"dispatch-{reservation.key}" if driver == "restricted-bridge" else "worker"
    claimed = queue.claim_one(worker, task_id=task.id)
    native = "native-worker"
    handle = native
    if driver == "restricted-bridge":
        info = {
            "kind": driver, "worker_id": worker,
            "task_generation": claimed.generation, "coordinator_epoch": 0,
            "configuration": {
                "name": "worker", "instance_id": "a" * 64,
                "coordinator_url": "https://coordinator.example.com",
            },
            "charter": None, "binding_id": "binding-1", "agent": "agent-1",
            "session_id": "bridge-worker", "native_session_id": native,
        }
        record(queue, reservation.key, info)
        queue.bind_owner_session(task.id, worker, native)
        record(queue, reservation.key, {**info, "send_attempted": True})
        handle = "local-body:bridge-worker"
    queue.record_spawn(
        reservation.key, session_handle=handle, startup_complete=True,
        startup_revision=reservation.startup_revision,
    )
    queue.start(task.id, worker, owner_session_id=native)
    queue.suspend(task.id, worker, reason="external")
    queue.request_spawn_release(reservation.key, disposition="settled")
    retired = queue.retire_spawn(reservation.key, exact_absence=True)
    assert retired.release_requested
    assert retired.state == "settled"
    assert not retired.conversation_retired
    assert not retired.exclusive_released
    snapshot = queue.task_diagnostics(task.id)
    assert snapshot["actions"][operation]["allowed"], snapshot["actions"][operation]
    result = queue.recover_task(
        task.id, operation=operation, expected_revision=snapshot["revision"],
        request_id="recovery-1", actor="operator", reason="qualified retirement complete",
    )
    assert result["task"]["owner"] is None
    assert result["task"]["owner_session_id"] is None
    assert result["task"]["status"] == (
        "proposed" if operation == "reset_proposed" else "queued"
    )
    [attachment] = queue.attachment_history(task.id)
    assert attachment.detached_at is not None
    assert attachment.session_id == native
