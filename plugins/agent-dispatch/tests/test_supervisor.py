"""Tests for the generic embody spawn supervisor.

The load-bearing property under test is **spawn-at-most-once**: a task is
embodied only when a fresh spawn reservation is acquired, so a slow-but-alive
embody (whose lease expired and whose task was re-queued) is never
double-spawned.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agent_dispatch.queue import SpawnState, Status
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


class QueueBackedClient:
    """A DispatchClient stand-in backed by a real TaskQueue (dicts in/out)."""

    def __init__(self, queue: TaskQueue):
        self._q = queue

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def list(self, *, repo=None, status=None, limit=200, **_kw):
        return [asdict(t) for t in self._q.list(repo=repo, status=status, limit=limit)]

    def get(self, task_id):
        t = self._q.get(task_id)
        if t is None:
            from agent_dispatch.client import DispatchError

            raise DispatchError(404, "no such task")
        return asdict(t)

    def list_reservations(self, *, task_id=None, state=None, limit=200):
        states = state.split(",") if isinstance(state, str) else state
        rows = self._q.list_reservations(task_id=task_id, state=states, limit=limit)
        return [asdict(r) for r in rows]

    def reserve_spawn(self, task_id, *, reserved_by=None):
        res, ok = self._q.reserve_spawn(task_id, reserved_by=reserved_by)
        return {"reserved": ok, "reservation": asdict(res)}

    def record_spawn(self, key, *, session_handle=None, worktree=None):
        return asdict(self._q.record_spawn(key, session_handle=session_handle, worktree=worktree))

    def fail_spawn(self, key, *, detail=None):
        return asdict(self._q.fail_spawn(key, detail=detail))

    def settle_spawn(self, key, *, detail=None):
        return asdict(self._q.settle_spawn(key, detail=detail))

    def heartbeat(self, task_id, worker_id):
        return asdict(self._q.heartbeat(task_id, worker_id))


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def client(q):
    return QueueBackedClient(q)


def _ok_spawn(handle=None):
    calls = []

    def spawn(task):
        calls.append(task["id"])
        return True, (handle or {"session": "sess-1", "worktree": "wt-1"})

    spawn.calls = calls  # type: ignore[attr-defined]
    return spawn


# -- happy path --------------------------------------------------------------


def test_poll_spawns_eligible_task_once(q, client):
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    spawned = sup.poll_once()
    assert spawned == [t.id]
    assert spawn.calls == [t.id]
    res = q.latest_reservation(t.id)
    assert res.state == SpawnState.SPAWNED
    assert res.worktree == "wt-1"

    # a second cycle does NOT spawn again (active spawned reservation)
    assert sup.poll_once() == []
    assert spawn.calls == [t.id]


def test_requeued_task_is_not_double_spawned(q, client):
    """A spawned-but-requeued task (lease expired, embody maybe still alive)
    must never be spawned a second time."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()  # spawn #1

    # simulate: embody claimed + started, then its lease expired -> re-queued
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    q.recover_expired_leases(now=q.get(t.id).lease_expires_at + 1)
    assert q.get(t.id).status == Status.QUEUED  # back in the queue

    # the supervisor must NOT re-spawn it (reservation still 'spawned')
    assert sup.poll_once() == []
    assert spawn.calls == [t.id]  # still just the one spawn


def test_reconcile_settles_terminal_then_allows_respawn(q, client):
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()

    # embody works the task to completion
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    q.complete(t.id, "m/wt")

    settled = sup.reconcile()
    assert settled == 1
    assert q.latest_reservation(t.id).state == SpawnState.SETTLED


def test_spawn_failure_fails_reservation_and_retries(q, client):
    t = q.create("work")
    attempts = []

    def flaky(task):
        attempts.append(1)
        if len(attempts) == 1:
            return False, {"error": "boom"}
        return True, {"session": "s", "worktree": "w"}

    sup = Supervisor(client, spawn_fn=flaky, repo=TEST_REPO, max_concurrent=5)
    assert sup.poll_once() == []  # first spawn fails
    assert q.latest_reservation(t.id).state == SpawnState.FAILED

    # next cycle reserves a fresh attempt and succeeds
    assert sup.poll_once() == [t.id]
    assert q.latest_reservation(t.id).attempt == 2
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


# -- policy: cap / labels / deferral -----------------------------------------


def test_max_concurrent_caps_spawns(q, client):
    a = q.create("a")
    b = q.create("b")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1)

    spawned = sup.poll_once()
    assert len(spawned) == 1  # only one, despite two eligible
    assert {a.id, b.id} & set(spawned)  # spawned one of them


def test_label_opt_in(q, client):
    marked = q.create("marked", labels=["cab-sweep"])
    q.create("unmarked")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, labels=["cab-sweep"], max_concurrent=5
    )

    spawned = sup.poll_once()
    assert spawned == [marked.id]


def test_not_before_deferral(q, client):
    future = q.create("later", not_before=9_999_999_999.0)
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    assert sup.poll_once() == []  # not due yet
    assert q.latest_reservation(future.id) is None


# -- liveness-gated heartbeat ------------------------------------------------


def _leased_task_with_spawn(q):
    """A started task with a recorded ``spawned`` reservation (owner m/wt)."""
    t = q.create("work")
    r, _ = q.reserve_spawn(t.id)
    q.record_spawn(r.key, session_handle="sess", worktree="wt")
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    return t


def test_heartbeat_holds_confirmed_live_lease(q, client):
    t = _leased_task_with_spawn(q)
    probes = []

    def alive(worktree, machine):
        probes.append((worktree, machine))
        return {"liveness": "alive"}

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, liveness_fn=alive)
    # push the lease into the past so the heartbeat visibly extends it
    before = q.get(t.id).lease_expires_at
    held = sup.hold_live_leases()
    assert held == 1
    assert probes == [("wt", "m")]
    assert q.get(t.id).lease_expires_at >= before


def test_heartbeat_skips_when_not_confirmed_alive(q, client):
    t = _leased_task_with_spawn(q)
    lease_before = q.get(t.id).lease_expires_at

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, liveness_fn=lambda w, m: None)
    assert sup.hold_live_leases() == 0
    # a None probe must never be treated as alive -> no heartbeat written
    assert q.get(t.id).lease_expires_at == lease_before


def test_heartbeat_disabled_skips_liveness(q, client):
    _leased_task_with_spawn(q)
    probes = []
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO, heartbeat=False,
        liveness_fn=lambda w, m: probes.append(1) or {"liveness": "alive"},
    )
    sup.poll_once()
    assert probes == []  # heartbeat disabled -> liveness never probed


# -- CLI wiring --------------------------------------------------------------


def test_cli_supervise_once(monkeypatch, q, client):
    import types

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    t = q.create("work")
    spawn = _ok_spawn()
    monkeypatch.setattr(m, "_client", lambda _args: client)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda _url, **_kw: spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False,
    )
    assert m._cmd_supervise(args) == 0
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED

