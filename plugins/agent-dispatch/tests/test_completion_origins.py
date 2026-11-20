"""Exercise queue-owned preparation and terminal detachment, not a copied selector policy."""

import json
from pathlib import Path

import pytest
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.completion_origins import (
    PreparationOrigin,
    exact_detached_completion,
    preparation_origins,
)
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import TaskError
from tests.test_assignment_relationships import GroundOwner
from tests.test_pool_placement import current_task, place, pool, publish, record_native, task


def test_failed_proposal_keeps_its_exact_acquisition_after_same_worker_reclaims(
    tmp_path: Path,
) -> None:
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.db", relationship_writer=owner, relationship_authority="local"
    )
    owner.queue = queue
    t = task(queue)
    space = AssignmentWorkspace("host-a", "example", "tree", str(tmp_path / "tree"), "first")
    assert isinstance(
        queue.claim_one(space.worker_id, task_id=t.id, assignment_workspace=space), Task
    )
    queue.start(t.id, space.worker_id, owner_session_id="first-native")
    with pytest.raises(TaskError, match="readiness"):
        queue.complete(t.id, space.worker_id, result={"prior": True})
    old = queue.submission_preparation(t.id)
    assert old is not None and old["blocker"] and old.get("advancement") is None
    old_event = old["request"]["assignment_event_id"]
    queue.yield_task(t.id, space.worker_id)
    assert queue.reconcile_task_relationship(t.id)["state"] == "closed"
    assert isinstance(
        queue.claim_one(space.worker_id, task_id=t.id, assignment_workspace=space), Task
    )
    current = queue.task_relationship(t.id)
    assert current and current["request"]["event_id"] != old_event
    with queue._connect() as conn:
        conn.execute("BEGIN")
        [actual] = preparation_origins(conn, t.id)
        assert actual.assignment_event_id == old_event
        # An unattributable legacy proposal must be visible, not quietly filtered out.
        altered = {
            **old,
            "request": {**old["request"], "assignment_event_id": current["request"]["event_id"]},
        }
        corrupt = conn.execute(
            "INSERT INTO task_events(task_id,ts,from_status,to_status,worker,note,submission_preparation)"
            " VALUES (?,100,'started','started',?,'fixture',?)",
            (t.id, space.worker_id, json.dumps(altered)),
        ).lastrowid
        assert corrupt is not None
        assert preparation_origins(conn, t.id) == (actual, PreparationOrigin(corrupt, None))
        conn.rollback()
    assert queue.submission_preparation(t.id) == old


@pytest.mark.parametrize("history", ["verified", "legacy", "enriched"])
def test_only_verified_terminal_detachment_can_identify_exact_pool_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: str,
) -> None:
    from agent_dispatch import completion_origins

    queue = TaskQueue(tmp_path / "queue.db")
    publish(queue, pool())
    t = task(queue)
    reservation, _ = place(queue, t)
    record_native(queue, reservation, worktree="tree", native="execution-native")
    queue.record_spawn(
        reservation.key,
        session_handle="execution-native",
        startup_complete=True,
        startup_revision=1,
    )
    queue.start(
        t.id,
        "host-a/tree",
        expected_generation=current_task(queue, t.id).generation,
        owner_session_id="execution-native",
    )
    payload = {"answer": [1, 2]}
    if history == "enriched":
        queue.complete(t.id, "host-a/tree")
    done = queue.complete(t.id, "host-a/tree", result=payload, result_ref="object:one")
    encoded = queue._encode_result(payload)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        assert not exact_detached_completion(
            conn, t.id, worker_id="host-a/tree", encoded_result=encoded, result_ref="object:one"
        )
    queue.request_spawn_release(reservation.key, disposition="settled")
    with pytest.raises(TaskError):
        queue.recover_task(
            t.id,
            operation="detach_agent",
            expected_revision=queue.task_diagnostics(t.id)["revision"],
            request_id="detach",
            actor="operator",
            reason="retired native",
        )
    queue.retire_spawn(reservation.key, exact_absence=True)
    if history == "legacy":
        monkeypatch.setattr(completion_origins, "capture_terminal_detachment", lambda *_: None)
    revision = queue.task_diagnostics(t.id)["revision"]
    result = queue.recover_task(
        t.id,
        operation="detach_agent",
        expected_revision=revision,
        request_id="detach",
        actor="operator",
        reason="retired native",
    )
    qualified = history == "verified"
    assert ("completion_origin_v1" in result) is qualified
    if qualified:
        origin = result["completion_origin_v1"]
        assert (origin["reservation_key"], origin["generation"], origin["native_id"]) == (
            reservation.key,
            done.generation,
            "execution-native",
        )
        assert "answer" not in json.dumps(origin)
    reopened = TaskQueue(queue.db_path)
    after = current_task(reopened, t.id)
    assert (
        after.status == "completed"
        and after.generation == done.generation + 1
        and after.owner_session_id is None
    )
    with reopened._connect() as conn:
        conn.execute("BEGIN")
        for worker, value, ref, expected in (
            ("host-a/tree", encoded, "object:one", qualified),
            ("other/tree", encoded, "object:one", False),
            ("host-a/tree", None, "object:one", False),
            ("host-a/tree", encoded, None, False),
        ):
            assert (
                exact_detached_completion(
                    conn, t.id, worker_id=worker, encoded_result=value, result_ref=ref
                )
                is expected
            )
        if qualified:
            # Even the same worker asking for the same (now substituted) stored
            # bytes is not replaying the outcome the detach owner sealed.
            conn.execute("UPDATE tasks SET result_ref='different' WHERE id=?", (t.id,))
            assert not exact_detached_completion(
                conn,
                t.id,
                worker_id="host-a/tree",
                encoded_result=encoded,
                result_ref="different",
            )
            conn.rollback()
    assert reopened.recover_task(
        t.id,
        operation="detach_agent",
        expected_revision=revision,
        request_id="detach",
        actor="operator",
        reason="retired native",
    ) == {**result, "replayed": True}
    assert current_task(reopened, t.id) == after
