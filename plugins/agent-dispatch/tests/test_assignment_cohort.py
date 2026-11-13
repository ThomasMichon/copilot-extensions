"""Original cohort snapshots use queue evidence, never present-day labels."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.task_obligations import read_snapshot
from tests._helpers import TEST_REPO
from tests.test_assignment_relationships import assignment as assignment, claim
from tests.test_pool_placement import place, pool, publish


def managed_task(queue, request_id):
    grant = queue.handoff_producer_scope(
        TEST_REPO, "emitter", producer_id="source-reader", expected_generation=0,
    )
    task = queue.create(
        "managed", repo=TEST_REPO, source="emitter", origin_ref="source-reader",
        producer_scope={"repo": TEST_REPO, "source": "emitter"},
        producer_id="source-reader", producer_generation=grant.state.current_generation,
        producer_capability=grant.producer_capability, producer_request_id=request_id,
    )
    return task, grant


def test_original_emitter_receipt_survives_restart_handoff_and_replay(assignment):
    queue, owner, workspace, _ = assignment
    task, grant = managed_task(queue, "original-request")
    owner.lose_reply = "reserve"
    with pytest.raises(TaskError, match="unresolved"):
        claim(queue, workspace, task)
    original = queue.task_relationship(task.id)
    assert original["request"]["cohort"] == {
        "schema": "dispatch-assignment-cohort", "version": 1, "pool": None,
        "emitter": {
            "repo": task.repo, "source": "emitter", "producer_id": "source-reader",
            "generation": grant.state.current_generation,
            "request_id": "original-request", "task_id": task.id,
        },
    }
    queue.handoff_producer_scope(
        TEST_REPO, "emitter", producer_id="replacement",
        expected_generation=grant.state.current_generation,
    )
    restarted = TaskQueue(
        queue.db_path, relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = restarted
    assert restarted.reconcile_task_relationship(task.id)["state"] == "bound"
    assert restarted.task_relationship(task.id)["request"] == original["request"]
    assert read_snapshot(restarted, task.id)["relationship"] == original["request"]
    assert len(owner.claims) == 1
    assert "producer_capability" not in original["request"]["cohort"]["emitter"]


def test_pool_snapshot_keeps_registration_instance_and_assignment_not_source_labels(assignment):
    queue, _, workspace, _ = assignment
    task = queue.create(
        "unverified source", repo="example", source="emitter", origin_ref="source-reader",
    )
    publish(queue, pool("registered-worker", machine=workspace.machine, env="linux"))
    reservation, _ = place(queue, task)
    expected = asdict(queue.task_pool_assignment(task.id))
    queue.record_spawn_worktree(
        reservation.key, workspace.worktree, ownership="created", driver="cli",
        execution_machine=workspace.machine, allocation_project=workspace.project,
        worktree_path=workspace.path, creation_nonce=workspace.creation_nonce,
    )
    original = queue.task_relationship(task.id)
    assert original["request"]["cohort"] == {
        "schema": "dispatch-assignment-cohort", "version": 1,
        "emitter": None, "pool": expected,
    }
    # Later catalog changes are not the original allocation's definition.
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET catalog_revision=catalog_revision+1 WHERE task_id=?",
            (task.id,),
        )
    assert queue.task_relationship(task.id)["request"] == original["request"]
    assert read_snapshot(
        queue, task.id, assignment_event_id=original["request"]["event_id"],
    )["relationship"]["cohort"]["pool"] == expected
    with TestClient(create_app(
        queue, enable_mcp=False, verification_interval=0,
    )) as api:
        response = api.get(
            f"/tasks/{task.id}", params={
                "obligation_assignment_event_id": original["request"]["event_id"],
            },
        )
    assert response.status_code == 200
    assert response.json()["task_obligation"]["relationship"]["cohort"]["pool"] == expected


def test_assignment_history_and_returned_mutation_cannot_relabel_original_cohort(assignment):
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    original = queue.task_relationship(task.id)
    queue.yield_task(task.id, workspace.worker_id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    claim(queue, workspace, task)
    current = queue.task_relationship(task.id)
    assert current["request"]["event_id"] != original["request"]["event_id"]
    assert current["request"]["generation"] > original["request"]["generation"]
    current["request"]["cohort"]["pool"] = {"registration_id": "replacement"}
    assert queue.task_relationship(task.id)["request"]["cohort"]["pool"] is None
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE task_events SET task_relationship=? WHERE id=?",
            (json.dumps(current), current["request"]["event_id"]),
        )
    assert queue.task_relationship(
        task.id, event_id=original["request"]["event_id"],
    )["request"] == original["request"]


def test_old_relationship_is_not_backfilled_from_current_source_or_pool(assignment):
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    original = queue.task_relationship(task.id)
    del original["request"]["cohort"]
    original.update(state="pending", receipt=None)
    with queue._connect() as conn:
        event = conn.execute(
            "INSERT INTO task_events(task_id,ts,from_status,to_status,worker,note)"
            " VALUES (?,0,'queued','claimed',?,'claim')", (task.id, workspace.worker_id),
        ).lastrowid
        original["request"]["event_id"] = event
        original["request"]["identity"]["assignment_event_id"] = event
        original["request"]["recovery"]["assignment_event_id"] = event
        conn.execute(
            "UPDATE task_events SET task_relationship=? WHERE id=?",
            (json.dumps(original), event),
        )
    restarted = TaskQueue(queue.db_path)
    assert restarted.task_relationship(task.id) == original
    assert "cohort" not in read_snapshot(restarted, task.id)["relationship"]
