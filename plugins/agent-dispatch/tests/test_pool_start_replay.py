"""Response-loss retries retain the original pool start request and custody."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnReservation, Status, Task, TaskError, TaskQueue
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import headers, place, pool, publish, record_native, task

q = placement_controls.q
StartFixture = tuple[TestClient, str, Task, SpawnReservation, dict[str, str | int | None]]


def snapshot(q: TaskQueue, task_id: str) -> tuple:
    with q._connect() as conn:
        attachments = [
            dict(row) for row in conn.execute(
                "SELECT * FROM task_attachments WHERE task_id = ? ORDER BY id", (task_id,),
            )
        ]
    return (
        q.get(task_id), q.list_reservations(task_id=task_id),
        q.task_pool_assignment(task_id), attachments,
    )


@pytest.fixture
def placed_start(q: TaskQueue) -> StartFixture:
    publish(q, pool())
    requested = task(q)
    reservation, _ = place(q, requested)
    record_native(q, reservation)
    client = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    body: dict[str, str | int | None] = {
        "worker_id": "host-a/worktree", "owner_session_id": "native",
        "expected_generation": q.get(requested.id).generation,
    }
    return client, f"/tasks/{requested.id}/start", requested, reservation, body


@pytest.mark.parametrize("explicit_null", [False, True])
def test_http_unchanged_initial_start_retries_after_response_loss(
    q: TaskQueue, placed_start: StartFixture, explicit_null: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, url, requested, _, body = placed_start
    if explicit_null:
        body["expected_owner_session_id"] = None
    first = client.post(url, headers=headers(), json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == Status.STARTED
    expected = first.json()
    assert expected.pop("task_token")
    before = snapshot(q, requested.id)
    assert len(before[3]) == 1
    for clock in (101.0, 102.0):
        monkeypatch.setattr(q, "_now", lambda now=None, clock=clock: clock if now is None else now)
        replay = client.post(url, headers=headers(), json=body)
        assert replay.status_code == 200, replay.text
        observed = replay.json()
        assert observed.pop("task_token")
        assert observed == expected
        assert snapshot(q, requested.id) == before
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("change", [
    {"worker_id": "host-b/worktree"},
    {"worker_id": "host-a/other"},
    {"worker_id": "HOST-A/worktree"},
    {"owner_session_id": "other-native"},
    {"expected_generation": 99},
    {"expected_owner_session_id": "other-native"},
])
def test_http_start_retry_rejects_mismatched_fences_without_mutation(
    q: TaskQueue, placed_start: StartFixture, started: bool, change: dict[str, str | int],
) -> None:
    client, url, requested, _, body = placed_start
    if started:
        response = client.post(url, headers=headers(), json=body)
        assert response.status_code == 200, response.text
    elif change == {"worker_id": "HOST-A/worktree"}:
        with q._connect() as conn:
            conn.execute(
                "UPDATE tasks SET owner = ? WHERE id = ?", ("host-a/worktree", requested.id),
            )
    before = snapshot(q, requested.id)
    response = client.post(url, headers=headers(), json={**body, **change})
    assert response.status_code == 409, response.text
    assert snapshot(q, requested.id) == before


def test_http_initial_start_preserves_claimed_owner_incarnation_fence(
    q: TaskQueue, placed_start: StartFixture,
) -> None:
    client, url, requested, _, body = placed_start
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET owner_session_id = ? WHERE id = ?", ("native", requested.id),
        )
    before = snapshot(q, requested.id)
    response = client.post(url, headers=headers(), json=body)
    assert response.status_code == 409, response.text
    assert snapshot(q, requested.id) == before
    response = client.post(
        url, headers=headers(), json={**body, "expected_owner_session_id": "native"},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("started", [False, True])
def test_http_start_replay_preserves_operator_hold_without_new_transition(
    q: TaskQueue, placed_start: StartFixture, started: bool,
) -> None:
    client, url, requested, _, body = placed_start
    if started:
        response = client.post(url, headers=headers(), json=body)
        assert response.status_code == 200, response.text
    q.set_hold(requested.id, reason="operator hold", actor="operator")
    before = snapshot(q, requested.id)
    response = client.post(url, headers=headers(), json=body)
    assert response.status_code == (200 if started else 409), response.text
    assert snapshot(q, requested.id) == before


@pytest.mark.parametrize("fence", ["release", "startup", "cooling", "retired"])
def test_http_started_replay_rechecks_reservation_fences(
    q: TaskQueue, placed_start: StartFixture, fence: str,
) -> None:
    client, url, requested, reservation, body = placed_start
    response = client.post(url, headers=headers(), json=body)
    assert response.status_code == 200, response.text
    if fence == "release":
        q.request_spawn_release(reservation.key, finished_startup_revision=1)
    else:
        column, value = {
            "startup": ("startup_fenced", 1),
            "cooling": ("cooling_token", "unresolved"),
            "retired": ("conversation_retired", 1),
        }[fence]
        with q._connect() as conn:
            conn.execute(
                f"UPDATE spawn_reservations SET {column} = ? WHERE key = ?",  # noqa: S608
                (value, reservation.key),
            )
    before = snapshot(q, requested.id)
    response = client.post(url, headers=headers(), json=body)
    assert response.status_code == 409, response.text
    assert snapshot(q, requested.id) == before
