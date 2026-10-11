"""Explicit operator recovery preserves history and fences new authority."""

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def recovery(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", exclusive_key="review:example:1")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, worktree="worktree-a", ownership="created")
    queue.defer_spawn(reservation.key, detail="historical owner capability unavailable")
    args = {
        "reservation_key": reservation.key, "worktree": "worktree-a",
        "expected_generation": task.generation, "permitted": True,
        "confirmed_no_live_holder": True, "reason": "operator verified no active holder",
    }
    return queue, task, reservation, args


def test_authorization_advances_generation_and_preserves_attempt(recovery):
    queue, task, reservation, args = recovery
    result = queue.authorize_fresh_acquisition(task.id, **args)
    assert result["generation"] == task.generation + 1
    assert result["launched"] is False
    original = queue.get_reservation(reservation.key)
    assert original.state == "deferred"
    assert original.detail == "historical owner capability unavailable"
    assert original.conversation_retired is True
    next_attempt, reserved = queue.reserve_spawn(task.id)
    assert reserved and next_attempt.worktree == "worktree-a"
    assert next_attempt.worktree_ownership == "reused"
    assert next_attempt.conversation_retired is True
    assert next_attempt.session_handle is None
    with queue._connect() as conn:
        notes = [row["note"] for row in conn.execute(
            "SELECT note FROM task_events WHERE task_id=?", (task.id,),
        )]
    assert any("history preserved" in (note or "") and reservation.key in note for note in notes)
    with pytest.raises(TaskError):
        queue.authorize_fresh_acquisition(task.id, **args)


@pytest.mark.parametrize("change", [
    {"permitted": False}, {"confirmed_no_live_holder": False}, {"reason": ""},
    {"expected_generation": 99}, {"reservation_key": "stale"}, {"worktree": "other"},
])
def test_permission_and_exact_fences_fail_without_mutation(recovery, change):
    queue, task, reservation, args = recovery
    with pytest.raises(TaskError):
        queue.authorize_fresh_acquisition(task.id, **{**args, **change})
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(reservation.key).conversation_retired is False


def test_new_active_reservation_refuses_stale_authorization(recovery):
    queue, task, _reservation, args = recovery
    queue.reserve_spawn(task.id)
    with pytest.raises(TaskError):
        queue.authorize_fresh_acquisition(task.id, **args)


@pytest.mark.parametrize("blocker", ["held", "claimed", "cleanup", "handle", "foreign"])
def test_authorization_refuses_owner_cleanup_and_foreign_reservations(recovery, blocker):
    queue, task, reservation, args = recovery
    with queue._connect() as conn:
        if blocker == "held":
            conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (task.id,))
        elif blocker == "claimed":
            conn.execute("UPDATE tasks SET owner='machine/worktree', status='claimed' WHERE id=?", (task.id,))
        elif blocker == "cleanup":
            conn.execute("UPDATE spawn_reservations SET conclusion_state='pending' WHERE key=?", (reservation.key,))
        elif blocker == "handle":
            conn.execute("UPDATE spawn_reservations SET session_handle='native-old' WHERE key=?", (reservation.key,))
        conn.commit()
    if blocker == "foreign":
        foreign_task = queue.create("other")
        foreign, _ = queue.reserve_spawn(foreign_task.id)
        queue.record_spawn_worktree(foreign.key, worktree="worktree-a", ownership="reused")
    with pytest.raises(TaskError):
        queue.authorize_fresh_acquisition(task.id, **args)
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(reservation.key).conversation_retired is False


def test_http_requires_control_identity_not_worker_identity(recovery):
    queue, task, _reservation, args = recovery
    client = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    path = f"/spawn-reservations/tasks/{task.id}/authorize-fresh"
    scheme = "Be" + "arer"
    assert client.post(path, json=args, headers={"Authorization": scheme + " worker-test"}).status_code == 403
    response = client.post(path, json=args, headers={"Authorization": scheme + " control-test"})
    assert response.status_code == 200
    assert response.json()["authorization"] == "operator-fresh"


def test_recovery_cli_requires_explicit_scoped_inputs():
    args = cli.build_parser().parse_args([
        "reservations", "authorize-fresh", "task-a",
        "--reservation-key", "reservation-a", "--worktree", "worktree-a",
        "--expected-generation", "0", "--permit", "--confirm-no-live-holder",
        "--reason", "verified",
    ])
    assert args.reservations_command == "authorize-fresh"
    assert args.permit and args.confirm_no_live_holder
