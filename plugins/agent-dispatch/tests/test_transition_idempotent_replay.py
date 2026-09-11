"""Phase 10 (``review-automation-reliability`` effort) item 2 tests:
prove the narrow idempotent-replay no-op ``TaskQueue._transition`` now
implements for ``approve``, ``start``, ``resume`` (plain, non-adoption
path), ``release_suspended``, ``abandon``, and ``yield_task``.

Per the approved design: a request that finds the task **already sitting
in the exact target state** under matching identity fences (owner, and
generation/owner-session when the caller supplied ``expected_generation``)
is a no-op that returns the current task rather than raising -- but any
other mismatch (wrong owner, wrong state entirely, a fenced generation
that no longer matches) still raises exactly as before. This closes the
gap `machine_coupling.py` declared (idempotent replay is a no-op, not an
error) for the methods that previously always raised on replay; `suspend`
and `complete_with_outcome` already had their own hand-written versions of
this pattern before this change.
"""

from __future__ import annotations

import pytest

from agent_dispatch.queue import Status, TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_approve_replay_is_a_noop_not_an_error(q):
    task = q.create("t", status=Status.PROPOSED)
    first = q.approve(task.id)
    assert first.status == Status.QUEUED
    replayed = q.approve(task.id)
    assert replayed.status == Status.QUEUED
    assert replayed.id == first.id


def test_approve_on_a_genuinely_wrong_state_still_raises(q):
    """A task already past QUEUED (e.g. claimed) is not the ``approve``
    target state -- this must still raise, not silently succeed."""
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    q.claim_one("worker-1")
    with pytest.raises(TaskError):
        q.approve(task.id)


def test_start_replay_by_the_same_worker_is_a_noop(q):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    first = q.start(claimed.id, "worker-1")
    replayed = q.start(claimed.id, "worker-1")
    assert replayed.status == Status.STARTED == first.status


def test_start_replay_by_a_different_worker_still_raises(q):
    """The exact-state match alone is not enough -- the owner fence must
    still hold for the no-op to apply."""
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    with pytest.raises(TaskError):
        q.start(claimed.id, "worker-2")


def test_resume_replay_without_adoption_is_a_noop(q):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    first = q.resume(claimed.id, "worker-1")
    replayed = q.resume(claimed.id, "worker-1")
    assert replayed.status == Status.STARTED == first.status


def test_resume_with_adoption_never_no_ops_even_on_replay(q):
    """A handoff-adoption resume (``adopt_owner_session_id`` set) must
    always bump the generation and adopt the new session -- disabling the
    idempotent no-op path is deliberate, since a silent no-op would drop
    that real effect. Modeled as two separate handoffs (each suspended in
    between, as a real successor sequence would be), not a bare replay of
    the exact same call -- an adoption resume replayed on an already-
    STARTED task is expected to raise, matching today's behavior."""
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    first = q.resume(claimed.id, "worker-1", adopt_owner_session_id="session-a")
    q.suspend(claimed.id, "worker-1", reason="pausing again")
    second = q.resume(claimed.id, "worker-1", adopt_owner_session_id="session-b")
    # Each adoption call genuinely re-applies (bumps generation, adopts
    # the new session) -- the second call must not be silently absorbed
    # as an idempotent replay of the first.
    assert second.generation > first.generation
    assert second.owner_session_id == "session-b"


def test_resume_with_adoption_raises_rather_than_no_ops_on_a_bare_replay(q):
    """The specific behavior the disabled idempotent path preserves: an
    adoption resume attempted again on an already-STARTED task (no
    intervening suspend) raises, exactly as before this change -- it is
    not silently absorbed as "already done"."""
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    q.resume(claimed.id, "worker-1", adopt_owner_session_id="session-a")
    with pytest.raises(TaskError):
        q.resume(claimed.id, "worker-1", adopt_owner_session_id="session-b")


def test_release_suspended_replay_is_a_noop(q):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    first = q.release_suspended(claimed.id, "worker-1")
    replayed = q.release_suspended(claimed.id, "worker-1")
    assert replayed.status == Status.QUEUED == first.status


def test_abandon_replay_is_a_noop(q):
    task = q.create("t", status=Status.PROPOSED)
    first = q.abandon(task.id, permitted=True)
    assert first.status == Status.ABANDONED
    replayed = q.abandon(task.id, permitted=True)
    assert replayed.status == Status.ABANDONED == first.status


def test_yield_task_replay_is_a_noop(q):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    first = q.yield_task(claimed.id, "worker-1")
    assert first.status == Status.QUEUED
    replayed = q.yield_task(claimed.id, "worker-1")
    assert replayed.status == Status.QUEUED == first.status
