"""A refused delivery retains its startup without aborting other candidates."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_dispatch import remote_dispatch
from agent_dispatch.ordinary_agent_storage import record_created_agent
from agent_dispatch.queue_common import Task
from tests._helpers import required
from tests._legacy_native_wake_history import seed_retained_native_yield
from tests.test_native_owner_wake import NATIVE, OWNER, Hosted, _capture, _native_supervisor
from tests.test_native_owner_wake import hosted as hosted


def test_native_delivery_identity_error_does_not_abort_later_reservations(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, "host-a/worktree-a", reuse_session=True)
    seed_retained_native_yield(queue, task_id, OWNER, NATIVE)
    second = queue.create("another review", repo="queue-project", source="wake-test")
    assert isinstance(second, Task)
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
    seed_retained_native_yield(queue, second.id, "host-a/worktree-b", "native-b")
    supervisor = _native_supervisor(queue)
    ordered = supervisor._pool_reservations(state="spawned")
    assert len(ordered) == 2
    machines = iter([None, "host-a"])
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: next(machines))
    calls = _capture(monkeypatch)

    assert supervisor.redrive_unclaimed_spawns() == 1

    assert len(calls) == 1
    failed, accepted = (required(queue.get_reservation(row["key"])) for row in ordered)
    assert failed.startup_complete is False
    redrive = required(failed.agent_info)["retained_redrive"]
    assert redrive["startup_revision"] == failed.startup_revision
    assert accepted.startup_complete is True
    assert calls[0][0][5] == required(accepted.agent_info)["session_id"]
