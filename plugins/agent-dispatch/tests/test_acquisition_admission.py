"""Native start identity and activity samples cannot inherit a prior turn's idle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch import tracking
from agent_dispatch.__main__ import build_parser
from tests._helpers import RepoDefaultingQueue as TaskQueue
from agent_dispatch.queue import TaskError

pytestmark = pytest.mark.contract("agent_dispatch.acquisition_admission")


def test_native_start_argument_constructs_exact_request_without_ambient_identity(monkeypatch):
    args = build_parser().parse_args([
        "start", "task-a", "headless-reservation-a", "--owner-session-id", "native-a",
    ])
    assert args.owner_session_id == "native-a"
    client = DispatchClient.__new__(DispatchClient)
    client._http = MagicMock()
    monkeypatch.setattr(client, "_unwrap", lambda response: response)
    client.start("task-a", "headless-reservation-a", owner_session_id=args.owner_session_id)
    client._http.post.assert_called_once_with(
        "/tasks/task-a/start",
        json={"worker_id": "headless-reservation-a", "owner_session_id": "native-a"},
    )


def test_claim_start_clear_prior_idle_and_reject_prestart_activity_sample(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", now=10)
    reservation, _ = queue.reserve_spawn(task.id, now=11)
    queue.record_spawn(reservation.key, session_handle="local-body:bridge-a", now=12)
    queue.set_activity(task.id, "IDLE", reservation_key=reservation.key, now=13)
    claimed = queue.claim_one("acquired-reservation-a", task_id=task.id, now=14)
    assert claimed.activity is None
    # A snapshot taken after claim but before start is no longer current once
    # start has established a new native turn's admission.
    queue.set_activity(task.id, "IDLE", reservation_key=reservation.key, now=15)
    with pytest.raises(TaskError, match="exact native"):
        queue.start(task.id, "acquired-reservation-a", now=16)
    assert queue.get(task.id).status == "claimed"
    started = queue.start(task.id, "acquired-reservation-a", owner_session_id="native-a", now=16)
    assert started.owner_session_id == "native-a"
    assert started.activity is None
    stale = queue.set_activity(
        task.id, "IDLE", reservation_key=reservation.key, observed_at=15, now=17,
    )
    assert stale.activity is None
    active = queue.set_activity(
        task.id, "ACTIVE", reservation_key=reservation.key, observed_at=17, now=18,
    )
    assert active.activity == "ACTIVE"
    with pytest.raises(TaskError, match="finite"):
        queue.set_activity(
            task.id, "IDLE", reservation_key=reservation.key, observed_at=float("nan"), now=19,
        )


def test_active_owned_turn_beats_lingering_idle_metadata():
    assert tracking.session_activity({
        "status": "running", "at_rest": False, "liveness": "idle", "turn_state": "idle",
    }) == "ACTIVE"
    assert tracking.session_activity({
        "status": "idle", "at_rest": True, "liveness": "idle", "turn_state": "idle",
    }) == "IDLE"
    assert tracking.session_activity({
        "status": "running", "at_rest": False, "liveness": "disconnected",
    }) is None
