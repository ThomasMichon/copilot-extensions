"""Tests for task selector matching — arbitrary include/exclude + reject-appends-exclusion.

Covers the anti-affinity (`excludes`) selector, identity-token folding (so a
selector can target/exclude by machine/worktree/repo), and the reject-appends-a-
scoped-"not me" flow with its monotonic convergence to unclaimable.
"""

from __future__ import annotations

import pytest

from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- excludes / anti-affinity ------------------------------------------------


def test_exclude_by_capability_blocks_matching_worker(q):
    t = q.create("work", excludes=["gpu"])
    # a worker advertising the excluded capability is ineligible
    assert q.claim_one("w1", capabilities=["gpu"], machine="m", worktree="wt") is None
    # a worker without it claims fine
    got = q.claim_one("w2", capabilities=["cpu"], machine="m2", worktree="wt2")
    assert got is not None and got.id == t.id


def test_exclude_by_machine_identity(q):
    q.create("work", excludes=["machine:lambda-core"])
    assert q.claim_one("lambda-core/wt", machine="lambda-core", worktree="wt") is None
    assert q.claim_one("borealis/wt", machine="borealis", worktree="wt") is not None


def test_exclude_by_worktree_identity(q):
    q.create("work", excludes=["worktree:foo"])
    assert q.claim_one("m/foo", machine="m", worktree="foo") is None
    assert q.claim_one("m/bar", machine="m", worktree="bar") is not None


def test_require_identity_token_via_folded_caps(q):
    # includes generalize the same way: a task requiring machine:borealis is
    # claimable only by that machine (identity folded into the advertised set)
    q.create("work", requires=["machine:borealis"])
    assert q.claim_one("lambda-core/wt", machine="lambda-core", worktree="wt") is None
    assert q.claim_one("borealis/wt", machine="borealis", worktree="wt") is not None


def test_excludes_round_trip_on_task(q):
    t = q.create("work", excludes=["machine:x", "agent:reviewer"])
    assert q.get(t.id).excludes == ["machine:x", "agent:reviewer"]


def test_existing_capability_requires_unaffected_by_folding(q):
    # a plain capability requirement still works exactly as before
    q.create("work", requires=["gpu"])
    assert q.claim_one("m/wt", capabilities=[], machine="m", worktree="wt") is None
    assert q.claim_one("m/wt", capabilities=["gpu"], machine="m", worktree="wt") is not None


# -- reject-appends-exclusion ("not me") -------------------------------------


def test_yield_appends_worktree_scoped_not_me(q):
    t = q.create("work")
    q.claim_one("borealis/wt", machine="borealis", worktree="wt", task_id=t.id)
    q.yield_task(t.id, "borealis/wt", exclude="worktree:wt")
    got = q.get(t.id)
    assert got.status == Status.QUEUED
    assert "worktree:wt" in got.excludes
    # the same worktree can't re-claim; a different worktree still can
    assert q.claim_one(
        "borealis/wt", machine="borealis", worktree="wt", task_id=t.id
    ) is None
    assert q.claim_one(
        "borealis/other", machine="borealis", worktree="other", task_id=t.id
    ) is not None


def test_yield_exclude_is_idempotent(q):
    t = q.create("work", excludes=["worktree:wt"])
    q.claim_one("m/other", machine="m", worktree="other", task_id=t.id)
    q.yield_task(t.id, "m/other", exclude="worktree:wt")  # already present
    assert q.get(t.id).excludes.count("worktree:wt") == 1


def test_machine_scoped_not_me_excludes_all_its_worktrees(q):
    t = q.create("work")
    q.claim_one("lambda-core/a", machine="lambda-core", worktree="a", task_id=t.id)
    q.yield_task(t.id, "lambda-core/a", exclude="machine:lambda-core")
    assert q.claim_one("lambda-core/a", machine="lambda-core", worktree="a", task_id=t.id) is None
    assert q.claim_one("lambda-core/b", machine="lambda-core", worktree="b", task_id=t.id) is None
    assert q.claim_one("borealis/a", machine="borealis", worktree="a", task_id=t.id) is not None


def test_monotonic_exclusion_converges_to_unclaimable(q):
    """Each decline only grows the exclusion set, so a task with two candidate
    machines that both decline becomes unclaimable (dead-letter signal)."""
    t = q.create("work")
    q.claim_one("m1/wt", machine="m1", worktree="wt", task_id=t.id)
    q.yield_task(t.id, "m1/wt", exclude="machine:m1")
    q.claim_one("m2/wt", machine="m2", worktree="wt", task_id=t.id)
    q.yield_task(t.id, "m2/wt", exclude="machine:m2")
    assert q.get(t.id).excludes == ["machine:m1", "machine:m2"]
    assert q.claim_one("m1/wt", machine="m1", worktree="wt", task_id=t.id) is None
    assert q.claim_one("m2/wt", machine="m2", worktree="wt", task_id=t.id) is None


def test_yield_without_exclude_leaves_excludes_untouched(q):
    t = q.create("work", excludes=["machine:x"])
    q.claim_one("m/wt", machine="m", worktree="wt", task_id=t.id)
    q.yield_task(t.id, "m/wt")  # no exclude
    assert q.get(t.id).excludes == ["machine:x"]
