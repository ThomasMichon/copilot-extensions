"""Use supported reservation retirement rather than synthetic marker-only proofs."""

import pytest

from agent_dispatch.restricted_delivery import record
from agent_dispatch.acquisition_receipts import bind
from agent_dispatch.queue import TaskError
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


@pytest.mark.parametrize(
    "operation", ["detach_agent", "reset_proposed", "reset_queued", "legacy_reset"],
)
def test_native_acquisition_before_spawn_publication_remains_owned(tmp_path, operation):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(
        task.id, reserved_by="supervisor", driver="cli",
    )
    queue.record_spawn_worktree(reservation.key, "workspace")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "workspace",
        "session_id": "bridge-1", "native_session_id": "native-1",
        "mode": "cli", "delivery_id": "delivery-1",
    }
    bind(queue, receipt, evidence={
        "delivery_id": "delivery-1", "stage": "launched", "state": "pending",
        "recorded_result": {key: value for key, value in receipt.items() if key != "reservation_key"},
    })
    failed = queue.fail_spawn(reservation.key, detail="failure before spawn publication")
    assert failed.agent_info is None
    assert failed.session_handle is None
    assert queue.get(task.id).owner_session_id is None
    before = queue.get(task.id)
    with pytest.raises(TaskError, match="native"):
        if operation == "legacy_reset":
            queue.reset(task.id)
        else:
            snapshot = queue.task_diagnostics(task.id)
            assert not snapshot["actions"][operation]["allowed"]
            queue.recover_task(
                task.id, operation=operation, expected_revision=snapshot["revision"],
                request_id="recovery-1", actor="operator", reason="must retain original acquisition",
            )
    assert queue.get(task.id) == before
    with queue._connect() as conn:
        assert conn.execute(
            "SELECT native_session_id FROM acquisition_receipts WHERE reservation_key=?",
            (reservation.key,),
        ).fetchone()["native_session_id"] == "native-1"
