from __future__ import annotations

import pytest

from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db", lease_seconds=900, eval_lease_seconds=120)


def test_default_claim_uses_full_work_lease(q):
    t = q.create("work")
    q.claim_one("w/1", machine="w", worktree="1", task_id=t.id, now=1000.0)
    assert q.get(t.id).lease_expires_at == pytest.approx(1000.0 + 900)


def test_evaluation_claim_uses_tight_lease(q):
    t = q.create("work")
    q.claim_one("w/1", machine="w", worktree="1", task_id=t.id, now=1000.0, evaluation=True)
    assert q.get(t.id).lease_expires_at == pytest.approx(1000.0 + 120)


def test_explicit_lease_overrides_evaluation(q):
    t = q.create("work")
    q.claim_one(
        "w/1", machine="w", worktree="1", task_id=t.id,
        now=1000.0, evaluation=True, lease_seconds=30,
    )
    assert q.get(t.id).lease_expires_at == pytest.approx(1000.0 + 30)


def test_start_extends_eval_claim_to_work_lease(q):
    t = q.create("work")
    q.claim_one("w/1", machine="w", worktree="1", task_id=t.id, now=1000.0, evaluation=True)
    q.start(t.id, "w/1", now=1050.0)
    task = q.get(t.id)
    assert task.status == Status.STARTED
    assert task.lease_expires_at == pytest.approx(1050.0 + 900)


def test_expired_eval_lease_is_recovered(q):
    t = q.create("work")
    q.claim_one("w/1", machine="w", worktree="1", task_id=t.id, now=1000.0, evaluation=True)
    recovered = q.recover_expired_leases(now=1000.0 + 120 + 1)
    assert recovered == 1
    assert q.get(t.id).status == Status.QUEUED
