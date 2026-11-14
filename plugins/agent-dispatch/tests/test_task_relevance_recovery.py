"""Generation-changing recovery must not strand an accepted owner closeout."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_task_relevance import report, task


@pytest.mark.parametrize("operation", ["detach_agent", "reset_proposed", "reset_queued"])
def test_pending_closeout_refuses_generation_changing_recovery(
    tmp_path: Path,
    operation: str,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    original = task(queue)
    queue.claim_one("host/worker", task_id=original.id)
    accepted = report(queue, original.id)
    before = queue.get(original.id), queue.events(original.id), queue.steer_log(original.id)
    diagnostics = queue.task_diagnostics(original.id)

    with pytest.raises(TaskError, match="pending relevance closeout"):
        queue.recover_task(
            original.id,
            operation=operation,
            expected_revision=diagnostics["revision"],
            request_id="recovery",
            actor="operator",
            reason="recover original worker",
        )

    assert not diagnostics["actions"][operation]["allowed"]
    assert any(
        "pending relevance closeout" in reason
        for reason in diagnostics["actions"][operation]["reasons"]
    )
    assert (
        queue.get(original.id),
        queue.events(original.id),
        queue.steer_log(original.id),
    ) == before
    restarted = TaskQueue(queue.db_path)
    assert restarted.relevance_receipt(original.id, "closed-source")["acceptance"] == accepted
    assert (
        restarted.relevance_snapshot(original.id)["closeout"]["generation"] == before[0].generation
    )
    assert restarted.claim_one("host/replacement", task_id=original.id) is None


@pytest.mark.parametrize("operation", ["detach_agent", "reset_proposed", "reset_queued"])
def test_relevance_acceptance_invalidates_prior_recovery_diagnostics(
    tmp_path: Path,
    operation: str,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    original = task(queue)
    queue.claim_one("host/worker", task_id=original.id)
    diagnostics = queue.task_diagnostics(original.id)
    assert diagnostics["actions"][operation]["allowed"]
    report(queue, original.id)
    before = queue.get(original.id), queue.events(original.id), queue.steer_log(original.id)

    with pytest.raises(TaskError, match="changed; refresh diagnostics"):
        queue.recover_task(
            original.id,
            operation=operation,
            expected_revision=diagnostics["revision"],
            request_id="recovery",
            actor="operator",
            reason="recover original worker",
        )

    assert (
        queue.get(original.id),
        queue.events(original.id),
        queue.steer_log(original.id),
    ) == before


def test_committed_recovery_replay_does_not_repeat_after_later_closeout(
    tmp_path: Path,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    original = task(queue)
    queue.claim_one("host/worker", task_id=original.id)
    arguments = {
        "operation": "reset_queued",
        "expected_revision": queue.task_diagnostics(original.id)["revision"],
        "request_id": "original-recovery",
        "actor": "operator",
        "reason": "recover original worker",
    }
    recovered = queue.recover_task(original.id, **arguments)
    queue.claim_one("host/current-worker", task_id=original.id)
    report(queue, original.id)
    before = queue.get(original.id), queue.events(original.id), queue.steer_log(original.id)
    restarted = TaskQueue(queue.db_path)

    assert restarted.recover_task(original.id, **arguments) == {**recovered, "replayed": True}
    assert (
        queue.get(original.id),
        queue.events(original.id),
        queue.steer_log(original.id),
    ) == before
