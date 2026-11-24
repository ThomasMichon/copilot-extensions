"""Empty wake polling must not contend with ordinary task production."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_dispatch import queue as queue_module
from agent_dispatch.queue import TaskQueue
from tests._helpers import RepoDefaultingQueue


@pytest.mark.parametrize("operation", ["recover", "claim"])
def test_empty_wake_poll_does_not_wait_for_unrelated_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    monkeypatch.setattr(queue_module, "_BUSY_TIMEOUT_MS", 100)
    with queue._connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        if operation == "recover":
            assert queue.recover_inflight_wakes(now=1000.0) == 0
        else:
            assert queue.claim_due_wake(now=1000.0) is None
        assert writer.in_transaction


def test_empty_observations_do_not_hide_a_later_wake(tmp_path: Path) -> None:
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    assert queue.recover_inflight_wakes(now=1000.0) == 0
    assert queue.claim_due_wake(now=1000.0) is None

    owner = "host-a/workspace-a"
    task = queue.create("fixture")
    queue.claim_one(owner, task_id=task.id)
    queue.start(task.id, owner, owner_session_id="session-a")
    queue.suspend(task.id, owner, reason="fixture")
    queue.resume(task.id, owner, wake_requested=True, now=1000.0)
    [original] = queue.list_wakes(task.id)

    claimed = queue.claim_due_wake(now=1000.0)
    assert claimed is not None
    assert claimed.id == original.id
    assert queue.recover_inflight_wakes(now=1059.0) == 0
    assert queue.recover_inflight_wakes(now=1060.0) == 1
    recovered = queue.claim_due_wake(now=1060.0)
    assert recovered is not None
    assert recovered.id == original.id
    assert recovered.attempts == 2
