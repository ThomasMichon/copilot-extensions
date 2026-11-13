"""Terminal closeout needs independently observed finalization, not readiness."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_common import CompletionOutcome
from tests import test_task_relevance_closeout_readiness as readiness

managed_assignment = readiness.managed_assignment
assignment = readiness.assignment
ready = readiness.ready


def finish(value: readiness.Ready) -> CompletionOutcome:
    queue, _, workspace, task, _ = value
    return queue.finish_relevance_closeout(
        task.id, request_id="closed-source", worker_id=workspace.worker_id,
        native_id="native", expected_generation=queue.get(task.id).generation,
    )


@pytest.fixture
def finalized(ready: readiness.Ready, monkeypatch: pytest.MonkeyPatch) -> readiness.Ready:
    from agent_dispatch import pool_decline_observation

    queue, _, workspace, task, _ = ready
    assert readiness.prepare(queue, workspace, task)["relationship"]["state"] == "closed"

    def observe(task_row: dict, allocation: dict, native: dict) -> dict:
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
        assert task_row["id"] == task.id
        assert allocation["key"] == queue.task_relationship(task.id)["request"]["reservation_key"]
        assert native["native_id"] == "native"
        return {
            "operation": "observe-finalized", "creation_nonce": workspace.creation_nonce,
            "status": "finalized", "checkout_retained": True, "process_retirement": False,
        }

    monkeypatch.setattr(pool_decline_observation, "observe", observe)
    return ready


def test_observed_finalization_abandons_through_existing_lifecycle_without_native_release(
    finalized: readiness.Ready,
) -> None:
    queue, _, _, task, _ = finalized
    before = queue.get_reservation(queue.task_relationship(task.id)["request"]["reservation_key"])
    result = finish(finalized)
    assert result.event_type == "task.abandoned"
    assert result.task.status == Status.ABANDONED and result.task.owner is None
    assert result.task.owner_session_id == "native"
    assert queue.get_reservation(before.key) == before
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    assert closeout["acknowledgement"]["observation"]["status"] == "finalized"
    assert closeout["acknowledgement"]["process_retirement"] is False
    assert closeout["acknowledgement"]["binding"] == closeout["resource_binding"]


def test_readiness_without_finalization_cannot_acknowledge(
    ready: readiness.Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    queue, _, workspace, task, _ = ready
    readiness.prepare(queue, workspace, task)

    def blocked(*_: object) -> dict:
        raise TaskError("workspace is not finalized")

    monkeypatch.setattr(pool_decline_observation, "observe", blocked)
    with pytest.raises(TaskError, match="not finalized"):
        finish(ready)
    assert queue.get(task.id).status == Status.STARTED
    assert queue.relevance_snapshot(task.id)["closeout"].get("acknowledgement") is None


@pytest.mark.parametrize("update", ["heartbeat", "activity", "progress"])
def test_liveness_during_finalized_observation_preserves_closeout(
    finalized: readiness.Ready, monkeypatch: pytest.MonkeyPatch, update: str,
) -> None:
    from agent_dispatch import pool_decline_observation

    queue, _, workspace, task, _ = finalized
    observe = pool_decline_observation.observe
    before = queue.get(task.id)
    key = queue.task_relationship(task.id)["request"]["reservation_key"]
    reservation = queue.get_reservation(key)

    def interleaved(*args: object) -> dict:
        result = observe(*args)
        if update == "heartbeat":
            changed = queue.heartbeat(task.id, workspace.worker_id, now=before.updated_at + 1)
            assert changed.lease_expires_at != before.lease_expires_at
        elif update == "activity":
            changed = queue.set_activity(
                task.id, "ACTIVE", reservation_key=key, now=before.updated_at + 1,
            )
            assert changed.activity == "ACTIVE"
        else:
            changed = queue.record_progress(
                task.id, workspace.worker_id, phase="closeout", summary="workspace finalized",
                now=before.updated_at + 1,
            )
            assert changed.latest_progress != before.latest_progress
        return result

    monkeypatch.setattr(pool_decline_observation, "observe", interleaved)
    result = finish(finalized)
    assert result.event_type == "task.abandoned"
    assert result.task.status == Status.ABANDONED and result.task.owner is None
    assert queue.get_reservation(key) == reservation
    assert queue.relevance_snapshot(task.id)["closeout"]["acknowledgement"] is not None


def test_unprepared_claim_cannot_reach_finalized_observation(
    ready: readiness.Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    def unexpected(*_: object) -> dict:
        raise AssertionError("an unsettled original claim must retain custody")

    monkeypatch.setattr(pool_decline_observation, "observe", unexpected)
    with pytest.raises(TaskError, match="readiness"):
        finish(ready)
    assert ready[0].task_relationship(ready[3].id)["state"] == "bound"


@pytest.mark.parametrize("field,value", [
    ("request_id", "foreign"), ("worker_id", "foreign/worker"),
    ("native_id", "foreign"), ("expected_generation", True),
])
def test_committed_acknowledgement_is_not_transferable(
    finalized: readiness.Ready, field: str, value: str | bool,
) -> None:
    queue, _, workspace, task, _ = finalized
    finish(finalized)
    arguments = {
        "request_id": "closed-source", "worker_id": workspace.worker_id,
        "native_id": "native", "expected_generation": queue.get(task.id).generation,
    }
    arguments[field] = value
    with pytest.raises(TaskError, match="incarnation"):
        queue.finish_relevance_closeout(task.id, **arguments)


def test_restart_and_lost_terminal_reply_replay_without_observation_or_duplicate_event(
    finalized: readiness.Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    queue, owner, workspace, task, calls = finalized
    first = finish(finalized)
    before = queue.events(task.id)

    def unexpected(*_: object) -> dict:
        raise AssertionError("a committed outcome must not observe or finalize again")

    monkeypatch.setattr(pool_decline_observation, "observe", unexpected)
    restarted = TaskQueue(queue.db_path, relationship_writer=owner, relationship_authority="local")
    second = finish((restarted, owner, workspace, task, calls))
    assert second.task == first.task and second.event_type is None
    assert restarted.events(task.id) == before


def test_prepared_then_yielded_owner_finishes_and_retries_with_retained_native(
    finalized: readiness.Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    queue, owner, workspace, task, calls = finalized
    yielded = queue.yield_task(task.id, workspace.worker_id, release_spawn=False)
    assert yielded.owner is None and yielded.owner_session_id is None
    first = finish(finalized)
    assert first.event_type == "task.abandoned"
    assert first.task.owner_session_id is None
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    assert closeout["owner_session_id"] == "native"
    assert closeout["acknowledgement"]["task_attachment"] == {
        "generation": yielded.generation, "owner_session_id": None,
    }

    def unexpected(*_: object) -> dict:
        raise AssertionError("a yielded closeout replay must not repeat observation")

    monkeypatch.setattr(pool_decline_observation, "observe", unexpected)
    restarted = TaskQueue(queue.db_path, relationship_writer=owner, relationship_authority="local")
    second = finish((restarted, owner, workspace, task, calls))
    assert second.event_type is None and second.task == first.task


@pytest.mark.parametrize("change", [
    "hold", "native", "reservation", "relationship", "generation",
    "task-status", "task-title",
])
def test_change_during_finalized_observation_retains_task_and_receipt_absence(
    finalized: readiness.Ready, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_dispatch import pool_decline_observation

    queue, _, _, task, _ = finalized
    observe = pool_decline_observation.observe

    def changed(*args: object) -> dict:
        result = observe(*args)
        with queue._connect() as conn:
            if change == "hold":
                conn.execute("UPDATE tasks SET hold_reason='operator' WHERE id=?", (task.id,))
            elif change == "native":
                conn.execute(
                    "UPDATE task_relevance_closeouts SET owner_session_id='foreign'"
                    " WHERE task_id=?",
                    (task.id,),
                )
            elif change == "generation":
                conn.execute(
                    "UPDATE task_relevance_closeouts SET generation=generation+1 WHERE task_id=?",
                    (task.id,),
                )
            elif change == "relationship":
                conn.execute(
                    "UPDATE task_events SET task_relationship=json_set(task_relationship,"
                    " '$.state','bound') WHERE task_id=? AND task_relationship IS NOT NULL",
                    (task.id,),
                )
            elif change.startswith("task-"):
                column, value = {
                    "task-status": ("status", Status.SUSPENDED),
                    "task-title": ("title", "changed source intent"),
                }[change]
                conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task.id))
            else:
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested=1 WHERE task_id=?",
                    (task.id,),
                )
        return result

    monkeypatch.setattr(pool_decline_observation, "observe", changed)
    with pytest.raises(TaskError, match=r"closeout|incarnation|held|expected status"):
        finish(finalized)
    expected_status = Status.SUSPENDED if change == "task-status" else Status.STARTED
    assert queue.get(task.id).status == expected_status
    assert queue.relevance_snapshot(task.id)["closeout"].get("acknowledgement") is None


def test_changed_workspace_incarnation_cannot_authorize_terminal_transition(
    finalized: readiness.Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    observe = pool_decline_observation.observe

    def replaced(*args: object) -> dict:
        return {**observe(*args), "creation_nonce": "replacement"}

    monkeypatch.setattr(pool_decline_observation, "observe", replaced)
    with pytest.raises(TaskError, match="incarnation"):
        finish(finalized)
    assert finalized[0].get(finalized[3].id).status == Status.STARTED


def test_failed_task_write_rolls_back_acknowledgement(finalized: readiness.Ready) -> None:
    queue, _, _, task, _ = finalized
    with queue._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_test_terminal BEFORE UPDATE OF status ON tasks"
            " WHEN NEW.status='abandoned' BEGIN SELECT RAISE(ABORT,'test rollback'); END",
        )
    with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
        finish(finalized)
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    assert closeout.get("acknowledgement") is None
    assert queue.get(task.id).status == Status.STARTED


def test_concurrent_acknowledgements_commit_one_terminal_outcome(
    finalized: readiness.Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    queue, _, _, task, _ = finalized
    observe = pool_decline_observation.observe
    barrier = Barrier(2)

    def together(*args: object) -> dict:
        result = observe(*args)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(pool_decline_observation, "observe", together)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: finish(finalized), range(2)))
    assert sorted(result.event_type or "" for result in results) == ["", "task.abandoned"]
    with queue._connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM task_events WHERE task_id=? AND to_status='abandoned'",
            (task.id,),
        ).fetchone()[0] == 1
        assert json.loads(conn.execute(
            "SELECT acknowledgement FROM task_relevance_closeouts WHERE task_id=?", (task.id,),
        ).fetchone()[0])["observation"]["status"] == "finalized"
