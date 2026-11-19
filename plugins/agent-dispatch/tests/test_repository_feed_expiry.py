"""Original native deadline wakes once without service completion or receipt loss."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any

import pytest

from agent_dispatch.queue_records import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_repository_feed_attempts import prepared
from tests.test_repository_feed_preparation import prepare, setup

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.expiry")


def test_initial_preparation_result_expires_without_a_reload(tmp_path: Path) -> None:
    queue, task_id, spec, _client, preparer = setup(tmp_path)
    initial = asyncio.run(prepare(preparer, task_id, spec))
    assert "id" not in initial
    deadline = spec["feed"]["deadline_at"]
    with pytest.raises(TaskError, match="identity differs"):
        queue.expire_repository_feed_waiter({**initial, "id": -1}, now=deadline)
    result = queue.expire_repository_feed_waiter(initial, now=deadline)
    assert result is not None and result["state"] == "retired"
    reopened = TaskQueue(tmp_path / "tasks.db")
    repeated = reopened.expire_repository_feed_waiter(initial, now=deadline + 1)
    assert repeated is not None and repeated["wake_id"] == result["wake_id"]
    wake, = reopened.list_run_waiter_wakes(task_id)
    assert wake.id == result["wake_id"] and wake.status == "pending"
    task = reopened.get(task_id)
    assert task is not None and task.status == "suspended"


@pytest.mark.parametrize("armed", [False, True])
def test_original_deadline_retirement_and_native_wake_reopen_once(
    tmp_path: Path, armed: bool,
) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    if armed:
        queue.replay_waiter_progress(waiter, attempt=True)
        queue.replay_waiter_progress(
            waiter, registration_id="original-receipt",
            registration_expires_at=int(spec["feed"]["deadline_at"]),
        )
        armed_waiter = queue.arm_run_waiter(
            task_id, generation=waiter["generation"], kind="delegated",
            delegation={**spec, "registration_id": "original-receipt"},
        )
        assert armed_waiter is not None
        waiter = armed_waiter
    before = queue.get(task_id)
    deadline = spec["feed"]["deadline_at"]
    with pytest.raises(TaskError, match="has not elapsed"):
        queue.expire_repository_feed_waiter(waiter, now=deadline - 0.01)
    assert not queue.list_run_waiter_wakes(task_id)
    result = queue.expire_repository_feed_waiter(waiter, now=deadline)
    assert result is not None and result["state"] == "retired" and result["event_id"] is None
    assert not result["replay_settled"] and result["replay_attempted"] == armed
    assert result["replay_registration_id"] == ("original-receipt" if armed else None)
    queue = TaskQueue(tmp_path / "tasks.db")
    repeated = queue.expire_repository_feed_waiter(waiter, now=deadline + 100)
    assert repeated is not None and repeated["wake_id"] == result["wake_id"]
    wake, = queue.list_run_waiter_wakes(task_id)
    assert wake.id == result["wake_id"] and wake.status == "pending"
    task = queue.get(task_id)
    assert task is not None and task.status == "suspended"
    assert before is not None and task.owner_session_id == before.owner_session_id


@pytest.mark.parametrize("change", [
    {"hold_reason": "operator hold"}, {"awaiting_steer": 1},
    {"owner_session_id": "successor"}, {"generation": 2},
    {"card": '{"summary":"new instruction"}'}, {"resume_requested": 1},
])
def test_expiry_cannot_override_new_native_admission(
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
        queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    current = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and current["state"] == "preparing"
    assert queue.get(task_id) == before and not queue.list_run_waiter_wakes(task_id)


def test_operator_event_wake_is_not_replaced_by_expiry(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    queue.append_event_note(task_id, sender="operator", note="new instruction", wake_agent=True)
    original, = queue.list_run_waiter_wakes(task_id)
    assert queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"]) is None
    assert queue.list_run_waiter_wakes(task_id) == [original]


def test_timeout_and_outbox_share_the_existing_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    original = queue._enqueue_run_waiter_wake

    def interrupted(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise RuntimeError("interrupted before commit")

    monkeypatch.setattr(queue, "_enqueue_run_waiter_wake", interrupted)
    with pytest.raises(RuntimeError, match="before commit"):
        queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    reopened = TaskQueue(tmp_path / "tasks.db")
    current = reopened.find_delegated_preparation(task_id, spec["preparation_id"])
    assert current is not None and current["state"] == "preparing"
    assert not reopened.list_run_waiter_wakes(task_id)


def test_lost_committed_result_reuses_its_original_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)

    def lost() -> None:
        raise RuntimeError("committed result lost")

    monkeypatch.setattr(queue, "_notify_wake", lost)
    with pytest.raises(RuntimeError, match="result lost"):
        queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    original, = queue.list_run_waiter_wakes(task_id)
    queue = TaskQueue(tmp_path / "tasks.db")
    result = queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    assert result is not None and result["wake_id"] == original.id
    assert queue.list_run_waiter_wakes(task_id) == [original]


def test_concurrent_expiry_uses_one_existing_native_wake(tmp_path: Path) -> None:
    queue, task_id, spec, waiter = prepared(tmp_path)
    barrier = threading.Barrier(2)

    def expire() -> dict[str, Any] | None:
        selected = TaskQueue(tmp_path / "tasks.db")
        barrier.wait(timeout=5)
        return selected.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _index: expire(), range(2)))
    first, second = results
    assert first is not None and second is not None
    assert first["wake_id"] == second["wake_id"]
    wake, = queue.list_run_waiter_wakes(task_id)
    assert wake.id == first["wake_id"]


@pytest.mark.parametrize("now", [float("nan"), float("inf")])
def test_invalid_clock_cannot_create_a_timeout(tmp_path: Path, now: float) -> None:
    queue, task_id, _, waiter = prepared(tmp_path)
    with pytest.raises(TaskError, match="has not elapsed"):
        queue.expire_repository_feed_waiter(waiter, now=now)
    assert not queue.list_run_waiter_wakes(task_id)
