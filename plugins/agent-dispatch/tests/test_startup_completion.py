"""Startup completion is recorded by the launch caller, never inferred."""

from __future__ import annotations

import json
from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


def test_startup_completion_is_explicit_and_invalidated_by_identity_change(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work")
    reservation, _ = queue.reserve_spawn(task.id)
    promoted = queue.record_spawn(reservation.key, session_handle="local-body:original")
    assert promoted.startup_complete is False
    completed = queue.record_spawn(
        reservation.key, session_handle="local-body:original", startup_complete=True, startup_revision=1,
    )
    assert completed.startup_complete is True
    assert queue.record_spawn(reservation.key).startup_complete is True
    refreshed = queue.record_spawn(reservation.key, session_handle="local-body:replacement")
    assert refreshed.startup_complete is False


def test_pending_resume_revokes_completion_before_callback(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original", startup_complete=True, startup_revision=1,
    )
    pending = queue.record_spawn(
        reservation.key, session_handle="local-body:original", startup_complete=False, startup_revision=1,
    )
    assert pending.startup_complete is False
    assert queue.record_spawn(reservation.key).startup_complete is False


def test_stale_completion_cannot_close_a_newer_pending_startup(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    pending = queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=False, startup_revision=reservation.startup_revision,
    )
    assert pending.startup_revision == reservation.startup_revision + 1
    for completion in (False, True):
        with pytest.raises(TaskError, match="startup revision changed"):
            queue.record_spawn(
                reservation.key, session_handle="local-body:original",
                startup_complete=completion, startup_revision=reservation.startup_revision,
            )
    assert queue.get_reservation(reservation.key).startup_complete is False
    completed = queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=pending.startup_revision,
    )
    assert completed.startup_complete is True


def test_terminal_fence_blocks_future_start_without_releasing_resource(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work", exclusive_key="resource:retained")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.abandon(task.id, permitted=True, reason="Assignment withdrawn")
    fenced = queue.fence_spawn_startup(
        reservation.key, startup_revision=reservation.startup_revision,
    )
    assert fenced.startup_fenced is True
    assert fenced.state == "spawned"
    assert fenced.exclusive_key == "resource:retained"
    with pytest.raises(TaskError, match="terminal startup is fenced"):
        queue.record_spawn(
            reservation.key, session_handle="local-body:original",
            startup_complete=False, startup_revision=fenced.startup_revision,
        )
    with pytest.raises(TaskError, match="terminal startup is fenced"):
        queue.record_spawn(reservation.key, session_handle="local-body:replacement")
    sibling = queue.create("Same resource", exclusive_key="resource:retained")
    assert queue.reserve_spawn(sibling.id)[1] is False
    assert queue.fence_spawn_startup(
        reservation.key, startup_revision=fenced.startup_revision,
    ) == fenced


@pytest.mark.parametrize("value", [1, "true", {}, []])
def test_startup_completion_requires_a_literal_boolean(tmp_path, value):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work")
    reservation, _ = queue.reserve_spawn(task.id)
    with pytest.raises(TaskError, match="must be a boolean"):
        queue.record_spawn(
            reservation.key, session_handle="local-body:original", startup_complete=value, startup_revision=1,
        )
    assert queue.get_reservation(reservation.key).startup_complete is False


def test_startup_completion_requires_a_handle(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work")
    reservation, _ = queue.reserve_spawn(task.id)
    with pytest.raises(TaskError, match="exact session handle"):
        queue.record_spawn(reservation.key, startup_complete=True, startup_revision=1)


def test_old_reservations_migrate_without_fabricating_completion(tmp_path):
    path = tmp_path / "tasks.db"
    queue = TaskQueue(path)
    task = queue.create("Retained work", exclusive_key="resource:retained")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(reservation.key, session_handle="local-body:retained")
    before = asdict(queue.get_reservation(reservation.key))
    before.pop("startup_complete")
    with queue._connect() as conn:
        conn.execute("ALTER TABLE spawn_reservations DROP COLUMN startup_complete")
        conn.execute("ALTER TABLE spawn_reservations DROP COLUMN startup_revision")
    migrated = TaskQueue(path).get_reservation(reservation.key)
    after = asdict(migrated)
    assert after.pop("startup_complete") is False
    assert after == before


def test_startup_completion_round_trips_client_api_and_storage(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Work")
    reservation, _ = queue.reserve_spawn(task.id)
    sent = []
    with TestClient(create_app(queue)) as api:
        def forward(request):
            sent.append(json.loads(request.content))
            result = api.post(
                request.url.path, content=request.content,
                headers={"content-type": request.headers["content-type"]},
            )
            return httpx.Response(result.status_code, json=result.json())

        with DispatchClient("http://testserver", transport=httpx.MockTransport(forward)) as client:
            result = client.record_spawn(
                reservation.key, session_handle="local-body:original", startup_complete=True, startup_revision=1,
            )
            assert result["startup_complete"] is True
            result = client.record_spawn(
                reservation.key, session_handle="local-body:original", startup_complete=False, startup_revision=1,
            )
            assert result["startup_complete"] is False
            client.record_spawn(
                reservation.key, session_handle="local-body:original",
                startup_complete=True, startup_revision=result["startup_revision"],
            )
            queue.abandon(task.id, permitted=True, reason="Assignment withdrawn")
            fenced = client.fence_spawn_startup(
                reservation.key, startup_revision=result["startup_revision"],
            )
            assert fenced["startup_fenced"] is True
        assert sent[0]["startup_complete"] is True
        assert sent[1]["startup_complete"] is False
        rejected = api.post(
            f"/spawn-reservations/{reservation.key}/spawned",
            json={"session_handle": "local-body:original", "startup_complete": "true"},
        )
        assert rejected.status_code == 422
    assert queue.get_reservation(reservation.key).startup_fenced is True
