"""Tests for the agent-dispatch queue engine."""

from __future__ import annotations

import concurrent.futures
import threading

import pytest

from agent_dispatch.queue import (
    DEFAULT_LEASE_SECONDS,
    Status,
    TaskError,
    TaskQueue,
    worker_id_for,
)


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- basic lifecycle ---------------------------------------------------------


def test_create_defaults_to_queued(q):
    t = q.create("do a thing", prompt="go")
    assert t.status == Status.QUEUED
    assert t.title == "do a thing"
    assert t.prompt == "go"
    assert t.attempts == 0


def test_full_happy_path(q):
    t = q.create("work")
    claimed = q.claim_one("w1")
    assert claimed is not None
    assert claimed.id == t.id
    assert claimed.status == Status.CLAIMED
    assert claimed.owner == "w1"
    assert claimed.attempts == 1
    started = q.start(t.id, "w1")
    assert started.status == Status.STARTED
    done = q.complete(t.id, "w1", result_ref="pr/42")
    assert done.status == Status.COMPLETED
    assert done.result_ref == "pr/42"
    assert done.owner is None


# -- proposed is not claimable ----------------------------------------------


def test_proposed_is_not_claimable(q):
    p = q.propose("draft idea")
    assert p.status == Status.PROPOSED
    assert q.claim_one("w1") is None
    approved = q.approve(p.id)
    assert approved.status == Status.QUEUED
    assert q.claim_one("w1") is not None


# -- atomic claim race -------------------------------------------------------


def test_concurrent_claim_single_winner(q):
    q.create("only one")
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        return q.claim_one(f"w{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(8)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].status == Status.CLAIMED


def test_two_queued_two_workers_no_double_claim(q):
    a = q.create("a")
    b = q.create("b")
    r1 = q.claim_one("w1")
    r2 = q.claim_one("w2")
    assert {r1.id, r2.id} == {a.id, b.id}
    assert q.claim_one("w3") is None


# -- lease expiry / recovery -------------------------------------------------


def test_lease_expiry_requeues(q):
    t = q.create("leased")
    q.claim_one("w1", now=1000.0, lease_seconds=60)
    assert q.recover_expired_leases(now=1030.0) == 0  # not yet expired
    recovered = q.recover_expired_leases(now=2000.0)
    assert recovered == 1
    back = q.get(t.id)
    assert back.status == Status.QUEUED
    assert back.owner is None
    # a second worker can now reclaim it
    assert q.claim_one("w2", now=2001.0).owner == "w2"


def test_cooperative_redundancy_after_worker_death(q):
    """A capable second worker reclaims a dead worker's task after lease expiry."""
    q.create("review", requires=["review"])
    first = q.claim_one("w1", capabilities=["review"], now=1000.0, lease_seconds=60)
    assert first is not None and first.owner == "w1"
    # w1 "dies"; nobody else can claim while the lease holds
    assert q.claim_one("w2", capabilities=["review"], now=1010.0) is None
    q.recover_expired_leases(now=2000.0)
    second = q.claim_one("w2", capabilities=["review"], now=2001.0)
    assert second is not None and second.owner == "w2"


def test_heartbeat_extends_lease(q):
    t = q.create("long")
    q.claim_one("w1", now=1000.0, lease_seconds=60)
    q.heartbeat(t.id, "w1", now=1050.0)
    # lease was extended to 1050 + DEFAULT_LEASE_SECONDS, so still held at 1100
    assert q.recover_expired_leases(now=1100.0) == 0
    assert q.get(t.id).lease_expires_at == pytest.approx(1050.0 + DEFAULT_LEASE_SECONDS)


def test_heartbeat_wrong_owner_rejected(q):
    t = q.create("x")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.heartbeat(t.id, "w2")


# -- capability gating -------------------------------------------------------


def test_requires_gates_claim(q):
    q.create("logging", requires=["logger"])
    assert q.claim_one("plain") is None
    assert q.claim_one("plain", capabilities=["logger"]) is not None


def test_identity_pin_via_requires(q):
    q.create("review", requires=["agent:review-bot"])
    assert q.claim_one("random", capabilities=["review"]) is None
    got = q.claim_one("review-bot", capabilities=["agent:review-bot"])
    assert got is not None


def test_affinity_orders_but_does_not_exclude(q):
    generic = q.create("generic")
    preferred = q.create("preferred", affinity={"agent": "w1"})
    # w1 prefers the affinity task even though the generic one is older
    got = q.claim_one("w1")
    assert got.id == preferred.id
    # a different worker still gets the remaining task (affinity never excludes)
    other = q.claim_one("w2")
    assert other.id == generic.id


# -- not_before scheduling ---------------------------------------------------


def test_not_before_defers_claim(q):
    q.create("later", not_before=5000.0)
    assert q.claim_one("w1", now=4000.0) is None
    assert q.claim_one("w1", now=5001.0) is not None


# -- dedup -------------------------------------------------------------------


def test_dedup_key_prevents_duplicate(q):
    a = q.create("dup", dedup_key="k1")
    b = q.create("dup again", dedup_key="k1")
    assert a.id == b.id
    assert len(q.list()) == 1


# -- yield / abandon ---------------------------------------------------------


def test_yield_returns_to_queued_with_updates(q):
    t = q.create("conflict")
    q.claim_one("w1")
    q.start(t.id, "w1")
    y = q.yield_task(t.id, "w1", note="merge conflict")
    assert y.status == Status.QUEUED
    assert y.owner is None
    assert q.claim_one("w2") is not None


def test_abandon_requires_permission(q):
    t = q.create("bad")
    with pytest.raises(TaskError):
        q.abandon(t.id)
    done = q.abandon(t.id, permitted=True, reason="duplicate")
    assert done.status == Status.ABANDONED


def test_terminal_states_reject_transitions(q):
    t = q.create("x")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.complete(t.id, "w1")
    with pytest.raises(TaskError):
        q.start(t.id, "w1")


def test_start_wrong_owner_rejected(q):
    t = q.create("x")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.start(t.id, "w2")


# -- detach (worktree portability) ------------------------------------------


def test_detach_demotes_hard_worktree_pin(q):
    t = q.create("handoff", requires=["worktree:wt-1"], target_worktree="wt-1")
    d = q.detach(t.id)
    assert "worktree:wt-1" not in d.requires
    assert d.affinity.get("worktree") == "wt-1"
    # now claimable by any worker (pin demoted to a soft preference)
    assert q.claim_one("anyone") is not None


# -- migration idempotency ---------------------------------------------------


def test_reopen_existing_db_is_idempotent(tmp_path):
    db = tmp_path / "tasks.db"
    q1 = TaskQueue(db)
    t = q1.create("persist")
    q2 = TaskQueue(db)  # re-run migrations on an existing DB
    assert q2.get(t.id).title == "persist"


# -- audit trail -------------------------------------------------------------


def test_events_record_transitions(q):
    t = q.create("audited")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.complete(t.id, "w1")
    trail = [e["to_status"] for e in q.events(t.id)]
    assert trail == [Status.QUEUED, Status.CLAIMED, Status.STARTED, Status.COMPLETED]


# -- worker identity + targeting-in-claim ------------------------------------


def test_worker_id_for():
    assert worker_id_for("host-a", "wt-1") == "host-a/wt-1"


def test_claim_gated_by_target_machine(q):
    q.create("m1-only", target_machine="m1")
    assert q.claim_one("a", machine="m2", worktree="w") is None
    got = q.claim_one("a", machine="m1", worktree="w")
    assert got is not None and got.target_machine == "m1"


def test_claim_gated_by_target_worktree(q):
    q.create("wtX-only", target_worktree="wtX")
    assert q.claim_one("a", machine="m", worktree="other") is None
    assert q.claim_one("a", machine="m", worktree="wtX") is not None


def test_untargeted_task_claimable_by_any_identity(q):
    q.create("open")
    assert q.claim_one("a", machine="m", worktree="w") is not None


def test_machineless_claimer_gets_only_untargeted(q):
    q.create("targeted", target_machine="m1")
    q.create("open")
    got = q.claim_one("a")  # no machine/worktree declared
    assert got.title == "open"


def test_claim_stamps_composite_owner(q):
    t = q.create("x")
    owner = worker_id_for("host-a", "wt-9")
    got = q.claim_one(owner, machine="host-a", worktree="wt-9", task_id=t.id)
    assert got.owner == "host-a/wt-9"


def test_mine_returns_assigned_and_owned(q):
    assigned = q.create("for-wt1", target_worktree="wt-1")
    machine_wide = q.create("for-machine", target_machine="host-a")
    to_own = q.create("to-own")
    q.claim_one(
        worker_id_for("host-a", "wt-1"),
        machine="host-a",
        worktree="wt-1",
        task_id=to_own.id,
    )
    q.create("open-to-all")  # untargeted -- not "assigned to me"

    inbox = q.mine("host-a", "wt-1")
    assigned_ids = {t.id for t in inbox["assigned"]}
    owned_ids = {t.id for t in inbox["owned"]}
    assert assigned.id in assigned_ids
    assert machine_wide.id in assigned_ids  # machine-wide, no worktree pin
    assert to_own.id in owned_ids
    assert all(t.title != "open-to-all" for t in inbox["assigned"])
