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

    def create(self, title, *, repo=None, proposed=False, **kwargs):
        status = Status.PROPOSED if proposed else Status.QUEUED
        return asdict(
            self._q.create(title, repo=repo or TEST_REPO, status=status, **kwargs)
        )

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

    def yield_task(self, task_id, worker_id, *, note=None, exclude=None):
        return asdict(self._q.yield_task(task_id, worker_id, note=note, exclude=exclude))

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
    marked = q.create("marked", labels=["nightly-sweep"])
    q.create("unmarked")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, labels=["nightly-sweep"], max_concurrent=5
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
    t = q.create("work", labels=["code-review"])
    sup = Supervisor(
        client, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=1,
        label_max_attempts={"code-review": 3},
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
    t = q.create("work", labels=["nightly-scan"])
    sup = Supervisor(
        client, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=1,
        label_max_attempts={"code-review": 5},
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


def test_make_embody_spawn_records_handle_on_success(monkeypatch):
    """The CLI embody backend returns success + a parsed session/worktree handle
    when embody exits 0 (regression: it previously fell through to None, breaking
    record_spawn on the happy path)."""
    import subprocess

    from agent_dispatch import embody
    from agent_dispatch.supervisor import make_embody_spawn

    def fake_spawn_embodied_worker(
        task_id, *, coordinator_url, worker_id, driver, project=None, verify_timeout=0
    ):
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"worktree_id": "wt-9", "session_id": "sess-9"}', stderr="",
        )

    monkeypatch.setattr(embody, "spawn_embodied_worker", fake_spawn_embodied_worker)
    ok, handle = make_embody_spawn("http://coord")(
        {"id": "t", "repo": "gitea.example/org/widgets"}
    )
    assert ok is True
    assert handle["worktree"] == "wt-9"
    assert handle["session"] == "sess-9"


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

    spawn = make_headless_spawn("http://coord", agent="review-worker")
    ok, handle = spawn({"id": "task-1"})

    assert ok is True
    assert handle["worktree"] is None  # headless body is not a worktree
    assert calls["agent"] == "review-worker"
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

    marked = q.create("sweep work", labels=["nightly-scan"])
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
        headless_label=["nightly-scan"], headless_agent="task-worker",
    )
    assert m._cmd_supervise(args) == 0
    assert headless_calls == [marked.id]
    assert plain.id in embody_calls
    assert marked.id not in embody_calls


def test_cli_supervise_pool_headless_builds_headless_fleet(monkeypatch, q, client):
    """--pool --headless constructs a headless FleetSpawner with the configured
    --headless-agent (the headless-fleet embodiment for a remote pool host)."""
    import types

    from agent_dispatch import __main__ as m
    from agent_dispatch import fleet as fleet_mod
    from agent_dispatch import remote_dispatch

    q.create("work")
    captured: dict = {}

    class FakeFleet:
        def __init__(
            self, pool, *, origin, headless=False, agent="task-worker",
            verify_timeout=0,
        ):
            self.pool = list(pool)
            captured.update(
                pool=pool, origin=origin, headless=headless, agent=agent
            )

        def __call__(self, task):
            return True, {
                "session": "s", "worktree": None, "machine": "lc", "owner": "o",
            }

        def can_spawn(self, task):
            return True

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: client)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(fleet_mod, "FleetSpawner", FakeFleet)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "mantis-counter")

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=["review"],
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        pool="anomalous-potato-wsl", origin=None, headless=True,
        headless_label=None, headless_agent="review-worker",
    )
    assert m._cmd_supervise(args) == 0
    assert captured["headless"] is True
    assert captured["agent"] == "review-worker"
    assert captured["pool"] == ["anomalous-potato-wsl"]
    assert captured["origin"] == "mantis-counter"



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


def test_recover_gone_worktree_started_requeues_then_reembodies(q, client):
    """A worktree embody that died *while holding the task started* is requeued
    (yield on the gone owner's behalf, preserving goal + progress_log) AND its
    reservation released -- so re-embody is prompt, NOT lease-bound. Without the
    on-behalf yield the confirmed-gone owner's task would linger STARTED until the
    coordinator's lease-expiry GC requeued it (the liveness-not-lease gap this
    closes, matching the fleet/local body paths)."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        verdict_fn=lambda wt, mc, sid: "gone", nudge=False,
    )
    assert sup.poll_once() == [t.id]  # spawn #1 -> SPAWNED (worktree wt-1)
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    assert q.get(t.id).status == Status.STARTED  # leased, NOT requeued by GC

    # recover_gone: GONE -> yield (requeue) + release reservation -> re-embody,
    # all in one cycle, without waiting out the lease.
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
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


# -- reactive-on-turn-end (event-driven supervision) -------------------------


def _virtual_clock():
    """A deterministic (clock, sleep) pair: sleep advances the virtual clock."""
    t = [0.0]

    def clock() -> float:
        return t[0]

    def sleep(d: float) -> None:
        t[0] += d

    return clock, sleep


def _lease(q, task_id, *, machine="m", worktree="wt-1"):
    """Drive a spawned task to leased+started so it is an embodied owner."""
    q.claim_one(f"{machine}/{worktree}", task_id=task_id, machine=machine, worktree=worktree)
    q.start(task_id, f"{machine}/{worktree}")


def test_embodied_owners_only_leased_workers(q, client):
    """_embodied_owners lists a spawned reservation's worker only once its task is
    leased; a merely-queued (not yet claimed) spawn contributes nothing."""
    t = q.create("work")
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    assert sup._embodied_owners() == []  # spawned but not yet claimed -> no live turn
    _lease(q, t.id)
    assert sup._embodied_owners() == [("wt-1", "m")]


def test_wait_for_turn_end_wakes_on_running_to_idle(q, client):
    """A worker observed transitioning running -> idle settles a turn -> wake early."""
    t = q.create("work")
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    _lease(q, t.id)
    states = iter(["running", "idle"])  # baseline running, then idle
    sup.turn_state_fn = lambda wt, mc: next(states, "idle")
    clock, sleep = _virtual_clock()
    assert sup.wait_for_turn_end(30.0, sleep=sleep, clock=clock) is True


def test_wait_for_turn_end_times_out_without_transition(q, client):
    """A worker that stays running never wakes the wait -> False at timeout."""
    t = q.create("work")
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    _lease(q, t.id)
    sup.turn_state_fn = lambda wt, mc: "running"
    clock, sleep = _virtual_clock()
    assert sup.wait_for_turn_end(6.0, sleep=sleep, clock=clock) is False


def test_wait_for_turn_end_ignores_already_idle_worker(q, client):
    """A worker already idle at entry is not a fresh turn-end (no busy-wake)."""
    t = q.create("work")
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    _lease(q, t.id)
    sup.turn_state_fn = lambda wt, mc: "idle"  # idle at baseline and after
    clock, sleep = _virtual_clock()
    assert sup.wait_for_turn_end(6.0, sleep=sleep, clock=clock) is False


def test_wait_for_turn_end_no_workers_is_a_plain_sleep(q, client):
    """With nothing embodied, the wait is a single plain sleep of the full interval."""
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    slept: list[float] = []
    assert sup.wait_for_turn_end(30.0, sleep=slept.append, clock=lambda: 0.0) is False
    assert slept == [30.0]


def test_wait_for_turn_end_degrades_when_turn_state_unavailable(q, client):
    """A None turn state (no reachable bridge) yields no signal -> plain timeout."""
    t = q.create("work")
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    _lease(q, t.id)
    sup.turn_state_fn = lambda wt, mc: None
    clock, sleep = _virtual_clock()
    assert sup.wait_for_turn_end(6.0, sleep=sleep, clock=clock) is False


def test_wait_for_turn_end_swallows_probe_errors(q, client):
    """A raising turn-state resolver is treated as no signal, never fatal."""
    t = q.create("work")
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    _lease(q, t.id)

    def boom(wt, mc):
        raise RuntimeError("bridge down")

    sup.turn_state_fn = boom
    clock, sleep = _virtual_clock()
    assert sup.wait_for_turn_end(4.0, sleep=sleep, clock=clock) is False


def test_wait_for_turn_end_disabled_returns_immediately(q, client):
    """reactive=False makes the wait a no-op (serve falls back to a fixed sleep)."""
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5, reactive=False
    )
    slept: list[float] = []
    assert sup.wait_for_turn_end(30.0, sleep=slept.append, clock=lambda: 0.0) is False
    assert slept == []


# -- Slice 5: headless fleet-body recovery (confirmed-gone over SSH) ----------


def _fleet_spawn(handle_session):
    calls = []

    def spawn(task):
        calls.append(task["id"])
        return True, {"session": handle_session, "worktree": None}

    spawn.calls = calls  # type: ignore[attr-defined]
    return spawn


def test_parse_fleet_body_handle_decodes_host_and_session():
    from agent_dispatch.supervisor import _parse_fleet_body_handle

    assert _parse_fleet_body_handle("fleet-body:anomalous-potato-wsl:brg-9") == (
        "anomalous-potato-wsl", "brg-9",
    )
    # non-fleet handles (worktree embody, synthetic owner, empty) -> None
    assert _parse_fleet_body_handle("wt-1") is None
    assert _parse_fleet_body_handle("fleet-t1-abc123") is None
    assert _parse_fleet_body_handle(None) is None
    assert _parse_fleet_body_handle("fleet-body:onlyhost") is None


def test_recover_gone_fleet_body_releases_for_reembody(q, client):
    """A *confirmed-gone* headless fleet body (no worktree; a bridge-session
    recovery handle) is recovered via the fleet verdict probe: its reservation is
    released so the next cycle re-embodies it (resuming from progress_log)."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:anomalous-potato-wsl:brg-1")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "gone",
        # the worktree verdict path must NOT be consulted for a fleet handle:
        verdict_fn=lambda *a: "live",
        nudge=False,
    )
    assert sup.poll_once() == [t.id]  # spawn #1 -> SPAWNED w/ fleet-body handle
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED
    assert q.latest_reservation(t.id).session_handle == "fleet-body:anomalous-potato-wsl:brg-1"

    # next cycle: recover_gone sees the fleet body GONE -> releases -> re-embodies
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert q.latest_reservation(t.id).attempt == 2


def test_recover_gone_fleet_body_started_requeues_then_reembodies(q, client):
    """A fleet body that died *while holding the task started* is requeued (yield
    on the dead owner's behalf, preserving goal + progress_log) AND its reservation
    released -- so re-embody is prompt, not lease-bound."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-5")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "gone", nudge=False,
    )
    assert sup.poll_once() == [t.id]                 # spawn #1
    q.claim_one("fleet-o", task_id=t.id)             # body claimed + started it
    q.start(t.id, "fleet-o")
    assert q.get(t.id).status == Status.STARTED

    # recover_gone: GONE -> yield (requeue) + release reservation -> re-embody
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert q.latest_reservation(t.id).attempt == 2


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_recover_fleet_body_leaves_live_or_unknown(q, client, verdict):
    """A fleet body that is live or can't-tell is never recovered (no double-spawn
    of an alive body): the reservation is held, no re-spawn."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-2")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: verdict, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert sup.poll_once() == []  # held -> not re-spawned
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED
    assert q.latest_reservation(t.id).attempt == 1


def test_hold_live_leases_heartbeats_confirmed_live_fleet_body(q, client, monkeypatch):
    """A confirmed-live fleet body's origin lease is heartbeated so a live-but-quiet
    body isn't wrongly re-embodied (its lease can't expire under it)."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-3")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "live", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    # the fleet body claimed + started the task under its synthetic owner
    q.claim_one("fleet-o", task_id=t.id)
    q.start(t.id, "fleet-o")

    beats: list[tuple[str, str]] = []
    real_hb = client.heartbeat
    monkeypatch.setattr(
        client, "heartbeat",
        lambda tid, wid: beats.append((tid, wid)) or real_hb(tid, wid),
    )
    assert sup.hold_live_leases() == 1
    assert beats == [(t.id, "fleet-o")]


def test_hold_live_leases_skips_unknown_fleet_body(q, client, monkeypatch):
    """A can't-tell fleet body is NOT heartbeated (its lease rides its course; a
    genuinely-dead body's lease then expires for recovery)."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-4")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "unknown", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("fleet-o", task_id=t.id)
    q.start(t.id, "fleet-o")

    beats: list = []
    monkeypatch.setattr(client, "heartbeat", lambda tid, wid: beats.append((tid, wid)))
    assert sup.hold_live_leases() == 0
    assert beats == []


# -- local headless-body recovery (confirmed-gone on THIS host, no SSH) --------
#
# The local analog of the fleet-body slice above: a headless body embodied on
# this machine records a `local-body:<bridge-session-id>` recovery handle, so an
# ended/cancelled body is liveness-recovered instead of orphaning its `spawned`
# reservation and starving the label's concurrency slot (the #4433 fix).


def _local_spawn(handle_session):
    calls = []

    def spawn(task):
        calls.append(task["id"])
        return True, {"session": handle_session, "worktree": None}

    spawn.calls = calls  # type: ignore[attr-defined]
    return spawn


def test_parse_local_body_handle_decodes_session():
    from agent_dispatch.supervisor import _parse_local_body_handle

    assert _parse_local_body_handle("local-body:brg-9") == "brg-9"
    # non-local handles (worktree embody, fleet body, synthetic owner, empty)
    assert _parse_local_body_handle("wt-1") is None
    assert _parse_local_body_handle("fleet-body:h:brg-1") is None
    assert _parse_local_body_handle("headless-abc123") is None
    assert _parse_local_body_handle(None) is None
    assert _parse_local_body_handle("local-body:") is None


def test_recover_gone_local_body_releases_for_reembody(q, client):
    """A *confirmed-gone* local headless body (no worktree; a `local-body:` bridge
    handle) is recovered via the local verdict probe: its reservation is released
    so the next cycle re-embodies it -- freeing the slot instead of orphaning it."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-1")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "gone",
        # neither the fleet nor the worktree verdict path applies to a local handle:
        fleet_verdict_fn=lambda host, sid: "live",
        verdict_fn=lambda *a: "live",
        nudge=False,
    )
    assert sup.poll_once() == [t.id]  # spawn #1 -> SPAWNED w/ local-body handle
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED
    assert q.latest_reservation(t.id).session_handle == "local-body:brg-1"

    # next cycle: recover_gone sees the local body GONE -> releases -> re-embodies
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert q.latest_reservation(t.id).attempt == 2


def test_recover_gone_local_body_started_requeues_then_reembodies(q, client):
    """A local body that died *while holding the task started* is requeued (yield
    on its behalf, preserving goal + progress_log) AND its reservation released --
    so re-embody is prompt, not lease-bound."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-5")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "gone", nudge=False,
    )
    assert sup.poll_once() == [t.id]                 # spawn #1
    q.claim_one("local-o", task_id=t.id)             # body claimed + started it
    q.start(t.id, "local-o")
    assert q.get(t.id).status == Status.STARTED

    # recover_gone: GONE -> yield (requeue) + release reservation -> re-embody
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert q.latest_reservation(t.id).attempt == 2


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_recover_local_body_leaves_live_or_unknown(q, client, verdict):
    """A local body that is live or can't-tell is never recovered (no double-spawn
    of an alive body): the reservation is held, no re-spawn."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-2")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: verdict, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert sup.poll_once() == []  # held -> not re-spawned
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED
    assert q.latest_reservation(t.id).attempt == 1


def test_hold_live_leases_heartbeats_confirmed_live_local_body(q, client, monkeypatch):
    """A confirmed-live local body's lease is heartbeated so a live-but-quiet body
    isn't wrongly re-embodied (its lease can't expire under it)."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-3")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "live", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")

    beats: list[tuple[str, str]] = []
    real_hb = client.heartbeat
    monkeypatch.setattr(
        client, "heartbeat",
        lambda tid, wid: beats.append((tid, wid)) or real_hb(tid, wid),
    )
    assert sup.hold_live_leases() == 1
    assert beats == [(t.id, "local-o")]


def test_hold_live_leases_skips_unknown_local_body(q, client, monkeypatch):
    """A can't-tell local body is NOT heartbeated (its lease rides its course; a
    genuinely-dead body's lease then expires for recovery)."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-4")
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "unknown", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")

    beats: list = []
    monkeypatch.setattr(client, "heartbeat", lambda tid, wid: beats.append((tid, wid)))
    assert sup.hold_live_leases() == 0
    assert beats == []


# -- evaluator pass (service-driven loop advancement) -----------------------


def _spec_evaluator(rules):
    from agent_dispatch.producers.evaluator import SpecEvaluator

    return SpecEvaluator({"rules": rules})


_REVIEWER_DONE_RULE = {
    "on": "task.completed",
    "when": {"labels_any": ["recipe:reviewer"]},
    "emit": {
        "title_template": "unstick follow-up for {title}",
        "labels": ["recipe:conflict-resolution"],
        "dedup_template": "eval:conflict:{task_id}",
    },
}


def _complete(q, title, *, labels=None):
    t = q.create(title, labels=labels or [])
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1")
    return t


def _conflict_followups(q):
    return [
        t for t in q.list(repo=TEST_REPO, status="queued")
        if "recipe:conflict-resolution" in (t.labels or [])
    ]


def test_no_evaluator_pass_is_noop(q, client):
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    assert sup.advance_via_evaluator() == 0


def test_evaluator_emits_followup_on_completed(q, client):
    t = _complete(q, "review o/n#42", labels=["recipe:reviewer"])
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 1
    fus = _conflict_followups(q)
    assert len(fus) == 1
    assert fus[0].title == "unstick follow-up for review o/n#42"
    assert fus[0].dedup_key == f"eval:conflict:{t.id}"
    assert fus[0].source == "evaluator"


def test_evaluator_ignores_non_matching_terminal(q, client):
    _complete(q, "some other task", labels=["kind:misc"])  # no recipe:reviewer
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 0
    assert _conflict_followups(q) == []


def test_evaluator_fires_each_task_once_per_process(q, client):
    _complete(q, "review o/n#7", labels=["recipe:reviewer"])
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 1
    # second pass: the in-process guard skips the already-seen terminal task
    assert sup.advance_via_evaluator() == 0
    assert len(_conflict_followups(q)) == 1


def test_evaluator_dedup_guards_a_fresh_supervisor(q, client):
    _complete(q, "review o/n#9", labels=["recipe:reviewer"])
    ev = [_REVIEWER_DONE_RULE]
    Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO,
               evaluator=_spec_evaluator(ev)).advance_via_evaluator()
    # a brand-new supervisor (empty in-process guard) re-emits, but the emit's
    # dedup_key collides -> no duplicate row is created.
    Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO,
               evaluator=_spec_evaluator(ev)).advance_via_evaluator()
    assert len(_conflict_followups(q)) == 1


def test_evaluator_handles_abandoned_event(q, client):
    t = q.create("drive something", labels=["recipe:goal"])
    q.abandon(t.id, permitted=True, reason="withdrawn")
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([{
            "on": "task.abandoned",
            "emit": {"title_template": "re-carve {title}",
                     "dedup_template": "eval:recarve:{task_id}"},
        }]),
    )
    assert sup.advance_via_evaluator() == 1
    recarves = [t for t in q.list(repo=TEST_REPO, status="queued")
                if t.title == "re-carve drive something"]
    assert len(recarves) == 1


def test_evaluator_error_does_not_crash_the_cycle(q, client):
    _complete(q, "review o/n#11", labels=["recipe:reviewer"])

    class _Boom:
        def evaluate(self, event):
            raise RuntimeError("evaluator blew up")

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, evaluator=_Boom())
    # the pass swallows the error (returns 0) and still marks the task seen
    assert sup.advance_via_evaluator() == 0
    assert sup.advance_via_evaluator() == 0  # not retried into a crash loop


def test_poll_once_runs_the_evaluator_pass(q, client):
    _complete(q, "review o/n#13", labels=["recipe:reviewer"])
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    sup.poll_once()  # the evaluator pass runs inside poll_once
    assert len(_conflict_followups(q)) == 1
