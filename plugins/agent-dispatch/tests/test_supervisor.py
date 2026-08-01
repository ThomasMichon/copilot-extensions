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

    def progress_log(self, task_id):
        return self._q.progress_log(task_id)


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

    # simulate: embody claimed + started, then its worker went away -> re-queued
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    q.reconcile_liveness(lambda wt, mc, sid: "gone")
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


def test_dead_letter_after_max_attempts(q, client):
    """A task that keeps failing to spawn is dead-lettered (not retried forever)."""
    t = q.create("work")

    def always_fail(task):
        return False, {"error": "boom"}

    sup = Supervisor(
        client, spawn_fn=always_fail, repo=TEST_REPO, max_concurrent=5, max_attempts=3
    )
    # three cycles each burn one attempt (fail_spawn), then it's dead-lettered
    for _ in range(3):
        assert sup.poll_once() == []
    assert len(q.list_reservations(task_id=t.id, state="failed")) == 3

    # a fourth cycle must NOT reserve a 4th attempt
    assert sup.poll_once() == []
    assert q.latest_reservation(t.id).attempt == 3  # still only 3 attempts made


def test_max_attempts_zero_retries_forever(q, client):
    t = q.create("work")
    sup = Supervisor(
        client, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=0,
    )
    for _ in range(5):
        sup.poll_once()
    assert q.latest_reservation(t.id).attempt == 5  # unbounded retries


def test_label_max_attempts_raises_one_labels_bound(q, client):
    """A per-label override raises one label's dead-letter bound above the
    global default (#3492) -- so its tasks retry longer, independently."""
    t = q.create("work", labels=["intelligence-dampener"])
    sup = Supervisor(
        client, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=1,
        label_max_attempts={"intelligence-dampener": 3},
    )
    # Global bound is 1, but the label override is 3: it retries up to 3.
    for _ in range(3):
        assert sup.poll_once() == []
    assert len(q.list_reservations(task_id=t.id, state="failed")) == 3
    # A fourth cycle must NOT reserve a 4th attempt -- dead-lettered at 3.
    assert sup.poll_once() == []
    assert q.latest_reservation(t.id).attempt == 3


def test_label_max_attempts_leaves_other_labels_on_global_bound(q, client):
    """A label with no override still uses the global bound -- reviving one
    label's tasks does not revive another's (the decoupling #3492 exists for)."""
    t = q.create("work", labels=["coherence-adjudication-board"])
    sup = Supervisor(
        client, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=1,
        label_max_attempts={"intelligence-dampener": 5},
    )
    assert sup.poll_once() == []  # 1 attempt burned
    assert sup.poll_once() == []  # dead-lettered at the global bound of 1
    assert q.latest_reservation(t.id).attempt == 1


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
    monkeypatch.setattr(m, "_client", lambda _args, **_kw: client)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda _url, **_kw: spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
    )
    assert m._cmd_supervise(args) == 0
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


# -- headless-ACP embody backend ---------------------------------------------


def test_make_headless_spawn_uses_bridge_with_autopilot_seed(monkeypatch):
    """The headless backend embodies via agent-bridge, delivering the SAME
    autopilot seed the CLI backend uses (parity: identical driving, different
    body) -- and records no worktree handle (a headless body is not a worktree)."""
    import subprocess

    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    calls: dict = {}

    def fake_spawn_worker(
        task_id, *, agent, coordinator_url, worker_id, prompt, wait, **_kw
    ):
        calls.update(
            task_id=task_id, agent=agent, coordinator_url=coordinator_url,
            worker_id=worker_id, prompt=prompt, wait=wait,
        )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bridge, "spawn_worker", fake_spawn_worker)
    monkeypatch.setattr(
        embody, "autopilot_worker_prompt",
        lambda task_id, *, coordinator_url, worker_id: f"SEED::{task_id}",
    )

    spawn = make_headless_spawn("http://coord", agent="board-worker")
    ok, handle = spawn({"id": "task-1"})

    assert ok is True
    assert handle["worktree"] is None  # headless body is not a worktree
    assert calls["agent"] == "board-worker"
    assert calls["prompt"] == "SEED::task-1"  # the CLI autopilot seed, verbatim
    assert calls["wait"] is False  # fire-and-forget; the worker drives itself


def test_make_headless_spawn_reports_failure_on_nonzero(monkeypatch):
    import subprocess

    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    monkeypatch.setattr(embody, "autopilot_worker_prompt", lambda *a, **k: "seed")
    monkeypatch.setattr(
        bridge, "spawn_worker",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "", "boom"),
    )
    ok, handle = make_headless_spawn("http://coord")({"id": "t"})
    assert ok is False
    assert "boom" in handle["error"]


def test_make_headless_spawn_degrades_when_bridge_absent(monkeypatch):
    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    monkeypatch.setattr(embody, "autopilot_worker_prompt", lambda *a, **k: "seed")

    def _boom(*a, **k):
        raise bridge.BridgeUnavailable("no agent-bridge on PATH")

    monkeypatch.setattr(bridge, "spawn_worker", _boom)
    ok, handle = make_headless_spawn("http://coord")({"id": "t"})
    assert ok is False
    assert "agent-bridge" in handle["error"]


def test_make_label_routed_spawn_routes_by_label():
    from agent_dispatch.supervisor import make_label_routed_spawn

    def default(_task):
        return True, {"session": "cli", "worktree": "wt"}

    def headless(_task):
        return True, {"session": "headless", "worktree": None}

    routed = make_label_routed_spawn(default, overrides={"sweep": headless})

    assert routed({"id": "a", "labels": ["sweep"]})[1]["session"] == "headless"
    assert routed({"id": "b", "labels": ["other"]})[1]["session"] == "cli"
    assert routed({"id": "c", "labels": []})[1]["session"] == "cli"
    assert routed({"id": "d"})[1]["session"] == "cli"  # no labels key


def test_make_label_routed_spawn_no_overrides_returns_default_unwrapped():
    from agent_dispatch.supervisor import make_label_routed_spawn

    def default(_task):
        return True, {}

    assert make_label_routed_spawn(default, overrides={}) is default


def test_cli_supervise_headless_label_routes(monkeypatch, q, client):
    """--headless-label routes a marked task to the headless backend while an
    unmarked task stays CLI-first in the same supervisor (per-label body)."""
    import types

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    marked = q.create("sweep work", labels=["board-sweep"])
    plain = q.create("interactive work")

    embody_calls: list[str] = []
    headless_calls: list[str] = []

    def embody_spawn(task):
        embody_calls.append(task["id"])
        return True, {"session": "cli", "worktree": "wt"}

    def headless_spawn(task):
        headless_calls.append(task["id"])
        return True, {"session": "headless", "worktree": None}

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: client)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda _url, **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda _url, **_kw: headless_spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        headless_label=["board-sweep"], headless_agent="task-worker",
    )
    assert m._cmd_supervise(args) == 0
    assert headless_calls == [marked.id]
    assert plain.id in embody_calls
    assert marked.id not in embody_calls



# -- Slice 2: liveness-gated auto-recovery -----------------------------------


def test_recover_gone_releases_stale_reservation_and_respawns(q, client):
    """A *confirmed-gone* embody's stale reservation is released so the task is
    re-embodied (the replacement resumes from progress_log)."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        verdict_fn=lambda wt, mc, sid: "gone",
    )
    sup.poll_once()  # spawn #1 -> reservation SPAWNED
    assert spawn.calls == [t.id]

    # embody claimed + started, then its worker vanished -> coordinator GC requeues
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.reconcile_liveness(lambda wt, mc, sid: "gone")
    assert q.get(t.id).status == Status.QUEUED

    # next cycle: recover_gone releases the stale reservation, then re-embodies
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]  # re-spawned
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED
    assert q.latest_reservation(t.id).attempt == 2


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_recover_leaves_live_or_unknown(q, client, verdict):
    """Recovery never fires on a live or can't-tell verdict (the safety guarantee
    behind liveness-not-lease): the reservation is held, no re-spawn."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        verdict_fn=lambda *_: verdict,
    )
    sup.poll_once()  # spawn #1
    assert spawn.calls == [t.id]

    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.reconcile_liveness(lambda wt, mc, sid: "gone")  # queue requeues
    assert q.get(t.id).status == Status.QUEUED

    # supervisor's OWN verdict is not 'gone' -> hold, never re-spawn on ignorance
    assert sup.poll_once() == []
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


def test_recover_disabled_holds_for_human(q, client):
    """recover=False restores the old hold-for-a-human default even on a gone
    verdict."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        recover=False, verdict_fn=lambda *_: "gone",
    )
    sup.poll_once()  # spawn #1
    assert sup.poll_once() == []  # gone, but recovery disabled -> no re-spawn
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


# -- Slice 2: completion-claim verification ----------------------------------


def test_completion_verify_flags_empty_goal_completion(q, client):
    """A goal-bearing task completed with no result-ref and no progress is
    flagged in the reservation detail (held for review), not silently accepted."""
    t = q.create("goal work", goal="reach X", done_criteria="X is done")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1")  # no result-ref, no progress
    assert sup.reconcile() == 1
    res = q.latest_reservation(t.id)
    assert res.state == SpawnState.SETTLED
    assert "UNVERIFIED" in (res.detail or "")


def test_completion_verify_accepts_goal_with_progress(q, client):
    """A goal completed after recording progress verifies cleanly."""
    t = q.create("goal work", goal="reach X", done_criteria="X is done")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.record_progress(t.id, "m/wt-1", phase="p1", summary="did a unit of work")
    q.complete(t.id, "m/wt-1")
    assert sup.reconcile() == 1
    res = q.latest_reservation(t.id)
    assert res.state == SpawnState.SETTLED
    assert "UNVERIFIED" not in (res.detail or "")
    assert "progress" in (res.detail or "")


def test_completion_verify_ignores_one_shot_task(q, client):
    """A plain one-shot task (no goal) is never flagged, even with no evidence."""
    t = q.create("plain work")  # no goal
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1")
    sup.reconcile()
    assert "UNVERIFIED" not in (q.latest_reservation(t.id).detail or "")


# -- Slice 2: nudge-before-recover (stalled-but-live) ------------------------


def test_nudge_stalled_live_worker_once(q, client):
    """A confirmed-alive worker with no progress within the window is nudged
    once (cooldown prevents re-nudging the same window)."""
    t = q.create("work")
    spawn = _ok_spawn()
    sent = []
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        stall_seconds=1.0,
        liveness_fn=lambda wt, mc: {"session": "s"},  # confirmed alive
        nudge_fn=lambda wt, mc, task: (sent.append(task["id"]) or True),
    )
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    future = (q.get(t.id).started_at or 0) + 100
    assert sup.nudge_stalled(now=future) == 1
    assert sent == [t.id]
    # cooldown: another check within the window does not re-nudge
    assert sup.nudge_stalled(now=future + 0.5) == 0


def test_nudge_skips_when_not_confirmed_alive(q, client):
    """A worker that is not confirmed alive is left to recovery, never nudged."""
    t = q.create("work")
    spawn = _ok_spawn()
    sent = []
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        stall_seconds=1.0,
        liveness_fn=lambda wt, mc: None,  # not confirmed alive
        nudge_fn=lambda wt, mc, task: (sent.append(1) or True),
    )
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    assert sup.nudge_stalled(now=(q.get(t.id).started_at or 0) + 100) == 0
    assert sent == []
