"""A refused delivery retains its startup without aborting other candidates."""

from __future__ import annotations

from agent_dispatch import bridge
from agent_dispatch.ordinary_agent_storage import record_created_agent
from tests.test_native_owner_wake import _capture, _native_supervisor
from tests.test_native_owner_wake import hosted as hosted


def test_native_delivery_identity_error_does_not_abort_later_reservations(hosted, monkeypatch, tmp_path):
    queue, task_id = hosted
    queue.resume(task_id, "host-a/worktree-a", reuse_session=True)
    queue.yield_task(task_id, "host-a/worktree-a", release_spawn=False)
    second = queue.create("another review", repo="queue-project", source="wake-test")
    reservation, _ = queue.reserve_spawn(
        second.id, reserved_by="supervisor", driver="ordinary-bridge",
    )
    queue.record_spawn_allocation(reservation.key, "execution-project")
    record_created_agent(queue, reservation.key, task_generation=second.generation, agent_info={
        "session_id": "bridge-b", "ready": True, "status": "awaiting_first_prompt",
        "connection": {
            "native_session_id": "native-b", "worktree_id": "worktree-b",
            "repo": "execution-project", "target_dir": str(tmp_path / "worktree-b"),
        },
    })
    queue.record_spawn(
        reservation.key, session_handle="local-body:bridge-b",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.claim_one("host-a/worktree-b", task_id=second.id)
    queue.start(second.id, "host-a/worktree-b", owner_session_id="native-b")
    queue.yield_task(second.id, "host-a/worktree-b", release_spawn=False)
    supervisor = _native_supervisor(queue)
    ordered = supervisor._pool_reservations(state="spawned")
    assert len(ordered) == 2
    machines = iter([None, "host-a"])
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: next(machines))
    calls = _capture(monkeypatch)

    assert supervisor.redrive_unclaimed_spawns() == 1

    assert len(calls) == 1
    failed, accepted = (queue.get_reservation(row["key"]) for row in ordered)
    assert failed.startup_complete is False
    assert failed.agent_info["retained_redrive"]["startup_revision"] == failed.startup_revision
    assert accepted.startup_complete is True
    assert calls[0][0][5] == accepted.agent_info["session_id"]
