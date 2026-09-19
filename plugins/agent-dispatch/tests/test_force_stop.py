"""Tests for Phase 2's ``force-stop`` (:mod:`agent_dispatch.force_stop`):
terminate a started task's exact current session (best-effort) and park it
suspended, without implying or requiring a durable pause hold.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agent_dispatch.force_stop import ForceStopError, force_stop
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


class _QueueBackedClient:
    """The narrow client surface `force_stop` needs, backed by a real
    `TaskQueue` (dicts in/out, mirroring `client.DispatchClient`'s shape)."""

    def __init__(self, queue: TaskQueue):
        self._q = queue

    def get(self, task_id):
        task = self._q.get(task_id)
        return asdict(task) if task is not None else None

    def suspend(
        self, task_id, worker_id, *, reason, expected_status=None,
        expected_generation=None, expected_owner_session_id=None,
        reject_pending_steer=True,
    ):
        return asdict(
            self._q.suspend(
                task_id, worker_id, reason=reason,
                expected_status=expected_status,
                expected_generation=expected_generation,
                expected_owner_session_id=expected_owner_session_id,
                reject_pending_steer=reject_pending_steer,
            )
        )


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def client(q):
    return _QueueBackedClient(q)


def test_force_stop_ends_local_session_and_suspends(q, client, monkeypatch):
    t = q.create("work", repo="r")
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt", owner_session_id="s1")

    ended = []
    monkeypatch.setattr(
        "agent_dispatch.bridge.force_end_session",
        lambda sid: ended.append(sid) or True,
    )

    result = force_stop(client, t.id, local_machine="m", actor="alice")

    assert ended == ["s1"]
    assert result["session"] == "s1"
    assert result["session_stopped"] is True
    assert result["task"]["status"] == Status.SUSPENDED
    back = q.get(t.id)
    assert back.status == Status.SUSPENDED
    assert back.owner == "m/wt"  # owner identity preserved for a later resume


def test_force_stop_ends_fleet_session_over_the_mesh(q, client, monkeypatch):
    t = q.create("work", repo="r")
    q.claim_one("pool-host/wt", task_id=t.id, machine="pool-host", worktree="wt")
    q.start(t.id, "pool-host/wt", owner_session_id="s1")

    calls = []
    monkeypatch.setattr(
        "agent_dispatch.embody.stop_fleet_body",
        lambda host, sid: calls.append((host, sid)) or True,
    )

    result = force_stop(client, t.id, local_machine="this-machine")

    assert calls == [("pool-host", "s1")]
    assert result["session_stopped"] is True
    assert q.get(t.id).status == Status.SUSPENDED


def test_force_stop_suspends_even_when_no_session_captured(q, client):
    """A task with no captured owner_session_id (e.g. a headless body whose
    bind_owner_session hasn't run yet) still gets suspended -- the operator's
    intent (stop treating it as actively running) is honored regardless."""
    t = q.create("work", repo="r")
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")  # no owner_session_id

    result = force_stop(client, t.id, local_machine="m")

    assert result["session"] is None
    assert result["session_stopped"] is None  # nothing attempted
    assert q.get(t.id).status == Status.SUSPENDED


def test_force_stop_rejects_claimed_not_yet_started(q, client):
    t = q.create("work", repo="r")
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    with pytest.raises(ForceStopError, match="requires started"):
        force_stop(client, t.id)
    assert q.get(t.id).status == Status.CLAIMED  # untouched


def test_force_stop_rejects_queued_task(q, client):
    t = q.create("work", repo="r")
    with pytest.raises(ForceStopError, match="requires started"):
        force_stop(client, t.id)


def test_force_stop_still_suspends_when_session_termination_fails(
    q, client, monkeypatch
):
    """Best-effort termination: a failed/unconfirmed stop never blocks the
    state transition the operator actually asked for."""
    t = q.create("work", repo="r")
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt", owner_session_id="s1")

    monkeypatch.setattr("agent_dispatch.bridge.force_end_session", lambda sid: False)

    result = force_stop(client, t.id, local_machine="m")
    assert result["session_stopped"] is False
    assert q.get(t.id).status == Status.SUSPENDED
