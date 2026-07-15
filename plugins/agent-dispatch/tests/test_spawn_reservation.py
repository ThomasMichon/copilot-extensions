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


def test_settle_releases_for_a_fresh_attempt(q):
    t = q.create("work")
    r1, _ = q.reserve_spawn(t.id)
    q.record_spawn(r1.key)
    q.settle_spawn(r1.key)

    r2, ok = q.reserve_spawn(t.id)
    assert ok is True
    assert r2.attempt == 2
    assert r2.key == spawn_key(t.id, 2)


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


def test_http_reserve_unknown_task_404(api):
    r = api.post("/spawn-reservations", json={"task_id": "does-not-exist"})
    assert r.status_code == 404


def test_http_bad_transition_409(api):
    task_id = _create_task(api)
    key = api.post("/spawn-reservations", json={"task_id": task_id}).json()["reservation"]["key"]
    api.post(f"/spawn-reservations/{key}/settle", json={})
    r = api.post(f"/spawn-reservations/{key}/fail", json={})
    assert r.status_code == 409


def test_http_record_missing_404(api):
    r = api.post("/spawn-reservations/dispatch-task:nope:1/spawned", json={})
    assert r.status_code == 404


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

    def fail_spawn(self, key, *, detail=None):
        from dataclasses import asdict

        return asdict(self._q.fail_spawn(key, detail=detail))


def test_create_spawn_never_double_spawns(monkeypatch, q):
    """Two `create --spawn` on one task spawn the worker exactly once."""
    t = q.create("work")
    spawns: list[str] = []

    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))

    def fake_do_spawn(_args, task):
        spawns.append(task["id"])
        return (types.SimpleNamespace(returncode=0), "fake", {"session": "s", "worktree": "w"})

    monkeypatch.setattr(m, "_do_spawn", fake_do_spawn)

    args = types.SimpleNamespace(url=None, token=None)
    m._spawn_worker_for(args, {"id": t.id})
    m._spawn_worker_for(args, {"id": t.id})  # dedup collision / re-run

    assert spawns == [t.id]  # spawned exactly once
    # the reservation is recorded as spawned
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


def test_create_spawn_failure_allows_retry(monkeypatch, q):
    """A failed spawn releases the reservation so a later run can retry."""
    t = q.create("work")
    calls: list[int] = []

    monkeypatch.setattr(m, "_client", lambda _args: _QueueBackedClient(q))

    def failing_do_spawn(_args, task):
        calls.append(1)
        return (types.SimpleNamespace(returncode=1), "fake", {"session": None, "worktree": None})

    monkeypatch.setattr(m, "_do_spawn", failing_do_spawn)
    args = types.SimpleNamespace(url=None, token=None)

    m._spawn_worker_for(args, {"id": t.id})
    assert q.latest_reservation(t.id).state == SpawnState.FAILED

    # a second run reserves a fresh attempt and spawns again
    m._spawn_worker_for(args, {"id": t.id})
    assert len(calls) == 2
    assert q.latest_reservation(t.id).attempt == 2
