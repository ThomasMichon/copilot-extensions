"""Tests for the spawn-reservation primitive.

The spawn reservation is the atomic "exactly one embody spawn per (task,
attempt)" record that closes the gap between the queue's transactional claim and
the non-transactional CLI-side spawn -- so ``create --spawn`` (and, later, the
supervisor loop) can never double-spawn an autonomous worker.
"""

from __future__ import annotations

import concurrent.futures
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


def test_bad_transitions_raise(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    q.settle_spawn(r1.key)
    # settled is terminal -- cannot fail it again
    with pytest.raises(TaskError):
        q.fail_spawn(r1.key)
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
    assert updated.state == SpawnState.SPAWNED
    assert updated.release_requested is True


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
        json={"session_handle": "local-body:s", "worktree": "w"},
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

    def fail_spawn(self, key, *, detail=None):
        from dataclasses import asdict

        return asdict(self._q.fail_spawn(key, detail=detail))

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


def test_create_spawn_failure_cleans_up_before_retry(monkeypatch, q):
    """A one-shot failed spawn concludes its created checkout synchronously."""
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
    assert first.state == SpawnState.FAILED

    m._spawn_worker_for(args, {"id": t.id})
    assert len(calls) == 2
    assert q.latest_reservation(t.id).attempt == 2


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
