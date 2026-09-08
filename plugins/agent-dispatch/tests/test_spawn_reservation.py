"""Tests for the spawn-reservation primitive.

The spawn reservation is the atomic "exactly one embody spawn per (task,
attempt)" record that closes the gap between the queue's transactional claim and
the non-transactional CLI-side spawn -- so ``create --spawn`` (and, later, the
supervisor loop) can never double-spawn an autonomous worker.
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
import types

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as m
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import SpawnState, TaskError, spawn_key
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- queue-level semantics ---------------------------------------------------


def test_reserve_is_idempotent_while_active(q):
    t = q.create("work")
    r1, ok1 = q.reserve_spawn(t.id, reserved_by="cli")
    assert ok1 is True
    assert r1.state == SpawnState.RESERVING
    assert r1.key == spawn_key(t.id, 1)

    # A second reservation while the first is active does NOT create a new one.
    r2, ok2 = q.reserve_spawn(t.id, reserved_by="cli")
    assert ok2 is False
    assert r2.key == r1.key


def test_spawned_still_blocks_a_second_reservation(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    rec = q.record_spawn(r1.key, session_handle="sess-1", worktree="wt-1")
    assert rec.state == SpawnState.SPAWNED
    assert rec.session_handle == "sess-1"
    assert rec.worktree == "wt-1"

    _, ok = q.reserve_spawn(t.id)
    assert ok is False  # 'spawned' is still an active owner of the spawn


def test_active_reservation_remains_idempotent_after_task_claim(q):
    t = q.create("work")
    first, _ = q.reserve_spawn(t.id)
    q.record_spawn(first.key, session_handle="sess-1", worktree="wt-1")
    q.claim_one("m/wt-1", task_id=t.id)

    existing, reserved = q.reserve_spawn(t.id)

    assert reserved is False
    assert existing.key == first.key


def test_settle_releases_for_a_fresh_attempt(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    q.record_spawn(r1.key)
    q.settle_spawn(r1.key)

    r2, ok = q.reserve_spawn(t.id)
    assert ok is True
    assert r2.attempt == 2
    assert r2.key == spawn_key(t.id, 2)


def test_settle_is_idempotent_and_can_record_late_detail(q):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(reservation.key)

    q.settle_spawn(reservation.key, detail="task completed")
    updated = q.settle_spawn(
        reservation.key,
        detail="task completed; terminal conclusion primed",
        conclusion_state="complete",
        conclusion_detail='{"action":"primed"}',
    )

    assert updated.state == SpawnState.SETTLED
    assert updated.detail == "task completed; terminal conclusion primed"
    assert updated.conclusion_state == "complete"
    assert updated.conclusion_detail == '{"action":"primed"}'


def test_fail_releases_for_a_fresh_attempt(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    failed = q.fail_spawn(r1.key, detail="boom")
    assert failed.state == SpawnState.FAILED
    assert failed.detail == "boom"

    r2, ok = q.reserve_spawn(t.id)
    assert ok is True
    assert r2.attempt == 2


def test_defer_releases_for_a_fresh_attempt_without_counting_as_failed(q):
    """Deferring (a carried session confirmed live/busy, #2056) releases the
    task for a fresh attempt exactly like `fail_spawn`, but the reservation
    lands `deferred`, not `failed` -- so it is invisible to any dead-letter
    count keyed on FAILED."""
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    deferred = q.defer_spawn(r1.key, detail="carried session remains live")
    assert deferred.state == SpawnState.DEFERRED
    assert deferred.detail == "carried session remains live"

    r2, ok = q.reserve_spawn(t.id)
    assert ok is True
    assert r2.attempt == 2
    assert q.list_reservations(task_id=t.id, state=SpawnState.FAILED) == []


def test_bad_transitions_raise(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    q.settle_spawn(r1.key)
    # settled is terminal -- cannot fail it again
    with pytest.raises(TaskError):
        q.fail_spawn(r1.key)
    # nor deferred
    with pytest.raises(TaskError):
        q.defer_spawn(r1.key)
    # unknown key
    with pytest.raises(TaskError):
        q.record_spawn("dispatch-task:nope:1")


def test_list_and_latest_reservations(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    q.fail_spawn(r1.key)
    r2, _ = q.reserve_spawn(t.id)

    assert q.latest_reservation(t.id).key == r2.key
    all_res = q.list_reservations(task_id=t.id)
    assert {r.attempt for r in all_res} == {1, 2}
    reserving = q.list_reservations(state=SpawnState.RESERVING)
    assert [r.key for r in reserving] == [r2.key]


def test_reserve_is_atomic_under_concurrency(q):
    """Many racing reservers on one task -> exactly one wins."""
    t = q.create("work")
    barrier = threading.Barrier(16)

    def race():
        barrier.wait()
        _, ok = q.reserve_spawn(t.id)
        return ok

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        wins = list(pool.map(lambda _: race(), range(16)))

    assert sum(1 for w in wins if w) == 1
    # exactly one reservation row exists
    assert len(q.list_reservations(task_id=t.id)) == 1


def test_exclusive_reserve_is_atomic_across_task_ids(q):
    """Racing task episodes for one resource still produce one active owner."""
    tasks = [
        q.create(f"review {index}", exclusive_key="review:repo:42")
        for index in range(16)
    ]
    barrier = threading.Barrier(len(tasks))

    def race(task_id):
        barrier.wait()
        reservation, won = q.reserve_spawn(task_id)
        return reservation, won

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        outcomes = list(pool.map(lambda task: race(task.id), tasks))

    assert sum(1 for _reservation, won in outcomes if won) == 1
    active = q.list_reservations(state=SpawnState.ACTIVE)
    assert len(active) == 1
    assert {reservation.key for reservation, _won in outcomes} == {
        active[0].key
    }


def test_exclusive_key_blocks_second_task_spawn(q):
    """Two head-specific tasks sharing a resource key cannot both spawn."""
    t1 = q.create("review old", exclusive_key="review:repo:42")
    t2 = q.create("review new", exclusive_key="review:repo:42")

    r1, ok1 = q.reserve_spawn(t1.id)
    r2, ok2 = q.reserve_spawn(t2.id)

    assert ok1 is True
    assert ok2 is False
    assert r2.key == r1.key
    assert r2.task_id == t1.id
    assert len(q.list_reservations(state=SpawnState.ACTIVE)) == 1


def test_exclusive_key_reuses_prior_worktree_after_settle(q):
    t1 = q.create("review old", exclusive_key="review:repo:42")
    r1, _ = q.reserve_spawn(t1.id)
    q.record_spawn(r1.key, session_handle="sess-old", worktree="wt-reviewer")
    q.settle_spawn(r1.key)

    t2 = q.create("review new", exclusive_key="review:repo:42")
    r2, ok2 = q.reserve_spawn(t2.id)

    assert ok2 is True
    assert r2.task_id == t2.id
    assert r2.worktree == "wt-reviewer"
    assert r2.worktree_ownership == "reused"
    assert r2.exclusive_key == "review:repo:42"


def test_exclusive_key_reuses_worktree_without_retired_session(q):
    t1 = q.create("review old", exclusive_key="review:repo:42")
    r1, _ = q.reserve_spawn(t1.id)
    q.record_spawn(
        r1.key,
        session_handle="local-body:session-retired",
        worktree="wt-reviewer",
    )
    q.request_spawn_release(r1.key)
    q.retire_spawn(
        r1.key,
        exact_absence=True,
        conclusion_state="complete",
        conclusion_detail='{"action":"preserved","reason":"test-cleanup-complete"}',
    )

    t2 = q.create("review new", exclusive_key="review:repo:42")
    r2, ok2 = q.reserve_spawn(t2.id)

    assert ok2 is True
    assert r2.worktree == "wt-reviewer"
    assert r2.inherited_worktree == "wt-reviewer"
    assert r2.worktree_ownership == "reused"
    assert r2.session_handle is None
    assert r2.exclusive_key == "review:repo:42"
    updated = q.record_spawn_worktree(
        r2.key,
        "wt-replacement",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    assert updated.worktree == "wt-replacement"
    assert updated.inherited_worktree == "wt-reviewer"


def test_migration_backfills_reused_worktree_lineage(q):
    task = q.create("review", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree = ?, "
            "worktree_ownership = 'reused', inherited_worktree = NULL "
            "WHERE key = ?",
            ("wt-inherited", reservation.key),
        )

    q._migrate()

    migrated = q.get_reservation(reservation.key)
    assert migrated.inherited_worktree == "wt-inherited"


def test_legacy_spawn_schema_adds_ownership_before_lineage_backfill(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE spawn_reservations ("
            "key TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
            "attempt INTEGER NOT NULL, state TEXT NOT NULL, "
            "reserved_by TEXT, session_handle TEXT, worktree TEXT, "
            "detail TEXT, reserved_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO spawn_reservations "
            "(key, task_id, attempt, state, worktree, reserved_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("dispatch-task:legacy:1", "legacy", 1, "failed", "wt-legacy", 1, 1),
        )

    migrated = TaskQueue(db)

    with migrated._connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(spawn_reservations)"
            ).fetchall()
        }
    assert {"worktree_ownership", "inherited_worktree"} <= columns
    assert migrated.get_reservation("dispatch-task:legacy:1") is not None


def test_retired_session_is_not_recarried_from_legacy_failed_attempt(q):
    task = q.create("review", exclusive_key="review:repo:42")
    retired, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        retired.key,
        session_handle="local-body:session-retired",
        worktree="wt-reviewer",
    )
    q.request_spawn_release(retired.key)
    q.retire_spawn(
        retired.key,
        exact_absence=True,
        conclusion_state="complete",
        conclusion_detail='{"action":"preserved","reason":"test-cleanup-complete"}',
    )

    legacy, _ = q.reserve_spawn(task.id)
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET session_handle = ? WHERE key = ?",
            ("local-body:session-retired", legacy.key),
        )
    q.fail_spawn(legacy.key, detail="could not determine carried session liveness")

    fresh, reserved = q.reserve_spawn(task.id)

    assert reserved is True
    assert fresh.attempt == 3
    assert fresh.worktree == "wt-reviewer"
    assert fresh.worktree_ownership == "reused"
    assert fresh.session_handle is None
    assert fresh.exclusive_key == "review:repo:42"


def test_exclusive_key_can_take_affinity_as_initial_resume_target(q):
    t = q.create(
        "review new",
        exclusive_key="review:repo:42",
        affinity={"worktree": "wt-recorded"},
    )

    reservation, reserved = q.reserve_spawn(t.id)

    assert reserved is True
    assert reservation.worktree == "wt-recorded"
    assert reservation.worktree_ownership == "targeted"


def test_record_spawn_worktree_does_not_mark_spawned(q):
    t = q.create("work")
    reservation, reserved = q.reserve_spawn(t.id)
    assert reserved is True

    updated = q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )

    assert updated.state == SpawnState.RESERVING
    assert updated.worktree == "wt-created"
    assert updated.worktree_ownership == "created"
    assert updated.creating_host == "host-a"
    assert updated.driver == "agent-dispatch"
    q.record_spawn(updated.key, session_handle="sess-created")
    final = q.get_reservation(updated.key)
    assert final.state == SpawnState.SPAWNED
    assert final.worktree == "wt-created"
    assert final.session_handle == "sess-created"


def test_record_replacement_worktree_clears_carried_session(q):
    first = q.create("old", exclusive_key="review:repo:42")
    prior, _ = q.reserve_spawn(first.id)
    q.record_spawn(
        prior.key,
        session_handle="local-body:old-session",
        worktree="wt-missing",
    )
    q.settle_spawn(prior.key)
    current = q.create("new", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(current.id)
    assert reservation.session_handle == "local-body:old-session"

    updated = q.record_spawn_worktree(reservation.key, "wt-fresh")

    assert updated.state == SpawnState.RESERVING
    assert updated.worktree == "wt-fresh"
    assert updated.session_handle is None


def test_yield_requests_release_without_settling_live_reservation(q):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:session-live",
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")

    yielded = q.yield_task(task.id, "fleet-owner")
    updated = q.get_reservation(reservation.key)

    assert yielded.status == "queued"
    assert updated.state == SpawnState.RELEASING
    assert updated.release_requested is True
    assert updated.release_disposition == "settled"
    assert updated.conclusion_state == "pending"


def test_equivalent_release_paths_enter_releasing_state(q):
    yielded = q.create("yielded")
    yielded_reservation, _ = q.reserve_spawn(yielded.id)
    q.record_spawn(yielded_reservation.key, session_handle="session-yielded")
    q.claim_one("worker-yielded", task_id=yielded.id)
    q.start(yielded.id, "worker-yielded")
    q.yield_task(yielded.id, "worker-yielded")

    explicit = q.create("explicit")
    explicit_reservation, _ = q.reserve_spawn(explicit.id)
    q.record_spawn(explicit_reservation.key, session_handle="session-explicit")
    q.request_spawn_release(
        explicit_reservation.key,
        disposition="settled",
    )

    released = q.get_reservation(yielded_reservation.key)
    requested = q.get_reservation(explicit_reservation.key)
    assert released.state == requested.state == SpawnState.RELEASING
    assert released.release_disposition == requested.release_disposition == "settled"
    assert released.conclusion_state == requested.conclusion_state == "pending"


def test_failed_created_spawn_stays_fenced_until_cleanup(q):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )

    releasing = q.request_spawn_release(
        reservation.key,
        detail="launch failed",
        disposition="failed",
    )
    blocked, acquired = q.reserve_spawn(task.id)

    assert releasing.state == SpawnState.RELEASING
    assert releasing.release_requested is True
    assert releasing.release_disposition == "failed"
    assert acquired is False
    assert blocked.key == reservation.key


def test_releasing_reservation_fences_same_exclusive_key(q):
    first = q.create("first", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(first.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(reservation.key, disposition="failed")
    second = q.create("second", exclusive_key="review:repo:42")

    blocked, acquired = q.reserve_spawn(second.id)

    assert acquired is False
    assert blocked.key == reservation.key
    assert blocked.state == SpawnState.RELEASING


def test_exclusive_index_replacement_is_transactional(q, monkeypatch):
    conn = sqlite3.connect(q.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    monkeypatch.setattr(q, "_connect", lambda: conn)

    try:
        q._migrate()
    finally:
        conn.close()

    normalized = [" ".join(statement.upper().split()) for statement in statements]
    begin = normalized.index("BEGIN IMMEDIATE")
    drop = normalized.index("DROP INDEX IF EXISTS IDX_SPAWN_RES_EXCLUSIVE_ACTIVE")
    create = next(
        index
        for index, statement in enumerate(normalized)
        if statement.startswith(
            "CREATE UNIQUE INDEX IF NOT EXISTS IDX_SPAWN_RES_EXCLUSIVE_ACTIVE"
        )
    )
    commit = normalized.index("COMMIT", create)
    assert begin < drop < create < commit


def test_repeated_release_preserves_held_conclusion_and_upgrades_failure(q):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.request_spawn_release(
        reservation.key,
        detail="yielded",
        disposition="settled",
    )
    q.record_spawn_conclusion(
        reservation.key,
        conclusion_state="held",
        conclusion_detail='{"action":"preserved","reason":"dirty-worktree"}',
    )

    repeated = q.request_spawn_release(
        reservation.key,
        detail="still held",
        disposition="settled",
    )
    assert repeated.state == SpawnState.RELEASING
    assert repeated.release_disposition == "settled"
    assert repeated.conclusion_state == "held"
    assert repeated.conclusion_detail == (
        '{"action":"preserved","reason":"dirty-worktree"}'
    )

    upgraded = q.request_spawn_release(
        reservation.key,
        detail="worker failed",
        disposition="failed",
    )
    assert upgraded.release_disposition == "failed"
    assert upgraded.conclusion_state == "held"
    assert upgraded.conclusion_detail == repeated.conclusion_detail


@pytest.mark.parametrize("transition", ["fail", "settle", "defer"])
def test_release_pending_reservation_rejects_direct_terminal_transition(
    q, transition
):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(reservation.key, session_handle="session-live")
    q.request_spawn_release(reservation.key, disposition="failed")

    with pytest.raises(TaskError):
        getattr(q, f"{transition}_spawn")(reservation.key)

    current = q.get_reservation(reservation.key)
    assert current.state == SpawnState.RELEASING
    assert current.conclusion_state == "pending"


def test_retire_requires_exact_absence_and_uses_release_disposition(q):
    failed_task = q.create("failed")
    failed, _ = q.reserve_spawn(failed_task.id)
    q.request_spawn_release(failed.key, disposition="failed")
    with pytest.raises(TaskError, match="exact absence proof"):
        q.retire_spawn(failed.key, exact_absence=False)
    retired_failed = q.retire_spawn(failed.key, exact_absence=True)
    assert retired_failed.state == SpawnState.FAILED

    settled_task = q.create("settled")
    settled, _ = q.reserve_spawn(settled_task.id)
    q.request_spawn_release(settled.key, disposition="settled")
    retired_settled = q.retire_spawn(settled.key, exact_absence=True)
    assert retired_settled.state == SpawnState.SETTLED


def test_claimed_cleanup_prevents_successor_worktree_reuse(q):
    old = q.create("old", exclusive_key="stable:resource")
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn_worktree(
        old_reservation.key,
        "wt-shared",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        old_reservation.key,
        disposition="failed",
    )
    q.retire_spawn(
        old_reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )

    claimed, acquired, claim_token = q.claim_spawn_conclusion_retry(
        old_reservation.key
    )

    assert acquired is True
    assert claim_token
    assert claimed.conclusion_state == "pending"
    assert json.loads(claimed.conclusion_detail or "{}")["reason"] == (
        "cleanup-retry-claimed"
    )
    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, reserved = q.reserve_spawn(successor.id)
    assert reserved is True
    assert successor_reservation.worktree is None
    assert successor_reservation.session_handle is None
    with pytest.raises(TaskError, match="in-flight cleanup"):
        q.record_spawn_worktree(
            successor_reservation.key,
            "wt-shared",
            ownership="targeted",
        )
    with pytest.raises(TaskError, match="cleanup claim expired"):
        q.record_spawn_conclusion(
            old_reservation.key,
            conclusion_state="complete",
            conclusion_detail='{"action":"removed"}',
            claim_token=claim_token,
            now=(claimed.cleanup_claim_expires_at or 0) + 1,
        )


def test_tokenless_pending_cleanup_prevents_worktree_reuse(q):
    old = q.create("old", exclusive_key="stable:resource")
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn_worktree(
        old_reservation.key,
        "wt-shared",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(old_reservation.key, disposition="failed")
    pending = q.retire_spawn(
        old_reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"not-yet-claimed"}',
    )
    assert pending.cleanup_claim_token is None

    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, reserved = q.reserve_spawn(successor.id)

    assert reserved is True
    assert successor_reservation.worktree is None
    assert successor_reservation.inherited_worktree is None
    with pytest.raises(TaskError, match="in-flight cleanup"):
        q.record_spawn_worktree(
            successor_reservation.key,
            "wt-shared",
            ownership="targeted",
        )


def test_cleanup_claim_expires_and_is_reclaimable(q):
    task = q.create("cleanup")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-cleanup",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(reservation.key, disposition="failed")
    q.retire_spawn(
        reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )
    with pytest.raises(TaskError, match="cleanup claim token is required"):
        q.record_spawn_conclusion(
            reservation.key,
            conclusion_state="complete",
            conclusion_detail='{"action":"removed"}',
            now=99,
        )

    first, claimed, first_token = q.claim_spawn_conclusion_retry(
        reservation.key,
        claim_seconds=10,
        now=100,
    )
    assert claimed is True
    assert first_token
    assert first.conclusion_state == "pending"

    still_claimed, claimed, token = q.claim_spawn_conclusion_retry(
        reservation.key,
        claim_seconds=10,
        now=105,
    )
    assert claimed is False
    assert token is None
    assert still_claimed.conclusion_state == "pending"
    with pytest.raises(TaskError, match="claim token is required"):
        q.record_spawn_conclusion(
            reservation.key,
            conclusion_state="pending",
            conclusion_detail='{"action":"pending","reason":"session-end"}',
            now=106,
        )
    unchanged = q.get_reservation(reservation.key)
    assert json.loads(unchanged.conclusion_detail or "{}")["claim_token"] == (
        first_token
    )
    with pytest.raises(TaskError, match="claim token is required"):
        q.record_spawn_conclusion(
            reservation.key,
            conclusion_state="pending",
            conclusion_detail='{"action":"pending","reason":"session-end"}',
            now=111,
        )

    reclaimed, claimed, second_token = q.claim_spawn_conclusion_retry(
        reservation.key,
        claim_seconds=10,
        now=111,
    )
    assert claimed is True
    assert second_token and second_token != first_token
    assert reclaimed.conclusion_state == "pending"

    with pytest.raises(TaskError, match="cleanup claim changed"):
        q.record_spawn_conclusion(
            reservation.key,
            conclusion_state="complete",
            conclusion_detail='{"action":"removed"}',
            claim_token=first_token,
            now=112,
        )
    completed = q.record_spawn_conclusion(
        reservation.key,
        conclusion_state="complete",
        conclusion_detail='{"action":"removed"}',
        claim_token=second_token,
        now=112,
    )
    assert completed.conclusion_state == "complete"


def test_expired_claim_still_fences_reuse_and_rejects_old_result(q):
    old = q.create("old", exclusive_key="stable:resource")
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn_worktree(
        old_reservation.key,
        "wt-shared",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(old_reservation.key, disposition="failed")
    q.retire_spawn(
        old_reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )
    claimed, acquired, old_token = q.claim_spawn_conclusion_retry(
        old_reservation.key,
        claim_seconds=10,
        now=100,
    )
    assert acquired is True

    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, reserved = q.reserve_spawn(successor.id, now=111)

    assert reserved is True
    assert successor_reservation.worktree is None
    with pytest.raises(TaskError, match="in-flight cleanup"):
        q.record_spawn_worktree(
            successor_reservation.key,
            "wt-shared",
            ownership="targeted",
            now=111,
        )
    with pytest.raises(TaskError, match="cleanup claim expired"):
        q.record_spawn_conclusion(
            old_reservation.key,
            conclusion_state="complete",
            conclusion_detail='{"action":"removed"}',
            claim_token=old_token,
            now=111,
        )
    assert q.get_reservation(old_reservation.key).conclusion_state == "pending"
    assert q.get_reservation(old_reservation.key).cleanup_claim_token == (
        claimed.cleanup_claim_token
    )


def test_http_cleanup_claim_returns_token(api):
    task_id = _create_task(api)
    reservation = api.post(
        "/spawn-reservations",
        json={"task_id": task_id},
    ).json()["reservation"]
    api.post(
        f"/spawn-reservations/{reservation['key']}/release",
        json={"disposition": "failed"},
    )
    api.post(
        f"/spawn-reservations/{reservation['key']}/retire",
        json={
            "exact_absence": True,
            "conclusion_state": "pending",
            "conclusion_detail": (
                '{"action":"failed","reason":"lifecycle-lock-busy"}'
            ),
        },
    )

    response = api.post(
        f"/spawn-reservations/{reservation['key']}/conclusion/claim"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["claimed"] is True
    assert body["claim_token"]
    assert body["reservation"]["conclusion_state"] == "pending"


def test_supersede_exclusive_key_abandons_only_queued_or_proposed(q):
    queued = q.create("old queued", exclusive_key="review:repo:42")
    held = q.create("old held", exclusive_key="review:repo:42")
    q.claim_one("m/wt", task_id=held.id)

    new = q.create(
        "new head",
        exclusive_key="review:repo:42",
        supersede_exclusive_key=True,
    )

    assert q.get(queued.id).status == "abandoned"
    assert q.get(held.id).status == "claimed"
    assert q.get(new.id).status == "queued"
    assert q.events(queued.id)[-1]["note"] == (
        f"superseded by exclusive task {new.id}"
    )


def _fail_attempts(q, task_id, count=3):
    for _ in range(count):
        reservation, reserved = q.reserve_spawn(task_id)
        assert reserved is True
        q.fail_spawn(reservation.key, detail="transport unavailable")


def test_rearm_atomically_retires_failed_history(q):
    t = q.create("work")
    _fail_attempts(q, t.id)

    result = q.rearm_spawn(
        t.id, permitted=True, reason="transport repaired", min_failures=3
    )

    assert result["rearmed"] == 3
    assert result["next_attempt"] == 4
    assert q.list_reservations(task_id=t.id, state=SpawnState.FAILED) == []
    rearmed = q.list_reservations(task_id=t.id, state=SpawnState.REARMED)
    assert len(rearmed) == 3
    assert all("rearmed: transport repaired" in (r.detail or "") for r in rearmed)
    fresh, reserved = q.reserve_spawn(t.id)
    assert reserved is True
    assert fresh.attempt == 4
    assert q.events(t.id)[-1]["note"] == (
        "spawn reservations rearmed: transport repaired"
    )


@pytest.mark.parametrize(
    ("permitted", "reason", "min_failures", "message"),
    [
        (False, "fixed", 3, "explicit permission"),
        (True, "", 3, "non-empty reason"),
        (True, "fixed", 2, "at least 3"),
    ],
)
def test_rearm_requires_guardrails(
    q, permitted, reason, min_failures, message
):
    t = q.create("work")
    _fail_attempts(q, t.id)
    with pytest.raises(TaskError, match=message):
        q.rearm_spawn(
            t.id,
            permitted=permitted,
            reason=reason,
            min_failures=min_failures,
        )
    assert len(q.list_reservations(task_id=t.id, state=SpawnState.FAILED)) == 3


def test_rearm_refuses_insufficient_or_active_history(q):
    t = q.create("work")
    _fail_attempts(q, t.id, count=2)
    with pytest.raises(TaskError, match="at least 3 required"):
        q.rearm_spawn(t.id, permitted=True, reason="fixed")

    reservation, _ = q.reserve_spawn(t.id)
    with pytest.raises(TaskError, match="active spawn reservation"):
        q.rearm_spawn(t.id, permitted=True, reason="fixed", min_failures=3)
    assert q.get_reservation(reservation.key).state == SpawnState.RESERVING


def test_rearm_cannot_bypass_pending_cleanup_worktree_fence(q):
    task = q.create("work", exclusive_key="stable:resource")
    for _ in range(2):
        failed, reserved = q.reserve_spawn(task.id)
        assert reserved is True
        q.fail_spawn(failed.key, detail="transport unavailable")
    pending, reserved = q.reserve_spawn(task.id)
    assert reserved is True
    q.record_spawn_worktree(
        pending.key,
        "wt-pending",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(pending.key, disposition="failed")
    q.retire_spawn(
        pending.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"cleanup-pending"}',
    )

    with pytest.raises(TaskError, match="pending spawn cleanup"):
        q.rearm_spawn(
            task.id,
            permitted=True,
            reason="transport repaired",
            min_failures=3,
        )

    assert q.get_reservation(pending.key).state == SpawnState.FAILED
    assert q.get_reservation(pending.key).conclusion_state == "pending"
    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, reserved = q.reserve_spawn(successor.id)
    assert reserved is True
    assert successor_reservation.worktree is None
    with pytest.raises(TaskError, match="in-flight cleanup"):
        q.record_spawn_worktree(
            successor_reservation.key,
            "wt-pending",
            ownership="targeted",
        )

    _, claimed, claim_token = q.claim_spawn_conclusion_retry(pending.key)
    assert claimed is True
    q.record_spawn_conclusion(
        pending.key,
        conclusion_state="complete",
        conclusion_detail='{"action":"removed"}',
        claim_token=claim_token,
    )
    assigned = q.record_spawn_worktree(
        successor_reservation.key,
        "wt-pending",
        ownership="targeted",
    )
    assert assigned.worktree == "wt-pending"


def test_rearm_refuses_owned_task_without_mutation(q):
    t = q.create("work")
    _fail_attempts(q, t.id)
    q.claim_one("m/wt", task_id=t.id)

    with pytest.raises(TaskError, match="rearm requires queued and unowned"):
        q.rearm_spawn(t.id, permitted=True, reason="fixed")

    assert len(q.list_reservations(task_id=t.id, state=SpawnState.FAILED)) == 3


def test_rearm_races_reserve_without_duplicate_spawn_right(q):
    t = q.create("work")
    _fail_attempts(q, t.id)
    barrier = threading.Barrier(2)

    def rearm():
        barrier.wait()
        try:
            return q.rearm_spawn(t.id, permitted=True, reason="fixed")
        except TaskError:
            return None

    def reserve():
        barrier.wait()
        try:
            return q.reserve_spawn(t.id)
        except TaskError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rearm_result, reserve_result = pool.submit(rearm), pool.submit(reserve)
        outcomes = (rearm_result.result(), reserve_result.result())

    active = q.list_reservations(task_id=t.id, state=SpawnState.ACTIVE)
    assert len(active) == 1
    assert outcomes[1] is not None
    if outcomes[0] is None:
        assert len(q.list_reservations(task_id=t.id, state=SpawnState.FAILED)) == 3
    else:
        assert q.list_reservations(task_id=t.id, state=SpawnState.FAILED) == []


def test_rearm_races_claim_without_partial_mutation(q):
    t = q.create("work")
    _fail_attempts(q, t.id)
    barrier = threading.Barrier(2)

    def rearm():
        barrier.wait()
        try:
            return q.rearm_spawn(t.id, permitted=True, reason="fixed")
        except TaskError:
            return None

    def claim():
        barrier.wait()
        return q.claim_one("m/wt", task_id=t.id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rearm_result, claim_result = pool.submit(rearm), pool.submit(claim)
        outcomes = (rearm_result.result(), claim_result.result())

    assert outcomes[1] is not None
    if outcomes[0] is None:
        assert len(q.list_reservations(task_id=t.id, state=SpawnState.FAILED)) == 3
        assert q.list_reservations(task_id=t.id, state=SpawnState.REARMED) == []
    else:
        assert q.list_reservations(task_id=t.id, state=SpawnState.FAILED) == []
        assert len(q.list_reservations(task_id=t.id, state=SpawnState.REARMED)) == 3


# -- HTTP surface ------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(TaskQueue(tmp_path / "tasks.db")))


def _create_task(api) -> str:
    resp = api.post("/tasks", json={"title": "work", "repo": TEST_REPO})
    return resp.json()["id"]


def test_http_reserve_record_list(api):
    task_id = _create_task(api)

    r = api.post("/spawn-reservations", json={"task_id": task_id, "reserved_by": "cli"})
    assert r.status_code == 200
    body = r.json()
    assert body["reserved"] is True
    key = body["reservation"]["key"]

    # second reserve -> not reserved
    r2 = api.post("/spawn-reservations", json={"task_id": task_id})
    assert r2.json()["reserved"] is False

    rec = api.post(
        f"/spawn-reservations/{key}/spawned",
        json={"session_handle": "s", "worktree": "w"},
    )
    assert rec.status_code == 200
    assert rec.json()["state"] == SpawnState.SPAWNED

    listed = api.get("/spawn-reservations", params={"task_id": task_id}).json()
    assert len(listed) == 1
    got = api.get(f"/spawn-reservations/{key}").json()
    assert got["key"] == key
    settled = api.post(
        f"/spawn-reservations/{key}/settle",
        json={
            "detail": "task completed",
            "conclusion_state": "pending",
            "conclusion_detail": '{"reason":"live-session"}',
        },
    )
    assert settled.status_code == 200
    assert settled.json()["conclusion_state"] == "pending"
    assert settled.json()["conclusion_detail"] == '{"reason":"live-session"}'
    active_task = _create_task(api)
    active = api.post(
        "/spawn-reservations",
        json={"task_id": active_task, "reserved_by": "cli"},
    ).json()["reservation"]
    api.post(
        f"/spawn-reservations/{active['key']}/spawned",
        json={"session_handle": "local-body:s", "worktree": "w2"},
    )
    checkpoint = api.post(
        f"/spawn-reservations/{active['key']}/conclusion",
        json={
            "conclusion_state": "pending",
            "conclusion_detail": '{"attempts":1}',
        },
    )
    assert checkpoint.status_code == 200
    assert checkpoint.json()["state"] == SpawnState.SPAWNED
    assert checkpoint.json()["conclusion_state"] == "pending"


def test_http_reserve_unknown_task_404(api):
    r = api.post("/spawn-reservations", json={"task_id": "does-not-exist"})
    assert r.status_code == 404


def test_http_reserve_nonqueued_task_409(api):
    response = api.post(
        "/tasks", json={"title": "draft", "repo": TEST_REPO, "proposed": True}
    )
    task_id = response.json()["id"]

    reserved = api.post("/spawn-reservations", json={"task_id": task_id})

    assert reserved.status_code == 409
    assert "requires queued and unowned" in reserved.json()["detail"]


def test_http_bad_transition_409(api):
    task_id = _create_task(api)
    key = api.post("/spawn-reservations", json={"task_id": task_id}).json()["reservation"]["key"]
    api.post(f"/spawn-reservations/{key}/settle", json={})
    r = api.post(f"/spawn-reservations/{key}/fail", json={})
    assert r.status_code == 409


def test_http_releasing_requires_proof_gated_retire(api):
    task_id = _create_task(api)
    reservation = api.post(
        "/spawn-reservations",
        json={"task_id": task_id},
    ).json()["reservation"]
    api.post(
        f"/spawn-reservations/{reservation['key']}/release",
        json={"disposition": "failed"},
    )

    for operation in ("fail", "settle", "defer"):
        response = api.post(
            f"/spawn-reservations/{reservation['key']}/{operation}",
            json={},
        )
        assert response.status_code == 409
    refused = api.post(
        f"/spawn-reservations/{reservation['key']}/retire",
        json={"exact_absence": False},
    )
    assert refused.status_code == 409
    retired = api.post(
        f"/spawn-reservations/{reservation['key']}/retire",
        json={"exact_absence": True},
    )
    assert retired.status_code == 200
    assert retired.json()["state"] == SpawnState.FAILED


def test_http_defer(api):
    """The `/defer` route mirrors `/fail`'s shape but lands `deferred`, not
    `failed` -- a carried session confirmed live/busy (#2056) is a legitimate
    deferral, not a spawn failure."""
    task_id = _create_task(api)
    key = api.post("/spawn-reservations", json={"task_id": task_id}).json()["reservation"]["key"]
    r = api.post(
        f"/spawn-reservations/{key}/defer", json={"detail": "carried session busy"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "deferred"
    assert body["detail"] == "carried session busy"

    # deferred is terminal too -- cannot defer (or fail) it again
    r2 = api.post(f"/spawn-reservations/{key}/defer", json={})
    assert r2.status_code == 409
    r3 = api.post(f"/spawn-reservations/{key}/fail", json={})
    assert r3.status_code == 409


def test_http_record_missing_404(api):
    r = api.post("/spawn-reservations/dispatch-task:nope:1/spawned", json={})
    assert r.status_code == 404


def test_http_rearm(api):
    task_id = _create_task(api)
    for _ in range(3):
        reservation = api.post(
            "/spawn-reservations", json={"task_id": task_id}
        ).json()["reservation"]
        api.post(
            f"/spawn-reservations/{reservation['key']}/fail",
            json={"detail": "down"},
        )

    response = api.post(
        f"/spawn-reservations/tasks/{task_id}/rearm",
        json={
            "permitted": True,
            "reason": "transport repaired",
            "min_failures": 3,
        },
    )

    assert response.status_code == 200
    assert response.json()["rearmed"] == 3


def test_http_rearm_rejects_pending_cleanup(api):
    task_id = _create_task(api)
    for _ in range(2):
        reservation = api.post(
            "/spawn-reservations",
            json={"task_id": task_id},
        ).json()["reservation"]
        api.post(
            f"/spawn-reservations/{reservation['key']}/fail",
            json={"detail": "transport unavailable"},
        )
    pending = api.post(
        "/spawn-reservations",
        json={"task_id": task_id},
    ).json()["reservation"]
    api.post(
        f"/spawn-reservations/{pending['key']}/release",
        json={"disposition": "failed"},
    )
    api.post(
        f"/spawn-reservations/{pending['key']}/retire",
        json={
            "exact_absence": True,
            "conclusion_state": "pending",
            "conclusion_detail": (
                '{"action":"failed","reason":"cleanup-pending"}'
            ),
        },
    )

    response = api.post(
        f"/spawn-reservations/tasks/{task_id}/rearm",
        json={
            "permitted": True,
            "reason": "transport repaired",
            "min_failures": 3,
        },
    )

    assert response.status_code == 409
    assert "pending spawn cleanup" in response.json()["detail"]


def test_reserve_refuses_nonqueued_task(q):
    task = q.propose("draft")
    with pytest.raises(TaskError, match="spawn reservation requires queued and unowned"):
        q.reserve_spawn(task.id)


def test_cli_rearm_passes_operator_guardrails(monkeypatch):
    calls = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def rearm_spawn(
            self, task_id, *, permitted=False, reason=None, min_failures=3
        ):
            calls.append((task_id, permitted, reason, min_failures))
            return {"task_id": task_id, "rearmed": 3}

    monkeypatch.setattr(m, "_client", lambda _args: FakeClient())
    args = m.build_parser().parse_args(
        [
            "reservations",
            "rearm",
            "task-1",
            "--permit",
            "--reason",
            "transport repaired",
            "--min-failures",
            "4",
        ]
    )

    assert args.func(args) == 0
    assert calls == [("task-1", True, "transport repaired", 4)]


@pytest.mark.parametrize("command", ["fail", "settle", "defer"])
def test_cli_release_pending_transition_is_rejected(
    monkeypatch, q, command
):
    task = q.create("cleanup")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-cleanup",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed" if command == "fail" else "settled",
    )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fail_spawn(self, key, *, detail=None):
            from dataclasses import asdict

            return asdict(q.fail_spawn(key, detail=detail))

        def settle_spawn(self, key, *, detail=None):
            from dataclasses import asdict

            return asdict(q.settle_spawn(key, detail=detail))

        def defer_spawn(self, key, *, detail=None):
            from dataclasses import asdict

            return asdict(q.defer_spawn(key, detail=detail))

    monkeypatch.setattr(m, "_client", lambda _args: FakeClient())
    args = m.build_parser().parse_args(
        ["reservations", command, reservation.key]
    )

    with pytest.raises(TaskError):
        args.func(args)
    updated = q.get_reservation(reservation.key)
    assert updated.state == SpawnState.RELEASING
    assert updated.conclusion_state == "pending"
    assert updated.cleanup_claim_token is None


# -- create --spawn double-spawn guard ---------------------------------------


class _QueueBackedClient:
    """A minimal DispatchClient stand-in backed by a real TaskQueue.

    Only the reservation methods `_spawn_worker_for` calls are implemented.
    """

    def __init__(self, queue):
        self._q = queue

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def reserve_spawn(self, task_id, *, reserved_by=None):
        res, ok = self._q.reserve_spawn(task_id, reserved_by=reserved_by)
        from dataclasses import asdict

        return {"reserved": ok, "reservation": asdict(res)}

    def record_spawn(self, key, *, session_handle=None, worktree=None):
        from dataclasses import asdict

        return asdict(self._q.record_spawn(key, session_handle=session_handle, worktree=worktree))

    def record_spawn_worktree(self, key, worktree, **kwargs):
        from dataclasses import asdict

        return asdict(self._q.record_spawn_worktree(key, worktree, **kwargs))

    def fail_spawn(
        self,
        key,
        *,
        detail=None,
        conclusion_state=None,
        conclusion_detail=None,
    ):
        from dataclasses import asdict

        return asdict(
            self._q.fail_spawn(
                key,
                detail=detail,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
            )
        )

    def defer_spawn(self, key, *, detail=None):
        from dataclasses import asdict

        return asdict(self._q.defer_spawn(key, detail=detail))

    def request_spawn_release(
        self, key, *, detail=None, disposition="failed"
    ):
        from dataclasses import asdict

        return asdict(
            self._q.request_spawn_release(
                key,
                detail=detail,
                disposition=disposition,
            )
        )

    def retire_spawn(
        self,
        key,
        *,
        exact_absence,
        detail=None,
        conclusion_state=None,
        conclusion_detail=None,
    ):
        from dataclasses import asdict

        return asdict(
            self._q.retire_spawn(
                key,
                exact_absence=exact_absence,
                detail=detail,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
            )
        )

    def record_spawn_conclusion(
        self, key, *, conclusion_state, conclusion_detail
    ):
        from dataclasses import asdict

        return asdict(
            self._q.record_spawn_conclusion(
                key,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
            )
        )


def _mock_created_worktree(monkeypatch):
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda _task, reservation, **_kwargs: {
            "worktree": f"wt-{reservation['attempt']}",
            "path": f"/tmp/wt-{reservation['attempt']}",
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )


def test_create_spawn_never_double_spawns(monkeypatch, q):
    """Two `create --spawn` on one task spawn the worker exactly once."""
    t = q.create("work")
    spawns: list[str] = []

    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)

    def fake_do_spawn(_args, task, *, route=""):
        spawns.append(task["id"])
        return (types.SimpleNamespace(returncode=0), "fake", {"session": "s", "worktree": "w"})

    monkeypatch.setattr(m, "_do_spawn", fake_do_spawn)

    args = types.SimpleNamespace(url=None, token=None)
    m._spawn_worker_for(args, {"id": t.id})
    m._spawn_worker_for(args, {"id": t.id})  # dedup collision / re-run

    assert spawns == [t.id]  # spawned exactly once
    # the reservation is recorded as spawned
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


def test_create_spawn_failure_without_absence_proof_remains_fenced(
    monkeypatch, q
):
    """A failed launch with no parsed handle is not positive body absence."""
    t = q.create("work")
    calls: list[int] = []

    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "conclude_dispatch_attempt",
        lambda *_args, **_kwargs: {"action": "removed"},
    )

    def failing_do_spawn(_args, task, *, route=""):
        calls.append(1)
        return (types.SimpleNamespace(returncode=1), "fake", {"session": None, "worktree": None})

    monkeypatch.setattr(m, "_do_spawn", failing_do_spawn)
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": t.id})
    first = q.latest_reservation(t.id)
    assert first.state == SpawnState.RELEASING
    assert first.conclusion_state == "complete"

    m._spawn_worker_for(args, {"id": t.id})
    assert len(calls) == 1


def test_create_spawn_failure_stays_fenced_when_cleanup_is_pending(
    monkeypatch, q
):
    t = q.create("work")
    calls: list[int] = []

    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "conclude_dispatch_attempt",
        lambda *_args, **_kwargs: {
            "action": "skipped",
            "reason": "live-session",
        },
    )

    def failing_do_spawn(_args, task, *, route=""):
        calls.append(1)
        return (
            types.SimpleNamespace(returncode=1),
            "fake",
            {"session": "session-live", "worktree": task["spawn_worktree"]},
        )

    monkeypatch.setattr(m, "_do_spawn", failing_do_spawn)
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": t.id})
    first = q.latest_reservation(t.id)
    assert first.state == SpawnState.RELEASING
    assert first.conclusion_state == "pending"

    m._spawn_worker_for(args, {"id": t.id})
    assert len(calls) == 1


def test_create_spawn_failure_without_handle_stays_fenced_when_cleanup_is_held(
    monkeypatch, q
):
    t = q.create("work")
    calls: list[int] = []

    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "conclude_dispatch_attempt",
        lambda *_args, **_kwargs: {
            "action": "preserved",
            "reason": "dirty-worktree",
        },
    )

    def failing_do_spawn(_args, task, *, route=""):
        calls.append(1)
        return (
            types.SimpleNamespace(returncode=1),
            "fake",
            {"session": None, "worktree": task["spawn_worktree"]},
        )

    monkeypatch.setattr(m, "_do_spawn", failing_do_spawn)
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": t.id})
    first = q.latest_reservation(t.id)
    assert first.state == SpawnState.RELEASING
    assert first.conclusion_state == "held"

    m._spawn_worker_for(args, {"id": t.id})
    assert len(calls) == 1


@pytest.mark.parametrize("session_handle", [None, ""])
@pytest.mark.parametrize("reason", ["live-session", "live-mux"])
def test_create_spawn_failure_without_handle_stays_fenced_when_pending(
    monkeypatch, q, session_handle, reason
):
    task = q.create("work")
    calls = []
    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "conclude_dispatch_attempt",
        lambda *_args, **_kwargs: {
            "action": "skipped",
            "reason": reason,
        },
    )

    def failing_do_spawn(_args, spawn_task, *, route=""):
        calls.append(spawn_task["id"])
        return (
            types.SimpleNamespace(returncode=1),
            "fake",
            {
                "session": session_handle,
                "worktree": spawn_task["spawn_worktree"],
            },
        )

    monkeypatch.setattr(m, "_do_spawn", failing_do_spawn)
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": task.id})
    fenced = q.latest_reservation(task.id)
    assert fenced.state == SpawnState.RELEASING
    assert fenced.conclusion_state == "pending"
    assert reason in (fenced.conclusion_detail or "")

    m._spawn_worker_for(args, {"id": task.id})
    assert calls == [task.id]


def test_create_spawn_failure_without_handle_stays_fenced_when_cleanup_complete(
    monkeypatch, q
):
    task = q.create("work")
    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "conclude_dispatch_attempt",
        lambda *_args, **_kwargs: {
            "action": "removed",
            "reason": "managed-gc-removed",
        },
    )
    monkeypatch.setattr(
        m,
        "_do_spawn",
        lambda _args, spawn_task, *, route="": (
            types.SimpleNamespace(returncode=1),
            "fake",
            {
                "session": None,
                "worktree": spawn_task["spawn_worktree"],
            },
        ),
    )
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": task.id})

    fenced = q.latest_reservation(task.id)
    assert fenced.state == SpawnState.RELEASING
    assert fenced.conclusion_state == "complete"


def test_no_spawn_mechanism_can_retire_held_cleanup(monkeypatch, q):
    task = q.create("work")
    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))
    _mock_created_worktree(monkeypatch)
    from agent_dispatch import embody

    monkeypatch.setattr(
        embody,
        "conclude_dispatch_attempt",
        lambda *_args, **_kwargs: {
            "action": "preserved",
            "reason": "dirty-worktree",
        },
    )
    monkeypatch.setattr(m, "_do_spawn", lambda *_args, **_kwargs: None)
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": task.id})

    retired = q.latest_reservation(task.id)
    assert retired.state == SpawnState.FAILED
    assert retired.conclusion_state == "held"
