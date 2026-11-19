"""Retained admission and transaction-owned subscription attempt fences."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agent_dispatch.queue_feed_preparation import FeedWaiterAdmission
from agent_dispatch.queue_records import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_repository_feed_preparation import prepare, setup

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.preparation")


def prepared(tmp_path: Path) -> tuple[TaskQueue, str, dict[str, Any], dict[str, Any]]:
    queue, task_id, spec, _, preparer = setup(tmp_path)
    asyncio.run(prepare(preparer, task_id, spec))
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    return queue, task_id, spec, waiter


def test_original_basis_survives_reopen_and_attempt_is_not_replayed(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    task = queue.get(task_id)
    assert task is not None and task.dedup_key is not None
    basis = FeedWaiterAdmission.from_record(waiter["feed_admission"])
    assert basis.suspended_steering_revision == task.steering_revision
    assert basis.steering_revision != task.steering_revision
    queue = TaskQueue(tmp_path / "tasks.db")
    retained = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert retained == waiter
    attempted = queue.replay_waiter_progress(retained, attempt=True)
    assert attempted["replay_attempted"] and attempted["delegation"] == spec
    assert attempted["feed_admission"] == waiter["feed_admission"]
    with pytest.raises(TaskError, match="already attempted"):
        queue.replay_waiter_progress(waiter, attempt=True)
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("change", [
    {"hold_reason": "operator hold"}, {"awaiting_steer": 1},
    {"owner_session_id": "successor"}, {"generation": 2}, {"owner": "other"},
    {"status": "queued"}, {"card": '{"summary":"new instruction"}'},
    {"dedup_key": "different"}, {"source": "other"}, {"origin_ref": "goal-driven"},
])
def test_changed_task_blocks_attempt_without_progress(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET " + ", ".join(key + "=?" for key in change) + " WHERE id=?",
            (*change.values(), task_id),
        )
    before = queue.get(task_id)
    with pytest.raises(TaskError):
        queue.replay_waiter_progress(waiter, attempt=True)
    assert queue.get(task_id) == before
    current = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and not current["replay_attempted"]
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("guard", ["execution", "relationship", "ordinary-wake", "waiter-wake"])
def test_native_closure_and_wakes_block_attempt(tmp_path: Path, guard: str) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    with queue._connect() as conn:
        if guard == "execution":
            conn.execute(
                "INSERT INTO execution_admission_closures VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("scope", "harness", "recipe", "original-close", "{}", 1, 1),
            )
        elif guard == "relationship":
            conn.execute(
                "UPDATE task_events SET task_relationship=? WHERE id="
                "(SELECT MAX(id) FROM task_events WHERE task_id=?)",
                (json.dumps({"state": "pending", "request": {"generation": 1}}), task_id),
            )
        elif guard == "ordinary-wake":
            conn.execute(
                "INSERT INTO wake_outbox(id,task_id,owner,owner_session_id,generation,"
                "message,wake_seq,status,created_at,updated_at) VALUES (?,?,?,?,?,?,1,'pending',1,1)",
                ("wake", task_id, waiter["owner"], waiter["owner_session_id"],
                 waiter["task_generation"], "instruction"),
            )
            conn.execute("UPDATE tasks SET wake_operation_id='wake' WHERE id=?", (task_id,))
    if guard == "waiter-wake":
        queue.append_event_note(task_id, sender="operator", note="instruction", wake_agent=True)
    with pytest.raises(TaskError):
        queue.replay_waiter_progress(waiter, attempt=True)
    current = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and not current["replay_attempted"]


def test_missing_historical_basis_refuses_without_inventing_current_basis(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    with queue._connect() as conn:
        conn.execute("UPDATE run_waiters SET feed_admission_json=NULL WHERE id=?", (waiter["id"],))
    queue = TaskQueue(tmp_path / "tasks.db")
    historical = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert historical is not None and historical["feed_admission"] is None
    with pytest.raises(TaskError, match="unsupported"):
        queue.replay_waiter_progress(historical, attempt=True)
    current = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and current["feed_admission"] is None
    assert not current["replay_attempted"]


def test_prior_schema_migration_preserves_original_intent_without_admission(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    with queue._connect() as conn:
        conn.execute("ALTER TABLE run_waiters DROP COLUMN feed_admission_json")
    reopened = TaskQueue(tmp_path / "tasks.db")
    historical = reopened.find_delegated_preparation(task_id, spec["preparation_id"])
    assert historical is not None and historical["delegation"] == waiter["delegation"]
    assert historical["feed_admission"] is None
    with pytest.raises(TaskError, match="unsupported"):
        reopened.replay_waiter_progress(historical, attempt=True)
    assert not reopened.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("field,value", [
    ("protocol", "future"), ("task_id", "foreign"), ("worker_id", "foreign"),
    ("owner_session_id", "foreign"), ("task_generation", True), ("lane", ""),
])
def test_invalid_retained_basis_refuses_on_reopen(
    tmp_path: Path, field: str, value: object,
) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    basis = copy.deepcopy(waiter["feed_admission"])
    basis[field] = value
    with queue._connect() as conn:
        conn.execute(
            "UPDATE run_waiters SET feed_admission_json=? WHERE id=?",
            (json.dumps(basis), waiter["id"]),
        )
    with pytest.raises(TaskError):
        TaskQueue(tmp_path / "tasks.db").find_delegated_preparation(task_id, spec["preparation_id"])


@pytest.mark.parametrize("stage", ["attempt", "arm"])
def test_original_deadline_remains_effective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    if stage == "arm":
        queue.replay_waiter_progress(waiter, attempt=True)
        queue.replay_waiter_progress(waiter, registration_id="receipt")
    monkeypatch.setattr("time.time", lambda: spec["feed"]["deadline_at"])
    with pytest.raises(TaskError, match="deadline elapsed"):
        if stage == "attempt":
            queue.replay_waiter_progress(waiter, attempt=True)
        else:
            queue.arm_run_waiter(
                task_id, generation=waiter["generation"], kind="delegated",
                delegation={**spec, "registration_id": "receipt"},
            )
    current = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and current["state"] == "preparing"
    assert not queue.list_run_waiter_wakes(task_id)


def test_changed_task_retains_unknown_result_but_cannot_arm_it(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    queue.replay_waiter_progress(waiter, attempt=True)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET awaiting_steer=1 WHERE id=?", (task_id,))
    recovered = queue.replay_waiter_progress(waiter, registration_id="original-receipt")
    with pytest.raises(TaskError, match="task changed"):
        queue.arm_run_waiter(
            task_id, generation=waiter["generation"], kind="delegated",
            delegation={**spec, "registration_id": "original-receipt"},
        )
    current = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and current["replay_registration_id"] == "original-receipt"
    assert current["feed_admission"] == recovered["feed_admission"]
    assert current["state"] == "preparing" and current["replay_attempted"]
    assert not queue.list_run_waiter_wakes(task_id)


def test_admitted_receipt_arms_without_another_request_or_wake(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    queue.replay_waiter_progress(waiter, attempt=True)
    queue.replay_waiter_progress(waiter, registration_id="original-receipt")
    armed = queue.arm_run_waiter(
        task_id, generation=waiter["generation"], kind="delegated",
        delegation={**spec, "registration_id": "original-receipt"},
    )
    assert armed is not None and armed["state"] == "active"
    assert armed["feed_admission"] == waiter["feed_admission"]
    assert not queue.list_run_waiter_wakes(task_id)
