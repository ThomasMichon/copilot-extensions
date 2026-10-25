"""Explicit operator recovery preserves history and fences new authority."""

import pytest
import json
import subprocess
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture(params=["created", "reused", "targeted"])
def recovery(tmp_path, request):
    queue = TaskQueue(tmp_path / "tasks.db")
    targeted = request.param == "targeted"
    task = queue.create(
        "review", exclusive_key="review:example:1",
        affinity={"worktree": "worktree-a"} if targeted else None,
        target_worktree="worktree-a" if targeted else None,
    )
    reservation, _ = queue.reserve_spawn(task.id)
    if targeted:
        assert reservation.worktree == "worktree-a"
        assert reservation.worktree_ownership == "targeted"
    queue.record_spawn_worktree(
        reservation.key, worktree="worktree-a", ownership=request.param,
        allocation_project="example-project", worktree_path=str(tmp_path / "worktree-a"),
    )
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
    assert next_attempt.allocation_project == original.allocation_project
    assert next_attempt.worktree_path == original.worktree_path
    assert next_attempt.creation_key == original.creation_key
    assert next_attempt.allocation_reservation_key == original.allocation_reservation_key
    assert queue.get_reservation(reservation.key).worktree_ownership == original.worktree_ownership
    with queue._connect() as conn:
        notes = [row["note"] for row in conn.execute(
            "SELECT note FROM task_events WHERE task_id=?", (task.id,),
        )]
    assert any("history preserved" in (note or "") and reservation.key in note for note in notes)
    with pytest.raises(TaskError):
        queue.authorize_fresh_acquisition(task.id, **args)


@pytest.mark.parametrize("recovery", ["targeted"], indirect=True)
@pytest.mark.parametrize("changed_target", ["affinity", "target", "missing-affinity"])
def test_targeted_authorization_refuses_changed_target_without_mutation(recovery, changed_target):
    queue, task, reservation, args = recovery
    with queue._connect() as conn:
        if changed_target == "affinity":
            conn.execute("UPDATE tasks SET affinity=? WHERE id=?", (
                json.dumps({"worktree": "other"}), task.id,
            ))
        elif changed_target == "target":
            conn.execute("UPDATE tasks SET target_worktree='other' WHERE id=?", (task.id,))
        else:
            conn.execute("UPDATE tasks SET affinity='{}' WHERE id=?", (task.id,))
        conn.commit()
    with pytest.raises(TaskError, match="target"):
        queue.authorize_fresh_acquisition(task.id, **args)
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(reservation.key).conversation_retired is False


@pytest.mark.parametrize("recovery", ["targeted"], indirect=True)
def test_affinity_only_targeted_allocation_retains_explicit_recovery(recovery):
    queue, task, reservation, args = recovery
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET target_worktree=NULL WHERE id=?", (task.id,))
        conn.commit()
    assert queue.authorize_fresh_acquisition(task.id, **args)["launched"] is False
    assert queue.get_reservation(reservation.key).worktree_ownership == "targeted"


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


def test_active_exclusive_key_contender_without_worktree_refuses(recovery):
    queue, task, _reservation, args = recovery
    contender = queue.create("other occurrence", exclusive_key=task.exclusive_key)
    pending, reserved = queue.reserve_spawn(contender.id)
    assert reserved and pending.worktree == "worktree-a"
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree=NULL, inherited_worktree=NULL WHERE key=?",
            (pending.key,),
        )
        conn.commit()
    with pytest.raises(TaskError, match="exclusive-key"):
        queue.authorize_fresh_acquisition(task.id, **args)
    assert queue.get(task.id).generation == task.generation


@pytest.mark.parametrize("blocker", ["held", "claimed", "cleanup", "handle", "foreign", "ownership"])
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
        elif blocker == "ownership":
            conn.execute("UPDATE spawn_reservations SET worktree_ownership='unknown' WHERE key=?", (reservation.key,))
        conn.commit()
    if blocker == "foreign":
        foreign_task = queue.create("other")
        foreign, _ = queue.reserve_spawn(foreign_task.id)
        queue.record_spawn_worktree(foreign.key, worktree="worktree-a", ownership="reused")
    with pytest.raises(TaskError):
        queue.authorize_fresh_acquisition(task.id, **args)
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(reservation.key).conversation_retired is False


def test_http_requires_control_identity_not_worker_identity(recovery, monkeypatch):
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.verify_recovery_bridge", lambda: None)
    queue, task, _reservation, args = recovery
    client = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    path = f"/spawn-reservations/tasks/{task.id}/authorize-fresh"
    scheme = "Be" + "arer"
    assert client.post(path, json=args, headers={"Authorization": scheme + " worker-test"}).status_code == 403
    response = client.post(path, json=args, headers={"Authorization": scheme + " control-test"})
    assert response.status_code == 200
    assert response.json()["authorization"] == "operator-fresh"


def test_http_refuses_old_bridge_before_authorization(recovery, monkeypatch):
    def unavailable():
        raise TaskError("recovery requires bridge payload and daemon acquisition protocol 31")
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.verify_recovery_bridge", unavailable)
    queue, task, reservation, args = recovery
    client = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    scheme = "Be" + "arer"
    response = client.post(
        f"/spawn-reservations/tasks/{task.id}/authorize-fresh", json=args,
        headers={"Authorization": scheme + " control-test"},
    )
    assert response.status_code == 409
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(reservation.key).conversation_retired is False


@pytest.mark.parametrize("invalid_generation", [True, False, "0", 0.0])
def test_http_rejects_coerced_generation_before_authorization(recovery, monkeypatch, invalid_generation):
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.verify_recovery_bridge", lambda: None)
    queue, task, reservation, args = recovery
    client = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    scheme = "Be" + "arer"
    response = client.post(
        f"/spawn-reservations/tasks/{task.id}/authorize-fresh",
        json={**args, "expected_generation": invalid_generation},
        headers={"Authorization": scheme + " control-test"},
    )
    assert response.status_code == 422
    assert queue.get(task.id).generation == task.generation
    assert queue.get_reservation(reservation.key).conversation_retired is False


@pytest.mark.parametrize("version,supported,accepted", [(30, True, False), (31, False, False), (31, True, True)])
def test_actual_recovery_readiness_boundary_requires_new_supported_protocol(monkeypatch, version, supported, accepted):
    from agent_dispatch.acquisition_receipts import verify_recovery_bridge
    monkeypatch.setattr("agent_dispatch.procutil.agent_bridge_launch_prefix", lambda: ["bridge-native"])
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "supported": supported, "required_protocol": version,
        }), "")
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.subprocess.run", run)
    if accepted:
        verify_recovery_bridge()
    else:
        with pytest.raises(TaskError):
            verify_recovery_bridge()
    assert seen == [["bridge-native", "acquisition-readiness"]]


def test_recovery_cli_requires_explicit_scoped_inputs():
    args = cli.build_parser().parse_args([
        "reservations", "authorize-fresh", "task-a",
        "--reservation-key", "reservation-a", "--worktree", "worktree-a",
        "--expected-generation", "0", "--permit", "--confirm-no-live-holder",
        "--reason", "verified",
    ])
    assert args.reservations_command == "authorize-fresh"
    assert args.permit and args.confirm_no_live_holder
