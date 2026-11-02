"""Fresh spawn admission respects the queue's existing scheduling deadline."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, Status, TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskQueue:
    monkeypatch.setattr("agent_dispatch.queue_storage.time.time", lambda: 100.0)
    return TaskQueue(tmp_path / "tasks.db")


@pytest.mark.parametrize("exclusive_key", [None, "review:example:42"])
def test_future_task_refuses_fresh_reservation(
    q: TaskQueue, exclusive_key: str | None,
) -> None:
    task = q.create("scheduled work", not_before=200.0, exclusive_key=exclusive_key)

    with pytest.raises(TaskError, match="spawn reservation requires not_before <= now"):
        q.reserve_spawn(task.id, reserved_by="supervisor")

    assert q.list_reservations(task_id=task.id) == []
    assert q.get(task.id) == task
    assert q.claim_one("machine/worker", task_id=task.id) is None


@pytest.mark.parametrize("now", [199.999, 200.0, 200.001])
def test_reservation_and_claim_share_the_due_boundary(
    q: TaskQueue, now: float,
) -> None:
    task = q.create("scheduled work", not_before=200.0)

    if now < task.not_before:
        with pytest.raises(TaskError, match="not_before"):
            q.reserve_spawn(task.id, now=now)
        assert q.latest_reservation(task.id) is None
        assert q.claim_one("machine/worker", task_id=task.id, now=now) is None
    else:
        reservation, reserved = q.reserve_spawn(task.id, now=now)
        assert reserved is True
        assert reservation.attempt == 1
        assert reservation.reserved_at == now
        assert q.get(task.id) == task
        claimed = q.claim_one("machine/worker", task_id=task.id, now=now)
        assert claimed is not None
        assert claimed.status == Status.CLAIMED
        assert claimed.not_before == task.not_before


@pytest.mark.parametrize("exclusive_key", [None, "review:example:42"])
def test_active_replay_before_due_preserves_original_reservation(
    q: TaskQueue, exclusive_key: str | None,
) -> None:
    task = q.create("scheduled work", not_before=200.0, exclusive_key=exclusive_key)
    original, _ = q.reserve_spawn(task.id, now=200.0)

    replay, reserved = q.reserve_spawn(task.id, now=100.0)

    assert reserved is False
    assert replay == original
    assert q.list_reservations(task_id=task.id) == [original]
    assert q.get(task.id) == task


def test_exclusive_blocker_remains_visible_before_due(q: TaskQueue) -> None:
    incumbent = q.create("incumbent", exclusive_key="review:example:42")
    original, _ = q.reserve_spawn(incumbent.id)
    task = q.create(
        "scheduled work", not_before=200.0, exclusive_key=incumbent.exclusive_key,
    )

    blocker, reserved = q.reserve_spawn(task.id)

    assert reserved is False
    assert blocker == original
    assert q.list_reservations(task_id=task.id) == []
    assert q.get(task.id) == task


def test_failed_attempt_cannot_retry_before_due(q: TaskQueue) -> None:
    task = q.create("scheduled work", not_before=200.0)
    original, _ = q.reserve_spawn(task.id, now=200.0)
    failed = q.fail_spawn(original.key, detail="startup failed", now=201.0)
    before_refusal = q.get(task.id)

    with pytest.raises(TaskError, match="not_before"):
        q.reserve_spawn(task.id, now=199.0)

    assert q.list_reservations(task_id=task.id) == [failed]
    assert q.get(task.id) == before_refusal
    assert before_refusal is not None
    assert before_refusal.not_before == task.not_before
    retry, reserved = q.reserve_spawn(task.id, now=200.0)
    assert reserved is True
    assert retry.attempt == 2
    assert retry.creation_key == original.creation_key
    assert retry.allocation_reservation_key == original.allocation_reservation_key


def test_suspended_reembodiment_is_not_new_queued_admission(q: TaskQueue) -> None:
    task = q.create("scheduled work", not_before=200.0)
    worker = "machine/retained-worktree"
    q.claim_one(worker, task_id=task.id, now=200.0)
    q.start(task.id, worker, now=201.0)
    suspended = q.suspend(task.id, worker, reason="waiting", now=202.0)

    with pytest.raises(TaskError, match="queued and unowned"):
        q.reserve_spawn(task.id, now=100.0)
    reservation, reserved = q.reserve_spawn(
        task.id, allow_suspended_reembodiment=True, now=100.0,
    )

    assert reserved is True
    assert reservation.state == SpawnState.RESERVING
    assert reservation.worktree == "retained-worktree"
    assert reservation.worktree_ownership == "reused"
    assert q.get(task.id) == suspended


def test_unscheduled_task_keeps_immediate_admission(q: TaskQueue) -> None:
    task = q.create("immediate work")

    reservation, reserved = q.reserve_spawn(task.id)

    assert reserved is True
    assert reservation.reserved_at == 100.0
    assert q.get(task.id) == task


@pytest.mark.parametrize("route", ["/spawn-reservations", "/spawn-reservations/revisions"])
def test_http_reservation_refuses_until_due(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch, route: str,
) -> None:
    task = q.create("scheduled work", not_before=200.0)
    api = TestClient(create_app(q))
    body = {"task_id": task.id, "allocation_revision": 1}

    refused = api.post(route, json=body)

    assert refused.status_code == 409
    assert refused.json() == {
        "detail": (
            f"task {task.id!r} is not due until 200.0; "
            "spawn reservation requires not_before <= now"
        ),
    }
    assert q.list_reservations(task_id=task.id) == []
    assert q.get(task.id) == task
    monkeypatch.setattr("agent_dispatch.queue_storage.time.time", lambda: 200.0)
    admitted = api.post(route, json=body)
    assert admitted.status_code == 200
    assert admitted.json()["reserved"] is True
    assert admitted.json()["reservation"]["attempt"] == 1
