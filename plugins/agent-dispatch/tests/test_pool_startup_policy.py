"""Fake-clock controls over real assignment, start and retirement transactions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Barrier

import pytest

from agent_dispatch.queue import SpawnReservation, SpawnState, Status, Task, TaskError, TaskQueue
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, task

q = placement_controls.q


def claimed(q: TaskQueue) -> tuple[Task, SpawnReservation]:
    publish(q, pool())
    t = task(q)
    r, _ = place(q, t)
    return q.get(t.id), r


def reachable(q: TaskQueue, t: Task, r: SpawnReservation, now: float = 101) -> None:
    record_native(q, r)
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.observe_pool_reachable(
        r.key, generation=t.generation, startup_revision=1, native_id="native", now=now,
    )


def exhaust(q: TaskQueue, t: Task, r: SpawnReservation, now: float = 700) -> Task:
    return q.exhaust_pool_startup(
        r.key, generation=t.generation, startup_revision=r.startup_revision, now=now,
    )


def renew(q: TaskQueue, now: float) -> None:
    scope = q.pool_catalog_snapshot().scopes[0]
    q.publish_pool_catalog_scope(
        scope.machine, scope.env, scope.publisher_id, generation=scope.generation,
        expected_revision=scope.revision, registration_revision=q.pool_registration_revision(),
        input_revision=f"renewed-{now}", definitions=scope.definitions, expected_cohort_revision=1,
        now=now,
    )


def test_policy_is_durable_and_placement_replay_does_not_restart_clocks(q: TaskQueue) -> None:
    t, r = claimed(q)
    before = q.pool_startup(t.id)
    assert before.startup_attempts == 1 and before.episode_start_attempt == 0
    assert before.startup_deadline == 700 and before.cooldown_stage == 0
    assert before.reachable_at is None and before.decision_deadline is None
    assert place(q, t, now=699) == (r, False)
    reopened = TaskQueue(q.db_path)
    assert reopened.pool_startup(t.id) == before
    assert reopened.task_pool_assignment(t.id) == q.task_pool_assignment(t.id)


def test_additive_legacy_migration_preserves_claim_and_refuses_invented_budget(q: TaskQueue) -> None:
    t, r = claimed(q)
    record_native(q, r)
    before = q.get(t.id), q.get_reservation(r.key), q.task_pool_assignment(t.id)
    with q._connect() as conn:
        for name in (
            "startup_attempts", "episode_start_attempt", "cooldown_stage", "startup_deadline",
            "startup_timeout",
            "outcome_kind", "outcome_detail", "outcome_revision", "outcome_at",
            "reachable_at", "next_nudge_at", "decision_deadline", "exhausted_at", "exhaustion_reason",
        ):
            conn.execute(f"ALTER TABLE task_pool_assignments DROP COLUMN {name}")
    reopened = TaskQueue(q.db_path)
    assert (reopened.get(t.id), reopened.get_reservation(r.key),
            reopened.task_pool_assignment(t.id)) == before
    policy = reopened.pool_startup(t.id)
    assert policy.startup_attempts == 0 and policy.startup_deadline is None
    with pytest.raises(TaskError, match="reconciliation"):
        reopened.start(t.id, "host-a/worktree", owner_session_id="native",
                       expected_generation=t.generation, now=101)
    assert TaskQueue(q.db_path).pool_startup(t.id) == policy


def test_reachability_starts_separate_decision_clock_without_resetting_budget(q: TaskQueue) -> None:
    t, r = claimed(q)
    reachable(q, t, r, 699)
    before = q.pool_startup(t.id)
    assert before.reachable_at == 699 and before.decision_deadline == 2499
    assert before.next_nudge_at == 999 and before.startup_attempts == 1
    assert q.get(t.id).status == Status.CLAIMED and q.get(t.id).owner is None
    q.observe_pool_reachable(
        r.key, generation=t.generation, startup_revision=1, native_id="native", now=900,
    )
    assert q.pool_startup(t.id) == before
    with pytest.raises(TaskError, match="not expired"):
        exhaust(q, t, r, 2498)
    assert exhaust(q, t, r, 2499).status == Status.QUEUED
    assert q.pool_startup(t.id).exhaustion_reason == "decision_timeout"


@pytest.mark.parametrize("now", [700, 701])
def test_first_reachability_cannot_revive_expired_startup(q: TaskQueue, now: float) -> None:
    t, r = claimed(q)
    record_native(q, r)
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    with pytest.raises(TaskError, match="expired"):
        q.observe_pool_reachable(
            r.key, generation=t.generation, startup_revision=1, native_id="native", now=now,
        )
    assert q.pool_startup(t.id).reachable_at is None


def test_delivered_startup_is_not_reachability_or_acceptance(q: TaskQueue) -> None:
    t, r = claimed(q)
    record_native(q, r)
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    assert q.pool_startup(t.id).reachable_at is None
    assert exhaust(q, t, r).status == Status.QUEUED
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and retained.startup_fenced
    assert retained.session_handle == "native" and retained.worktree == "worktree"
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_due_nudge_slot_is_atomic_and_never_a_delivery_or_start(q: TaskQueue) -> None:
    t, r = claimed(q)
    reachable(q, t, r)
    assert q.take_pool_nudge(
        r.key, generation=t.generation, startup_revision=1, native_id="native", now=400,
    ) is None
    barrier = Barrier(2)

    def take() -> bool:
        barrier.wait(timeout=5)
        return q.take_pool_nudge(
            r.key, generation=t.generation, startup_revision=1, native_id="native", now=401,
        ) is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: take(), range(2))) == [False, True]
    assert q.pool_startup(t.id).next_nudge_at == 701
    assert q.get(t.id).status == Status.CLAIMED
    with pytest.raises(TaskError, match="expired"):
        q.take_pool_nudge(
            r.key, generation=t.generation, startup_revision=1, native_id="native", now=1901,
        )
    assert q.pool_startup(t.id).startup_attempts == 1


def test_three_completed_same_reservation_startup_attempts_are_bounded(q: TaskQueue) -> None:
    t, r = claimed(q)
    record_native(q, r)
    for revision in (1, 2, 3):
        q.record_spawn(
            r.key, session_handle="native", startup_complete=True, startup_revision=revision,
            now=100 + revision,
        )
        if revision < 3:
            q.record_spawn(
                r.key, session_handle="native", startup_complete=False, startup_revision=revision,
                now=110 + revision,
            )
    before = q.get_reservation(r.key), q.pool_startup(t.id)
    with pytest.raises(TaskError, match="another attempt"):
        q.record_spawn(
            r.key, session_handle="native", startup_complete=False, startup_revision=3, now=120,
        )
    assert (q.get_reservation(r.key), q.pool_startup(t.id)) == before
    assert before[1].startup_attempts == 3
    assert len(q.list_reservations(task_id=t.id)) == 1
    assert q.task_pool_assignment(t.id).reservation_key == r.key


def test_incomplete_startup_does_not_consume_a_second_attempt(q: TaskQueue) -> None:
    t, r = claimed(q)
    before = q.pool_startup(t.id)
    with pytest.raises(TaskError, match="incomplete"):
        q.record_spawn(
            r.key, session_handle="native", startup_complete=False, startup_revision=1, now=101,
        )
    assert q.pool_startup(t.id) == before


def test_unknown_handleless_exhaustion_queues_but_never_frees_capacity(q: TaskQueue) -> None:
    t, r = claimed(q)
    before = q.task_pool_assignment(t.id)
    queued = exhaust(q, t, r)
    assert queued.status == Status.QUEUED and queued.generation == t.generation + 1
    assert queued.not_before == 4300 and queued.owner is None
    retained = q.get_reservation(r.key)
    assert retained.state == SpawnState.RELEASING and retained.release_requested
    assert not retained.startup_complete and not retained.startup_fenced
    assert retained.session_handle is None and retained.worktree is None
    assert q.task_pool_assignment(t.id) == before
    assert q.pool_startup(t.id).exhaustion_reason == "startup_timeout"
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with pytest.raises(TaskError, match="original caller"):
        q.retire_spawn(r.key, exact_absence=True)
    with pytest.raises(TaskError, match="changed"):
        exhaust(q, t, r, 701)
    assert q.pool_startup(t.id).cooldown_stage == 1


def test_next_episode_preserves_counters_and_requires_retirement_and_due_time(q: TaskQueue) -> None:
    t, r = claimed(q)
    queued = exhaust(q, t, r)
    assert place(q, queued, now=4300)[1] is False
    q.request_spawn_release(r.key, finished_startup_revision=1, now=701)
    q.retire_spawn(r.key, exact_absence=True, now=702)
    with pytest.raises(TaskError, match="not due"):
        place(q, queued, now=4299)
    renew(q, 4300)
    replacement, reserved = place(q, queued, now=4300)
    assert reserved and replacement.key != r.key
    policy = q.pool_startup(t.id)
    assert policy.startup_attempts == 2 and policy.episode_start_attempt == 1
    assert policy.cooldown_stage == 1 and policy.startup_deadline == 4900
    assert policy.exhausted_at is None and policy.exhaustion_reason is None
    assert len(q.list_reservations(task_id=t.id)) == 2
    assert q.task_pool_assignment(t.id).registration_id == "worker"


def test_five_actual_episodes_escalate_without_erasing_startup_history(q: TaskQueue) -> None:
    t, r = claimed(q)
    clock = 100
    for episode, delay in enumerate((3600, 10800, 21600, 43200, 43200), 1):
        queued = exhaust(q, t, r, clock + 600)
        assert queued.not_before == clock + 600 + delay
        policy = q.pool_startup(t.id)
        assert policy.startup_attempts == episode and policy.episode_start_attempt == episode - 1
        assert policy.cooldown_stage == min(episode, 4)
        q.request_spawn_release(
            r.key, finished_startup_revision=r.startup_revision, now=clock + 601,
        )
        q.retire_spawn(r.key, exact_absence=True, now=clock + 602)
        clock = queued.not_before
        renew(q, clock)
        r, reserved = place(q, queued, now=clock)
        assert reserved
        t = q.get(t.id)
    record_native(q, r)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=t.generation, now=clock)
    policy = q.pool_startup(t.id)
    assert policy.startup_attempts == 0 and policy.cooldown_stage == 0
    assert len(q.list_reservations(task_id=t.id)) == 6
    assert q.get(t.id).not_before == clock


def test_write_lock_clock_not_prior_observation_decides_acceptance(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    t, r = claimed(q)
    record_native(q, r)
    observations = iter((699, 699, 700))
    monkeypatch.setattr(q, "_now", lambda now=None: next(observations))
    with pytest.raises(TaskError, match="deadline expired"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=t.generation)
    assert q.get(t.id).status == Status.CLAIMED


def test_late_current_startup_return_is_retained_for_release_not_acceptance(q: TaskQueue) -> None:
    t, r = claimed(q)
    record_native(q, r)
    exhaust(q, t, r)
    late = q.record_spawn(
        r.key, session_handle="native", worktree="worktree", startup_complete=True,
        startup_revision=1, now=701,
    )
    assert late.state == SpawnState.RELEASING and late.startup_complete
    with pytest.raises(TaskError):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=t.generation, now=701)
    assert q.get(t.id).status == Status.QUEUED
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


def test_late_unknown_workspace_and_native_are_retained_by_release_owner(q: TaskQueue) -> None:
    t, r = claimed(q)
    exhaust(q, t, r)
    late = q.request_spawn_release(
        r.key, session_handle="native", worktree="late-worktree", finished_startup_revision=1,
        now=701,
    )
    assert late.state == SpawnState.RELEASING and late.startup_fenced
    assert late.session_handle == "native" and late.worktree == "late-worktree"
    assert q.get(t.id).status == Status.QUEUED
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))


@pytest.mark.parametrize("clock", [float("nan"), float("inf"), float("-inf")])
def test_invalid_clock_never_mutates_budget_or_task(q: TaskQueue, clock: float) -> None:
    t, r = claimed(q)
    before = q.get(t.id), q.pool_startup(t.id), q.get_reservation(r.key)
    with pytest.raises(TaskError, match="finite"):
        exhaust(q, t, r, clock)
    assert (q.get(t.id), q.pool_startup(t.id), q.get_reservation(r.key)) == before


def test_start_and_exhaustion_race_has_one_current_incarnation(q: TaskQueue) -> None:
    t, r = claimed(q)
    record_native(q, r)
    barrier = Barrier(2)

    def accept() -> str:
        barrier.wait(timeout=5)
        try:
            q.start(t.id, "host-a/worktree", owner_session_id="native",
                    expected_generation=t.generation, now=699)
            return "started"
        except TaskError:
            return "refused"

    def expire() -> str:
        barrier.wait(timeout=5)
        try:
            exhaust(q, t, r)
            return "queued"
        except TaskError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(accept), executor.submit(expire)
        result = first.result(), second.result()
    assert result in {("started", "refused"), ("refused", "queued")}
    current = q.get(t.id)
    policy = q.pool_startup(t.id)
    if current.status == Status.STARTED:
        assert policy.startup_attempts == 0 and policy.cooldown_stage == 0
    else:
        assert current.status == Status.QUEUED
        assert policy.startup_attempts == 1 and policy.cooldown_stage == 1


@pytest.mark.parametrize("operation", ["reachable", "exhaust", "nudge", "retry"])
@pytest.mark.parametrize("hold", [False, True])
def test_stale_or_held_policy_writes_are_atomic(
    q: TaskQueue, operation: str, hold: bool,
) -> None:
    t, r = claimed(q)
    reachable(q, t, r)
    if hold:
        q.set_hold(t.id, reason="operator", actor="operator")
        generation = t.generation
    else:
        generation = t.generation + 1
    before = q.get(t.id), q.pool_startup(t.id), q.get_reservation(r.key)
    with pytest.raises(TaskError):
        if operation == "reachable":
            q.observe_pool_reachable(
                r.key, generation=generation, startup_revision=1, native_id="native", now=102,
            )
        elif operation == "nudge":
            q.take_pool_nudge(
                r.key, generation=generation, startup_revision=1, native_id="native", now=401,
            )
        elif operation == "retry":
            q.record_spawn(
                r.key, session_handle="native", startup_complete=False,
                startup_revision=1, now=102,
            )
        else:
            q.exhaust_pool_startup(
                r.key, generation=generation, startup_revision=1, now=1901,
            )
    assert (q.get(t.id), q.pool_startup(t.id), q.get_reservation(r.key)) == before


def test_only_current_start_resets_counters_and_preserves_later_due_time(q: TaskQueue) -> None:
    t, r = claimed(q)
    reachable(q, t, r)
    with q._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET cooldown_stage=3 WHERE task_id=?", (t.id,),
        )
        conn.execute("UPDATE tasks SET not_before=9999 WHERE id=?", (t.id,))
    before = asdict(q.pool_startup(t.id))
    with pytest.raises(TaskError):
        q.start(t.id, "host-a/worktree", owner_session_id="other",
                expected_generation=t.generation, now=102)
    assert asdict(q.pool_startup(t.id)) == before
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=t.generation, now=102)
    policy = q.pool_startup(t.id)
    assert policy.startup_attempts == 0 and policy.cooldown_stage == 0
    assert policy.decision_deadline is None and policy.startup_deadline is None
    assert q.get(t.id).not_before == 9999
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=t.generation, now=99999)
    assert q.pool_startup(t.id) == policy


@pytest.mark.parametrize("stage,delay", [(0, 3600), (1, 10800), (2, 21600), (3, 43200), (4, 43200)])
@pytest.mark.parametrize("later", [False, True])
def test_task_wide_cooldown_escalates_caps_and_preserves_later_due_time(
    q: TaskQueue, stage: int, delay: int, later: bool,
) -> None:
    t, r = claimed(q)
    due = 100000 if later else 0
    with q._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET cooldown_stage=? WHERE task_id=?", (stage, t.id),
        )
        conn.execute("UPDATE tasks SET not_before=? WHERE id=?", (due, t.id))
    queued = exhaust(q, t, r)
    assert queued.not_before == max(due, 700 + delay)
    assert q.pool_startup(t.id).cooldown_stage == min(stage + 1, 4)
    assert q.pool_startup(t.id).startup_attempts == 1


def test_start_cannot_win_after_deadline_even_before_supervisor_exhaustion(q: TaskQueue) -> None:
    t, r = claimed(q)
    record_native(q, r)
    with pytest.raises(TaskError, match="deadline expired"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=t.generation, now=700)
    assert q.get(t.id).status == Status.CLAIMED


def test_legacy_worker_start_has_no_pool_policy(q: TaskQueue) -> None:
    t = task(q)
    worker = q.claim_one("host-a/worktree", task_id=t.id)
    assert worker is not None
    q.start(t.id, "host-a/worktree", now=99999)
    assert q.pool_startup(t.id) is None and q.get(t.id).status == Status.STARTED
