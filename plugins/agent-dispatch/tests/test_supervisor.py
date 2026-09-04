"""Tests for the generic embody spawn supervisor.

The load-bearing property under test is **spawn-at-most-once**: a task is
embodied only when a fresh spawn reservation is acquired, so a slow-but-alive
embody (whose lease expired and whose task was re-queued) is never
double-spawned.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from agent_dispatch import supervisor as supervisor_module
from agent_dispatch import tracking
from agent_dispatch.client import DispatchError
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

    def list(self, *, repo=None, status=None, limit=200, **kw):
        return [
            asdict(t)
            for t in self._q.list(
                repo=repo, status=status, limit=limit, **kw
            )
        ]

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

    def list_reservations(
        self,
        *,
        task_id=None,
        state=None,
        repo=None,
        label=None,
        conclusion_state=None,
        resume_requested=None,
        limit=200,
    ):
        states = state.split(",") if isinstance(state, str) else state
        rows = self._q.list_reservations(
            task_id=task_id,
            state=states,
            repo=repo,
            label=label,
            conclusion_state=conclusion_state,
            resume_requested=resume_requested,
            limit=limit,
        )
        return [asdict(r) for r in rows]

    def reserve_spawn(self, task_id, *, reserved_by=None):
        res, ok = self._q.reserve_spawn(task_id, reserved_by=reserved_by)
        return {"reserved": ok, "reservation": asdict(res)}

    def record_spawn(self, key, *, session_handle=None, worktree=None):
        return asdict(self._q.record_spawn(key, session_handle=session_handle, worktree=worktree))

    def record_spawn_worktree(self, key, worktree, **kwargs):
        return asdict(
            self._q.record_spawn_worktree(key, worktree, **kwargs)
        )

    def fail_spawn(self, key, *, detail=None):
        return asdict(self._q.fail_spawn(key, detail=detail))

    def request_spawn_release(
        self, key, *, detail=None, disposition="failed"
    ):
        return asdict(
            self._q.request_spawn_release(
                key,
                detail=detail,
                disposition=disposition,
            )
        )

    def record_cold(self, key):
        return asdict(self._q.record_cold(key))

    def settle_spawn(
        self,
        key,
        *,
        detail=None,
        conclusion_state=None,
        conclusion_detail=None,
    ):
        return asdict(
            self._q.settle_spawn(
                key,
                detail=detail,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
            )
        )

    def record_spawn_conclusion(
        self,
        key,
        *,
        conclusion_state,
        conclusion_detail,
    ):
        return asdict(
            self._q.record_spawn_conclusion(
                key,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
            )
        )

    def heartbeat(self, task_id, worker_id):
        return asdict(self._q.heartbeat(task_id, worker_id))

    def set_activity(self, task_id, activity, *, reservation_key):
        return asdict(
            self._q.set_activity(
                task_id, activity, reservation_key=reservation_key
            )
        )

    def bind_owner_session(
        self,
        task_id,
        worker_id,
        owner_session_id,
        *,
        expected_generation=None,
    ):
        return asdict(
            self._q.bind_owner_session(
                task_id,
                worker_id,
                owner_session_id,
                expected_generation=expected_generation,
            )
        )

    def yield_task(
        self,
        task_id,
        worker_id,
        *,
        note=None,
        exclude=None,
        release_spawn=True,
    ):
        return asdict(
            self._q.yield_task(
                task_id,
                worker_id,
                note=note,
                exclude=exclude,
                release_spawn=release_spawn,
            )
        )

    def release(self, task_id, worker_id, *, reason=None):
        return asdict(
            self._q.release_suspended(
                task_id, worker_id, reason=reason
            )
        )

    def suspend(self, task_id, worker_id, *, reason):
        return asdict(self._q.suspend(task_id, worker_id, reason=reason))

    def resume(
        self,
        task_id,
        worker_id,
        *,
        wake=True,
        message=None,
        adopt_session=False,
        reuse_session=False,
        expected_owner_session_id=None,
        expected_generation=None,
    ):
        return asdict(
            self._q.resume(
                task_id,
                worker_id,
                wake_requested=wake,
                wake_message=message,
                adopt_owner_session_id=(
                    expected_owner_session_id if adopt_session else None
                ),
                reuse_session=reuse_session,
                expected_owner_session_id=expected_owner_session_id,
                expected_generation=expected_generation,
            )
        )

    def progress_log(self, task_id):
        return self._q.progress_log(task_id)


def test_default_liveness_uses_local_bridge_for_local_machine(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tracking.remote_dispatch, "is_peer_machine", lambda machine: False
    )
    monkeypatch.setattr(
        tracking,
        "resolve_live_session",
        lambda worktree, *, machine=None: calls.append((worktree, machine)) or {},
    )

    supervisor_module._default_liveness("wt-local", "LOCAL-HOST")

    assert calls == [("wt-local", None)]


def test_default_liveness_uses_ssh_for_peer_machine(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tracking.remote_dispatch, "is_peer_machine", lambda machine: True
    )
    monkeypatch.setattr(
        tracking,
        "resolve_live_session",
        lambda worktree, *, machine=None: calls.append((worktree, machine)) or {},
    )

    supervisor_module._default_liveness("wt-peer", "peer-host")

    assert calls == [("wt-peer", "peer-host")]


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


def test_protected_label_pool_does_not_claim_unlabeled_task(q, client):
    q.handoff_producer_scope(
        TEST_REPO,
        "scheduled",
        producer_id="scheduler-a",
        expected_generation=0,
        required_label="board",
    )
    ordinary = q.create("ordinary", source="manual")
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["board"],
        max_concurrent=5,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.get(ordinary.id).status == Status.QUEUED


def test_suspended_reservation_does_not_consume_supervisor_capacity(q, client):
    first = q.create("dormant")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1
    )
    assert sup.poll_once() == [first.id]
    q.claim_one("host-a/wt-1", task_id=first.id)
    q.start(first.id, "host-a/wt-1")
    q.suspend(first.id, "host-a/wt-1", reason="waiting")
    second = q.create("runnable")

    assert sup.poll_once() == [second.id]
    assert spawn.calls == [first.id, second.id]
    assert q.get(first.id).status == Status.SUSPENDED
    assert q.get(first.id).attempts == 1


def test_live_releasing_reservation_consumes_supervisor_capacity(q, client):
    first = q.create("releasing")
    reservation, _ = q.reserve_spawn(first.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-live",
        worktree="wt-created",
    )
    q.request_spawn_release(reservation.key, disposition="failed")
    second = q.create("runnable")
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=1,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "ACTIVE",
        local_acp_session_fn=lambda _sid: "acp-live",
        local_end_fn=lambda _sid: False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.latest_reservation(first.id).state == SpawnState.RELEASING
    assert q.latest_reservation(second.id) is None


def test_other_pool_reservation_does_not_consume_process_capacity(q, client):
    other = q.create("other pool", repo="github.com/example/other")
    reservation, _ = q.reserve_spawn(other.id)
    q.record_spawn(reservation.key, session_handle="local-body:other-session")
    runnable = q.create("this pool")
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1
    )

    assert sup.poll_once() == [runnable.id]


def test_other_label_reservations_are_not_probed(q, client):
    held = q.create("other held task", labels=["other"])
    held_reservation, _ = q.reserve_spawn(held.id)
    q.record_spawn(
        held_reservation.key,
        session_handle="other-session",
        worktree="other-worktree",
    )
    owner = "other-machine/other-worktree"
    q.claim_one(owner, task_id=held.id)
    q.start(held.id, owner)

    unclaimed = q.create("other unclaimed task", labels=["other"])
    queued_reservation, _ = q.reserve_spawn(unclaimed.id)
    q.record_spawn(
        queued_reservation.key,
        session_handle="queued-session",
        worktree="queued-worktree",
    )

    probes: list[tuple[str, str]] = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        stall_seconds=1,
        liveness_fn=lambda worktree, _machine: (
            probes.append(("live", worktree)) or {"session_id": "session"}
        ),
        verdict_fn=lambda worktree, _machine, _session: (
            probes.append(("verdict", worktree)) or tracking.LIVE
        ),
    )

    assert sup.poll_once(now=held.created_at + 10) == []
    assert probes == []


def test_suspended_local_headless_body_is_cooled(q, client):
    task = q.create("dormant review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:bridge-session-1"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    q.suspend(task.id, "headless-owner", reason="waiting for author")
    stopped = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=lambda session_id: stopped.append(session_id) or True,
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.cool_dormant_bodies() == 1
    assert stopped == ["bridge-session-1"]
    assert q.get_reservation(reservation.key).state == SpawnState.COLD
    assert sup.cool_dormant_bodies() == 0


def test_cli_suspensions_do_not_consume_headless_cooling_budget(q, client):
    for index in range(12):
        task = q.create(f"cli dormant {index}", labels=["review"])
        reservation, _ = q.reserve_spawn(task.id)
        q.record_spawn(
            reservation.key,
            session_handle=f"cli-session-{index}",
            worktree=f"wt-{index}",
        )
        q.claim_one(f"machine/wt-{index}", task_id=task.id)
        q.start(task.id, f"machine/wt-{index}")
        q.suspend(task.id, f"machine/wt-{index}", reason="waiting")
    headless = q.create("headless dormant", labels=["review"])
    reservation, _ = q.reserve_spawn(headless.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:headless-session"
    )
    q.claim_one("headless-owner", task_id=headless.id)
    q.start(headless.id, "headless-owner")
    q.suspend(headless.id, "headless-owner", reason="waiting")
    stopped = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=lambda session_id: stopped.append(session_id) or True,
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.cool_dormant_bodies() == 1
    assert stopped == ["headless-session"]


def test_blocking_card_cools_body_and_frees_process_capacity(q, client):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.set_card(
        blocked.id,
        "headless-owner",
        card={"request_input": [{"name": "decision", "type": "text"}]},
    )
    runnable = q.create("next review", labels=["review"])
    stopped = []
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        max_concurrent=1,
        local_cold_fn=lambda session_id: stopped.append(session_id) or True,
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == [runnable.id]
    assert stopped == ["blocked-session"]
    assert q.get(blocked.id).status == Status.SUSPENDED
    assert q.get(blocked.id).awaiting_steer is True


def test_cold_steer_resumes_existing_acp_session(q, client):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    steered = q.submit_steer(
        blocked.id,
        fields={"decision": "continue"},
        sender="operator",
    )
    assert steered.status == Status.SUSPENDED
    assert steered.resume_requested is True
    assert q.get_reservation(reservation.key).state == SpawnState.COLD
    spawn = _ok_spawn()
    resumed = []
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "ACTIVE",
        local_body_verdict_fn=lambda _session_id: "live",
        local_resume_fn=lambda session_id, prompt: (
            resumed.append((session_id, prompt)) or True
        ),
    )
    sup._cooled_reservations.add(reservation.key)

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert resumed and resumed[0][0] == "blocked-session"
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
    assert q.get(blocked.id).status == Status.STARTED
    assert q.get(blocked.id).resume_requested is False
    assert reservation.key not in sup._cooled_reservations


def test_cold_resume_does_not_start_process_before_reservation_transition(
    q, client, monkeypatch
):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")
    resumed: list[str] = []

    def fail_record_spawn(*_args, **_kwargs):
        raise DispatchError(500, "write failed")

    monkeypatch.setattr(
        client,
        "record_spawn",
        fail_record_spawn,
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_resume_fn=lambda sid, _prompt: resumed.append(sid) or True,
    )

    assert sup.release_resumed_cold_tasks() == 0
    assert resumed == []
    assert q.get_reservation(reservation.key).state == SpawnState.COLD
    assert q.get(blocked.id).status == Status.SUSPENDED


def test_cold_resume_task_failure_is_recoolable(q, client, monkeypatch):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")

    def fail_resume(*_args, **_kwargs):
        raise DispatchError(500, "resume failed")

    monkeypatch.setattr(
        client,
        "resume",
        fail_resume,
    )
    stopped: list[str] = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_resume_fn=lambda _sid, _prompt: True,
        local_cold_fn=lambda sid: stopped.append(sid) or True,
    )

    assert sup.release_resumed_cold_tasks() == 0
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
    assert q.get(blocked.id).status == Status.SUSPENDED
    assert sup.cool_dormant_bodies() == 1
    assert stopped == ["blocked-session"]
    assert q.get_reservation(reservation.key).state == SpawnState.COLD


def test_cold_resume_process_exception_is_recoolable(q, client):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")

    def fail_process_resume(_sid, _prompt):
        raise OSError("bridge unavailable")

    stopped: list[str] = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_resume_fn=fail_process_resume,
        local_cold_fn=lambda sid: stopped.append(sid) or True,
    )

    assert sup.release_resumed_cold_tasks() == 0
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
    assert q.get(blocked.id).status == Status.SUSPENDED
    assert sup.cool_dormant_bodies() == 1
    assert stopped == ["blocked-session"]
    assert q.get_reservation(reservation.key).state == SpawnState.COLD


def test_cold_resume_process_failure_backs_off_before_retry(q, client):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")
    attempts: list[str] = []

    def fail_process_resume(sid, _prompt):
        attempts.append(sid)
        raise OSError("bridge unavailable")

    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_resume_fn=fail_process_resume,
        local_cold_fn=lambda _sid: True,
    )

    assert sup.release_resumed_cold_tasks(now=1000) == 0
    assert sup.cool_dormant_bodies() == 1
    assert sup.release_resumed_cold_tasks(now=1299) == 0
    assert attempts == ["blocked-session"]
    assert sup.release_resumed_cold_tasks(now=1300) == 0
    assert attempts == ["blocked-session", "blocked-session"]


def test_idle_headless_turn_auto_suspends_and_cools(q, client):
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:review-session"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    stopped = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "IDLE",
        local_cold_fn=lambda session_id: stopped.append(session_id) or True,
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == []
    current = q.get(task.id)
    assert current.status == Status.SUSPENDED
    assert current.activity == "IDLE"
    assert q.get_reservation(reservation.key).state == SpawnState.COLD
    assert stopped == ["review-session"]


def test_supervisor_binds_headless_owner_to_bridge_session(q, client):
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:review-session"
    )
    claimed = q.claim_one("headless-owner", task_id=task.id)
    assert claimed is not None
    q.start(task.id, "headless-owner")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "ACTIVE",
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.bind_headless_owner_sessions() == 1
    assert q.get(task.id).owner_session_id == "review-session"
    assert sup.bind_headless_owner_sessions() == 0


def test_failed_cold_stop_keeps_live_process_capacity(q, client):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.set_card(
        blocked.id,
        "headless-owner",
        card={"request_input": [{"name": "decision", "type": "text"}]},
    )
    runnable = q.create("next review", labels=["review"])
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        max_concurrent=1,
        local_cold_fn=lambda _session_id: False,
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.get(runnable.id).status == Status.QUEUED


def test_cold_stop_exception_does_not_abort_cycle(q, client):
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="waiting")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=lambda _session_id: (_ for _ in ()).throw(
            TimeoutError("stop timed out")
        ),
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == []


def test_supervisor_settles_suspended_task_completed_by_resolver(q, client):
    task = q.create("wait for condition")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="session-1", worktree="wt-1"
    )
    q.claim_one("host-a/wt-1", task_id=task.id)
    q.start(task.id, "host-a/wt-1")
    q.suspend(task.id, "host-a/wt-1", reason="condition pending")
    q.complete(
        task.id, "host-a/wt-1", result_ref="condition:satisfied"
    )
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=1
    )

    assert sup.reconcile() == 1
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED


def test_supervisor_settles_terminal_cold_reservation(q, client):
    task = q.create("wait for condition", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:session-1"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    q.suspend(task.id, "headless-owner", reason="condition pending")
    q.record_cold(reservation.key)
    q.complete(
        task.id, "headless-owner", result_ref="condition:satisfied"
    )
    ended: list[str] = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda sid: ended.append(sid) or True,
    )

    assert sup.reconcile() == 1
    assert ended == ["session-1"]
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED


def test_terminal_conclusion_uses_exact_reservation_identity(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one(
        "host-a/worktree-exact",
        task_id=task.id,
        machine="host-a",
        worktree="worktree-exact",
    )
    q.start(task.id, "host-a/worktree-exact")
    q.complete(
        task.id,
        "host-a/worktree-exact",
        result_ref="review:complete",
    )
    calls = []

    def conclude(worktree, session):
        calls.append((worktree, session))
        return {"action": "primed", "reason": "managed-gc-candidate"}

    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        disposable_cli_labels=["review"],
        conclusion_fn=conclude,
    )

    assert sup.reconcile() == 1
    assert calls == [("worktree-exact", "session-exact")]
    settled = q.get_reservation(reservation.key)
    assert settled.state == SpawnState.SETTLED
    assert "terminal conclusion primed" in (settled.detail or "")
    assert settled.conclusion_state == "complete"


def test_terminal_refresh_does_not_replace_exact_reserved_session(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="original-session",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        liveness_fn=lambda *_args: {
            "session_id": "successor-session",
            "worktree_id": "worktree-exact",
        },
        conclusion_fn=lambda *args: calls.append(args) or {
            "action": "skipped",
            "reason": "session-mismatch",
        },
    )

    assert sup.reconcile() == 1
    assert calls == [("worktree-exact", "original-session")]
    settled = q.get_reservation(reservation.key)
    assert settled.session_handle == "original-session"
    assert settled.conclusion_state == "held"


def test_terminal_refresh_uses_durable_owner_session_for_mux_placeholder(
    q,
    client,
):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="wt-worktree-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(
        task.id,
        "host-a/worktree-exact",
        owner_session_id="owner-session-exact",
    )
    q.complete(task.id, "host-a/worktree-exact")
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        liveness_fn=lambda *_args: None,
        conclusion_fn=lambda *args: calls.append(args) or {
            "action": "primed",
            "reason": "managed-gc-candidate",
        },
    )

    assert sup.reconcile() == 1
    assert calls == [("worktree-exact", "owner-session-exact")]
    settled = q.get_reservation(reservation.key)
    assert settled.session_handle == "owner-session-exact"
    assert settled.conclusion_state == "complete"


def test_terminal_refresh_preserves_headless_session_type_before_nudge(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="wt-worktree-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(
        task.id,
        "headless-worker",
        owner_session_id="owner-session-exact",
    )
    q.complete(task.id, "headless-worker")
    nudges = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda worktree, session: {
            "action": "skipped",
            "reason": "live-session",
            "worktree": worktree,
            "session": session,
        },
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_resume_fn=lambda sid, _prompt: nudges.append(sid) or True,
    )

    assert sup.reconcile() == 0
    settled = q.get_reservation(reservation.key)
    assert settled.session_handle == "local-body:owner-session-exact"
    assert settled.state == SpawnState.SPAWNED
    assert settled.conclusion_state == "pending"
    assert nudges == ["owner-session-exact"]
    detail = json.loads(settled.conclusion_detail or "{}")
    assert detail["session"] == "acp-session-exact"


def test_terminal_conclusion_is_opt_in_only(q, client):
    task = q.create("ordinary", labels=["ordinary"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        conclusion_fn=lambda *args: calls.append(args) or {"action": "primed"},
    )

    assert sup.reconcile() == 1
    assert calls == []


def test_terminal_conclusion_is_terminal_only(q, client):
    task = q.create("still working", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: calls.append(args) or {"action": "primed"},
    )

    assert sup.reconcile() == 0
    assert calls == []
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED


def test_terminal_conclusion_failure_does_not_block_settlement(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert sup.reconcile() == 1
    settled = q.get_reservation(reservation.key)
    assert settled.state == SpawnState.SETTLED
    assert "terminal conclusion failed (boom)" in (settled.detail or "")
    assert settled.conclusion_state == "pending"


def test_terminal_conclusion_reconcile_is_idempotent(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: calls.append(args) or {"action": "primed"},
    )

    assert sup.reconcile() == 1
    assert sup.reconcile() == 0
    assert len(calls) == 1


@pytest.mark.parametrize("action", ["removed", "already-removed"])
def test_removed_conclusion_actions_are_complete(action):
    assert Supervisor._conclusion_state({"action": action}) == "complete"


def test_live_terminal_conclusion_retries_after_settlement(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    outcomes = iter(
        [
            {"action": "skipped", "reason": "live-session"},
            {"action": "primed", "reason": "managed-gc-candidate"},
        ]
    )
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: calls.append(args) or next(outcomes),
        liveness_fn=lambda *_args: None,
    )

    assert sup.reconcile() == 1
    pending = q.get_reservation(reservation.key)
    assert pending.state == SpawnState.SETTLED
    assert pending.conclusion_state == "pending"
    q.settle_spawn(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "skipped",
                "reason": "live-session",
                "attempts": 1,
                "next_attempt_at": 0,
            }
        ),
    )
    assert sup.reconcile() == 0
    complete = q.get_reservation(reservation.key)
    assert complete.conclusion_state == "complete"
    assert len(calls) == 2


def test_live_headless_terminal_conclusion_nudges_same_session_once(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    nudges = []
    conclusions = []

    def conclude(worktree, session):
        conclusions.append((worktree, session))
        return {
            "action": "skipped",
            "reason": "live-session",
        }

    def resume(sid, prompt):
        assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
        nudges.append((sid, prompt))
        return True

    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=conclude,
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_resume_fn=resume,
    )

    assert sup.reconcile() == 0
    pending = q.get_reservation(reservation.key)
    assert pending.state == SpawnState.SPAWNED
    assert pending.conclusion_state == "pending"
    detail = json.loads(pending.conclusion_detail or "{}")
    assert detail["same_owner_nudge"] == "delivered"
    assert conclusions == [("worktree-exact", "acp-session-exact")]
    assert nudges and nudges[0][0] == "session-exact"
    assert "not FINAL" in nudges[0][1]

    q.record_spawn_conclusion(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                **detail,
                "next_attempt_at": 0,
            }
        ),
    )
    assert sup.reconcile() == 0
    assert len(nudges) == 1


def test_live_headless_conclusion_keeps_exclusive_fence_until_nudge(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: {
            "action": "skipped",
            "reason": "live-session",
        },
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_resume_fn=lambda _sid, _prompt: False,
    )

    assert sup.reconcile() == 0
    still_active = q.get_reservation(reservation.key)
    assert still_active.state == SpawnState.SPAWNED
    assert still_active.conclusion_state == "pending"
    detail = json.loads(still_active.conclusion_detail or "{}")
    assert detail["attempts"] == 1
    assert detail["same_owner_nudge"] == "unavailable"


def test_failed_headless_nudges_reach_durable_held_bound(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")

    for attempt in range(12):
        sup = Supervisor(
            client,
            spawn_fn=_ok_spawn(),
            repo=TEST_REPO,
            disposable_cli_labels=["review"],
            conclusion_fn=lambda *_args: {
                "action": "skipped",
                "reason": "live-session",
            },
            local_body_verdict_fn=lambda _sid: tracking.LIVE,
            local_body_activity_fn=lambda _sid: "IDLE",
            local_acp_session_fn=lambda _sid: "acp-session-exact",
            local_resume_fn=lambda _sid, _prompt: False,
        )
        assert sup.reconcile() == 0
        current = q.get_reservation(reservation.key)
        assert current.state == SpawnState.SPAWNED
        detail = json.loads(current.conclusion_detail or "{}")
        assert detail["attempts"] == attempt + 1
        if attempt < 11:
            q.record_spawn_conclusion(
                reservation.key,
                conclusion_state="pending",
                conclusion_detail=json.dumps(
                    {
                        **detail,
                        "next_attempt_at": 0,
                    }
                ),
            )

    held = q.get_reservation(reservation.key)
    assert held.conclusion_state == "held"


def test_successful_headless_nudge_holds_fence_until_conclusion(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    nudges = []
    live = True

    def verdict(_sid):
        return tracking.LIVE if live else tracking.GONE

    outcomes = iter([
        {"action": "skipped", "reason": "live-session"},
        {"action": "primed", "reason": "managed-gc-candidate"},
    ])
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: next(outcomes),
        local_body_verdict_fn=verdict,
        local_body_activity_fn=lambda _sid: "IDLE" if live else None,
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_resume_fn=lambda sid, _prompt: nudges.append(sid) or True,
    )

    assert sup.reconcile() == 0
    checkpoint = q.get_reservation(reservation.key)
    assert checkpoint.state == SpawnState.SPAWNED
    assert checkpoint.conclusion_state == "pending"
    assert json.loads(
        checkpoint.conclusion_detail or "{}"
    )["same_owner_nudge"] == "delivered"

    live = False
    assert sup.reconcile() == 1
    assert nudges == ["session-exact"]
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED


def test_successful_nudge_counts_after_legacy_pending_attempts(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.record_spawn_conclusion(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "failed",
                "reason": "session-identity-unavailable",
                "attempts": 3,
                "next_attempt_at": 0,
            }
        ),
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: {
            "action": "skipped",
            "reason": "live-session",
        },
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_resume_fn=lambda _sid, _prompt: True,
    )

    assert sup.reconcile() == 0
    detail = json.loads(
        q.get_reservation(reservation.key).conclusion_detail or "{}"
    )
    assert detail["attempts"] == 4
    assert detail["same_owner_nudge"] == "delivered"


def test_nonexclusive_headless_conclusion_nudges_before_settlement(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    ended = []
    nudged = []

    def resume(sid, _prompt):
        assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
        nudged.append(sid)
        return True

    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: {
            "action": "skipped",
            "reason": "live-session",
        },
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_end_fn=lambda sid: ended.append(sid) or True,
        local_resume_fn=resume,
    )

    assert sup.reconcile() == 1
    assert nudged == ["session-exact"]
    assert ended == []
    assert q.get_reservation(reservation.key).conclusion_state == "pending"


def test_pending_headless_conclusion_does_not_end_running_session(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    q.settle_spawn(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "skipped",
                "reason": "live-session",
                "attempts": 1,
                "next_attempt_at": 0,
                "same_owner_nudge": "delivered",
            }
        ),
    )
    ended = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "RUNNING",
        local_end_fn=lambda sid: ended.append(sid) or True,
    )

    assert sup.reconcile() == 0
    assert ended == []
    assert q.get_reservation(reservation.key).conclusion_state == "pending"


def test_nonidle_headless_conclusion_reaches_durable_held_bound(q, client):
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")

    for attempt in range(12):
        sup = Supervisor(
            client,
            spawn_fn=_ok_spawn(),
            repo=TEST_REPO,
            disposable_cli_labels=["review"],
            local_body_verdict_fn=lambda _sid: tracking.LIVE,
            local_body_activity_fn=lambda _sid: "RUNNING",
        )
        assert sup.reconcile() == 0
        current = q.get_reservation(reservation.key)
        detail = json.loads(current.conclusion_detail or "{}")
        assert detail["attempts"] == attempt + 1
        if attempt < 11:
            q.record_spawn_conclusion(
                reservation.key,
                conclusion_state="pending",
                conclusion_detail=json.dumps(
                    {
                        **detail,
                        "next_attempt_at": 0,
                    }
                ),
            )

    assert q.get_reservation(reservation.key).conclusion_state == "held"


def test_terminal_conclusion_retries_are_bounded(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: calls.append(args) or {
            "action": "skipped",
            "reason": "live-session",
        },
        liveness_fn=lambda *_args: None,
    )

    assert sup.reconcile() == 1
    for attempt in range(1, 12):
        q.settle_spawn(
            reservation.key,
            conclusion_state="pending",
            conclusion_detail=json.dumps(
                {
                    "action": "skipped",
                    "reason": "live-session",
                    "attempts": attempt,
                    "next_attempt_at": 0,
                }
            ),
        )
        assert sup.reconcile() == 0

    held = q.get_reservation(reservation.key)
    assert held.conclusion_state == "held"
    assert len(calls) == 12


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


def test_reconcile_ends_terminal_local_body_before_settling(q, client):
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-terminal")
    ended: list[str] = []
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda sid: ended.append(sid) or True,
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.complete(t.id, "local-o")

    assert sup.reconcile() == 1
    assert ended == ["brg-terminal"]
    assert q.latest_reservation(t.id).state == SpawnState.SETTLED


def test_reconcile_retains_terminal_local_reservation_when_end_fails(q, client):
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-terminal")
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda _sid: False,
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.complete(t.id, "local-o")

    assert sup.reconcile() == 0
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


def test_reconcile_ends_cold_terminal_local_body_before_settling(q, client):
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-cold")
    ended: list[str] = []
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda sid: ended.append(sid) or True,
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.suspend(t.id, "local-o", reason="turn ended")
    q.record_cold(q.latest_reservation(t.id).key)
    q.complete(t.id, "local-o")

    assert sup.reconcile() == 1
    assert ended == ["brg-cold"]
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


def test_dead_letter_summary_is_compact_and_only_repeats_on_change(
    q, client, caplog
):
    first = q.create("first")
    second = q.create("second")
    sup = Supervisor(
        client,
        spawn_fn=lambda _task: (False, {"error": "boom"}),
        repo=TEST_REPO,
        max_concurrent=5,
        max_attempts=1,
    )
    sup.poll_once()
    caplog.clear()

    sup.poll_once()
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    summaries = [m for m in warnings if "spawn-dead-lettered task(s)" in m]
    assert len(summaries) == 1
    assert first.id in summaries[0]
    assert second.id in summaries[0]
    assert "reservations rearm <task> --permit" in summaries[0]

    caplog.clear()
    sup.poll_once()
    assert not [
        r for r in caplog.records if "spawn-dead-lettered task(s)" in r.message
    ]

    third = q.create("third")
    sup.poll_once()
    caplog.clear()
    sup.poll_once()
    summaries = [
        r.message
        for r in caplog.records
        if "spawn-dead-lettered task(s)" in r.message
    ]
    assert len(summaries) == 1
    assert third.id in summaries[0]


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
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend="cli", cli_label=None, headless_label=None,
    )
    assert m._cmd_supervise(args) == 0
    assert spawn.calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


def test_cli_supervise_all_repos_marks_spawn_claim_as_administrative(
    monkeypatch, q, client
):
    import types

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    q.create("work")
    spawn = _ok_spawn()
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "_client", lambda _args, **_kw: client)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(
        m,
        "_scope_repo",
        lambda _args: (_ for _ in ()).throw(AssertionError("must not resolve repo")),
    )
    monkeypatch.setattr(
        sup_mod,
        "make_embody_spawn",
        lambda **kwargs: seen.append(("cli", kwargs)) or spawn,
    )
    monkeypatch.setattr(
        sup_mod,
        "make_headless_spawn",
        lambda **kwargs: seen.append(("headless", kwargs)) or spawn,
    )
    args = types.SimpleNamespace(
        all_repos=True,
        repo=None,
        url=None,
        token=None,
        label=None,
        max_concurrent=5,
        verify_timeout=0,
        once=True,
        interval=30.0,
        no_heartbeat=False,
        max_attempts=3,
        embody_backend=None,
        cli_label=None,
        headless_label=None,
        headless_agent="task-worker",
    )

    assert m._cmd_supervise(args) == 0
    assert {kind for kind, _kwargs in seen} == {"cli", "headless"}
    assert all(kwargs["all_repos"] is True for _kind, kwargs in seen)


def test_cli_supervise_refuses_unresolved_repo_without_all_repos(
    monkeypatch, capsys
):
    import types

    from agent_dispatch import __main__ as m

    monkeypatch.setattr(m, "_scope_repo", lambda _args: None)
    args = types.SimpleNamespace(all_repos=False)

    assert m._cmd_supervise(args) == 2
    assert "could not resolve the calling repo" in capsys.readouterr().err


def test_make_embody_spawn_records_handle_on_success(monkeypatch):
    """The CLI embody backend returns success + a parsed session/worktree handle
    when embody exits 0 (regression: it previously fell through to None, breaking
    record_spawn on the happy path)."""
    import subprocess

    from agent_dispatch import embody
    from agent_dispatch.supervisor import make_embody_spawn

    def fake_spawn_embodied_worker(
        task_id,
        *,
        worker_id,
        driver,
        project=None,
        worktree_id=None,
        route="",
        repo=None,
        all_repos=False,
        verify_timeout=0,
    ):
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"worktree_id": "wt-9", "session_id": "sess-9"}', stderr="",
        )

    monkeypatch.setattr(embody, "spawn_embodied_worker", fake_spawn_embodied_worker)
    ok, handle = make_embody_spawn()(
        {"id": "t", "repo": "gitea.example/org/widgets"}
    )
    assert ok is True
    assert handle["worktree"] == "wt-9"
    assert handle["session"] == "sess-9"


def test_parse_handle_accepts_nested_worktree_object():
    """Older/newer agent-worktrees JSON shapes both preserve the worktree handle."""
    import subprocess

    from agent_dispatch import embody

    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"worktree": {"id": "wt-nested"}, "session": "sess-nested"}',
        stderr="",
    )

    assert embody.parse_handle(result) == {
        "worktree": "wt-nested",
        "session": "sess-nested",
    }


def test_resolving_client_uses_fresh_client_for_each_operation():
    from agent_dispatch.client import ResolvingDispatchClient

    created: list[int] = []

    class FakeClient:
        def __init__(self, generation):
            self.generation = generation

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, task_id):
            return {"generation": self.generation, "task_id": task_id}

    def factory():
        generation = len(created) + 1
        created.append(generation)
        return FakeClient(generation)

    client = ResolvingDispatchClient(factory)

    assert client.get("a") == {"generation": 1, "task_id": "a"}
    assert client.get("b") == {"generation": 2, "task_id": "b"}
    assert created == [1, 2]


def test_resolving_client_rejects_unknown_attribute():
    from agent_dispatch.client import ResolvingDispatchClient

    client = ResolvingDispatchClient(lambda: None)
    # Only real DispatchClient methods proxy; a typo/unknown name is an honest
    # AttributeError (hasattr stays truthful) rather than a silent callable.
    assert hasattr(client, "reserve_spawn")
    assert not hasattr(client, "definitely_not_a_method")
    import pytest

    with pytest.raises(AttributeError):
        client.definitely_not_a_method


def test_dispatch_client_skips_tls_setup_only_for_plain_http(monkeypatch):
    from agent_dispatch import client as client_module
    from agent_dispatch.client import DispatchClient

    created: list[dict] = []

    class FakeHttpClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(client_module.httpx, "Client", FakeHttpClient)

    DispatchClient("http://127.0.0.1:9847").close()
    DispatchClient("https://dispatch.example.com").close()

    assert created[0]["verify"] is False
    assert created[1]["verify"] is True


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
        task_id, *, agent, worker_id, prompt, route="", wait, **_kw
    ):
        calls.update(
            task_id=task_id, agent=agent,
            worker_id=worker_id, prompt=prompt, wait=wait,
        )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bridge, "spawn_worker", fake_spawn_worker)
    monkeypatch.setattr(
        embody, "autopilot_worker_prompt",
        lambda task_id, *, worker_id, route="", repo=None, all_repos=False,
        explicit_worker_identity=False: (
            f"SEED::{task_id}"
        ),
    )

    spawn = make_headless_spawn(agent="review-worker")
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
    ok, handle = make_headless_spawn()({"id": "t"})
    assert ok is False
    assert "boom" in handle["error"]


def test_make_headless_spawn_reuses_carried_session(monkeypatch):
    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    resumed = []
    created = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "live")
    monkeypatch.setattr(
        bridge,
        "resume_worker",
        lambda sid, prompt, **_kwargs: resumed.append((sid, prompt)) or True,
    )
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )

    ok, handle = make_headless_spawn()(
        {
            "id": "next-task",
            "repo": TEST_REPO,
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/tmp/wt-review",
            "spawn_session_handle": "local-body:session-old",
        }
    )

    assert ok is True
    assert handle == {
        "session": "local-body:session-old",
        "worktree": "wt-review",
    }
    assert resumed and resumed[0][0] == "session-old"
    assert "next-task" in resumed[0][1]
    assert created == []


def test_make_headless_spawn_does_not_replace_live_busy_session(monkeypatch):
    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    created = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "live")
    monkeypatch.setattr(bridge, "resume_worker", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )

    ok, handle = make_headless_spawn()(
        {
            "id": "next-task",
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/tmp/wt-review",
            "spawn_session_handle": "local-body:session-busy",
        }
    )

    assert ok is False
    assert "remains live" in handle["error"]
    assert created == []


def test_make_headless_spawn_replaces_confirmed_gone_session(monkeypatch):
    import subprocess

    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    created = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "gone")
    monkeypatch.setattr(bridge, "resume_worker", lambda *_args, **_kwargs: False)

    def create(*args, **kwargs):
        created.append((args, kwargs))
        return subprocess.CompletedProcess(
            [],
            0,
            '{"session_id":"session-new"}',
            "",
        )

    monkeypatch.setattr(bridge, "spawn_worker", create)

    ok, handle = make_headless_spawn()(
        {
            "id": "next-task",
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/tmp/wt-review",
            "spawn_session_handle": "local-body:session-gone",
        }
    )

    assert ok is True
    assert handle == {
        "session": "local-body:session-new",
        "worktree": "wt-review",
    }
    assert created[0][1]["target_dir"] == "/tmp/wt-review"
    assert created[0][1]["worktree_id"] == "wt-review"


def test_make_headless_spawn_holds_on_unknown_carried_session(monkeypatch):
    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    created = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "unknown")
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )

    ok, handle = make_headless_spawn()(
        {
            "id": "next-task",
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/tmp/wt-review",
            "spawn_session_handle": "local-body:session-unknown",
        }
    )

    assert ok is False
    assert "could not determine" in handle["error"]
    assert created == []


def test_make_headless_spawn_degrades_when_bridge_absent(monkeypatch):
    from agent_dispatch import bridge, embody
    from agent_dispatch.supervisor import make_headless_spawn

    monkeypatch.setattr(embody, "autopilot_worker_prompt", lambda *a, **k: "seed")

    def _boom(*a, **k):
        raise bridge.BridgeUnavailable("no agent-bridge on PATH")

    monkeypatch.setattr(bridge, "spawn_worker", _boom)
    ok, handle = make_headless_spawn()({"id": "t"})
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


def test_cli_supervise_headless_is_default(monkeypatch, q, client):
    """Headless is the DEFAULT embody backend: with no per-label flags, every
    watched task embodies headless (no CLI/mux)."""
    import types

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    a = q.create("sweep a")
    b = q.create("sweep b")

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
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend=None, headless_label=None, cli_label=None,
        headless_agent="task-worker",
    )
    assert m._cmd_supervise(args) == 0
    assert set(headless_calls) == {a.id, b.id}  # both headless by default
    assert embody_calls == []


def test_ordinary_headless_task_records_created_worktree_before_launch(
    monkeypatch, q, client
):
    from agent_dispatch import embody

    task = q.create("ordinary")
    spawn = _ok_spawn({"session": "local-body:s1", "worktree": "wt-created"})
    spawn.requires_reusable_worktree = True
    spawn.allocation_interface = "acp"
    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda *_args, **_kwargs: {
            "worktree": "wt-created",
            "path": "/tmp/wt-created",
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
    )

    assert sup.poll_once() == [task.id]
    reservation = q.latest_reservation(task.id)
    assert reservation.worktree == "wt-created"
    assert reservation.worktree_ownership == "created"
    assert reservation.driver == "agent-dispatch"
    assert reservation.creating_host == "host-a"


def test_exclusive_spawn_records_precreated_worktree_before_launch(
    monkeypatch, q, client
):
    from agent_dispatch import embody

    task = q.create("review", exclusive_key="review:repo:42")
    observed = {}

    def spawn(spawn_task):
        reservation = q.latest_reservation(task.id)
        observed.update(task=spawn_task, reservation=reservation)
        return True, {
            "session": "local-body:session-new",
            "worktree": spawn_task["spawn_worktree"],
        }

    spawn.requires_reusable_worktree = True
    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda _task, _reservation, **_kwargs: {
            "worktree": "wt-precreated",
            "path": "/tmp/wt-precreated",
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
    )

    assert sup.poll_once() == [task.id]
    assert observed["reservation"].state == SpawnState.RESERVING
    assert observed["reservation"].worktree == "wt-precreated"
    assert observed["task"]["spawn_worktree_path"] == "/tmp/wt-precreated"


def test_created_worktree_bookkeeping_failure_retains_spawn_fence(
    monkeypatch, q, client
):
    from agent_dispatch import embody

    task = q.create("work")
    spawn = _ok_spawn()
    spawn.requires_reusable_worktree = True
    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda *_args, **_kwargs: {
            "worktree": "wt-created",
            "path": "/tmp/wt-created",
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )
    monkeypatch.setattr(
        client,
        "record_spawn_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DispatchError(503, "coordinator unavailable")
        ),
    )
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.latest_reservation(task.id).state == SpawnState.RESERVING


def test_cli_supervise_cli_label_opts_out(monkeypatch, q, client):
    """--cli-label routes a marked task back to CLI/mux while everything else
    stays headless (the opt-out on a headless-by-default lane)."""
    import types

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    interactive = q.create("interactive work", labels=["needs-attach"])
    sweep = q.create("sweep work")

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
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend=None, headless_label=None, cli_label=["needs-attach"],
        headless_agent="task-worker",
    )
    assert m._cmd_supervise(args) == 0
    assert embody_calls == [interactive.id]  # opted out to CLI
    assert sweep.id in headless_calls
    assert interactive.id not in headless_calls


def test_cli_supervise_headless_label_routes(monkeypatch, q, client):
    """--headless-label forces a label headless on an explicit --embody-backend cli
    lane (the opt-in when the default has been set back to CLI)."""
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
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)

    args = types.SimpleNamespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend="cli", headless_label=["nightly-scan"], cli_label=None,
        headless_agent="task-worker",
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
            all_repos=False, verify_timeout=0,
        ):
            self.pool = list(pool)
            captured.update(
                pool=pool, origin=origin, headless=headless, agent=agent,
                all_repos=all_repos,
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
    assert captured["all_repos"] is False
    assert captured["pool"] == ["anomalous-potato-wsl"]
    assert captured["origin"] == "mantis-counter"



# -- Slice 2: liveness-gated auto-recovery -----------------------------------


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_superseded_task_does_not_release_exclusive_live_reservation(
    q, client, verdict
):
    old = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-old",
        worktree="wt-review",
    )
    new = q.create(
        "new",
        exclusive_key="review:repo:42",
        supersede_exclusive_key=True,
    )
    ended = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: verdict,
        local_end_fn=lambda sid: ended.append(sid) or False,
        nudge=False,
    )

    assert q.get(old.id).status == Status.ABANDONED
    assert sup.reconcile() == 0
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
    blocked, acquired = q.reserve_spawn(new.id)
    assert acquired is False
    assert blocked.key == reservation.key
    assert ended == (["session-old"] if verdict == "live" else [])


def test_confirmed_gone_superseded_body_releases_exclusive_key(q, client):
    old = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-old",
        worktree="wt-review",
    )
    new = q.create(
        "new",
        exclusive_key="review:repo:42",
        supersede_exclusive_key=True,
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda _sid: pytest.fail(
            "confirmed-gone body does not need an end request"
        ),
        nudge=False,
    )

    assert sup.reconcile() == 1
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED
    fresh, acquired = q.reserve_spawn(new.id)
    assert acquired is True
    assert fresh.worktree == "wt-review"


def test_explicit_end_releases_live_exclusive_body(q, client):
    task = q.create("work", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-live",
        worktree="wt-review",
    )
    q.abandon(task.id, permitted=True, reason="superseded")
    ended = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda sid: ended.append(sid) or True,
        nudge=False,
    )

    assert sup.reconcile() == 1
    assert ended == ["session-live"]
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_yielded_fleet_body_is_not_replaced_before_safe_teardown(
    q, client, verdict
):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:session-live",
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")
    q.yield_task(task.id, "fleet-owner")
    ended = []
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        fleet_verdict_fn=lambda _host, _sid: verdict,
        fleet_end_fn=lambda host, sid: ended.append((host, sid)) or False,
        nudge=False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
    assert ended == (
        [("worker-host", "session-live")] if verdict == "live" else []
    )


def test_yielded_fleet_body_respawns_only_after_explicit_end(q, client):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:session-live",
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")
    q.yield_task(task.id, "fleet-owner")
    ended = []
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        fleet_verdict_fn=lambda _host, _sid: "live",
        fleet_end_fn=lambda host, sid: ended.append((host, sid)) or True,
        nudge=False,
    )

    assert sup.poll_once() == [task.id]
    assert ended == [("worker-host", "session-live")]
    assert spawn.calls == [task.id]
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED
    assert q.latest_reservation(task.id).attempt == 2


def test_yielded_created_worktree_is_concluded_before_respawn(q, client):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-1",
        worktree="wt-created",
    )
    q.claim_one("host-a/wt-created", task_id=task.id)
    q.start(
        task.id,
        "host-a/wt-created",
        owner_session_id="acp-session-1",
    )
    q.yield_task(task.id, "host-a/wt-created")
    conclusions = []
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "gone",
        local_acp_session_fn=lambda _sid: "acp-session-1",
        attempt_conclusion_fn=lambda *args: conclusions.append(args) or {
            "action": "primed",
            "reason": "managed-gc-candidate",
        },
        nudge=False,
    )

    assert sup.poll_once() == [task.id]
    first = q.get_reservation(reservation.key)
    assert first.state == SpawnState.SETTLED
    assert first.conclusion_state == "complete"
    assert conclusions == [
        (
            "wt-created",
            "acp-session-1",
            reservation.key,
            "agent-dispatch",
        )
    ]
    assert q.latest_reservation(task.id).attempt == 2


def test_failed_attempt_cleanup_retries_across_supervisor_restart(q, client):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="launch failed",
        disposition="failed",
    )
    spawn = _ok_spawn()
    first = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "gone",
        attempt_conclusion_fn=lambda *_args: {
            "action": "failed",
            "reason": "lifecycle lock busy",
        },
        nudge=False,
    )

    assert first.poll_once() == []
    pending = q.get_reservation(reservation.key)
    assert pending.state == SpawnState.RELEASING
    assert pending.conclusion_state == "pending"
    assert spawn.calls == []

    second = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "gone",
        attempt_conclusion_fn=lambda *_args: {
            "action": "primed",
            "reason": "managed-gc-candidate",
        },
        nudge=False,
    )

    assert second.poll_once() == [task.id]
    assert q.get_reservation(reservation.key).state == SpawnState.FAILED
    assert q.latest_reservation(task.id).attempt == 2


def test_held_attempt_cleanup_remains_fenced_without_retries(q, client):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="launch failed",
        disposition="failed",
    )
    conclusions = []
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "gone",
        attempt_conclusion_fn=lambda *args: conclusions.append(args) or {
            "action": "preserved",
            "reason": "dirty-worktree",
        },
        nudge=False,
    )

    assert sup.poll_once() == []
    held = q.get_reservation(reservation.key)
    assert held.state == SpawnState.RELEASING
    assert held.conclusion_state == "held"
    assert len(conclusions) == 1
    assert spawn.calls == []

    assert sup.poll_once() == []
    assert len(conclusions) == 1
    assert q.get_reservation(reservation.key).state == SpawnState.RELEASING
    assert spawn.calls == []


def test_terminal_created_worktree_uses_attempt_cleanup_without_label(
    q, client
):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-1",
        worktree="wt-created",
    )
    q.claim_one("host-a/wt-created", task_id=task.id)
    q.start(
        task.id,
        "host-a/wt-created",
        owner_session_id="acp-session-1",
    )
    q.complete(task.id, "host-a/wt-created")
    conclusions = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "gone",
        local_acp_session_fn=lambda _sid: "acp-session-1",
        attempt_conclusion_fn=lambda *args: conclusions.append(args) or {
            "action": "primed",
            "reason": "managed-gc-candidate",
        },
        nudge=False,
    )

    assert sup.poll_once() == []
    settled = q.get_reservation(reservation.key)
    assert settled.state == SpawnState.SETTLED
    assert settled.conclusion_state == "complete"
    assert conclusions


def test_yielded_targeted_worktree_is_preserved_without_conclusion(q, client):
    task = q.create("work", affinity={"worktree": "wt-target"})
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-1",
        worktree="wt-target",
    )
    q.claim_one("host-a/wt-target", task_id=task.id)
    q.start(task.id, "host-a/wt-target")
    q.yield_task(task.id, "host-a/wt-target")
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "gone",
        attempt_conclusion_fn=lambda *_args: pytest.fail(
            "targeted worktree must never be concluded as dispatch-owned"
        ),
        nudge=False,
    )

    assert sup.poll_once() == [task.id]
    settled = q.get_reservation(reservation.key)
    assert settled.state == SpawnState.SETTLED
    assert settled.conclusion_state == "complete"
    assert "targeted-worktree" in (settled.conclusion_detail or "")


def test_yielded_cli_worker_is_not_redriven_while_release_is_pending(
    q, client
):
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-live",
        worktree="wt-live",
    )
    q.claim_one("host/wt-live", task_id=task.id)
    q.start(task.id, "host/wt-live")
    q.yield_task(task.id, "host/wt-live", exclude="worktree:wt-live")
    redriven = []
    spawn = _ok_spawn()
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda worktree, _machine: {
            "session_id": "session-live",
            "worktree_id": worktree,
        },
        redrive_fn=lambda *args: redriven.append(args) or True,
        nudge=False,
    )

    assert sup.poll_once() == []
    assert redriven == []
    assert spawn.calls == []
    active = q.get_reservation(reservation.key)
    assert active.state == SpawnState.SPAWNED
    assert active.release_requested is True


def test_completed_idle_exclusive_body_is_carried_to_next_episode(q, client):
    task = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-idle",
        worktree="wt-review",
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    q.complete(task.id, "headless-owner", result_ref="result/1")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_end_fn=lambda _sid: pytest.fail(
            "idle completed session should remain reusable"
        ),
        nudge=False,
    )

    assert sup.reconcile() == 1
    next_task = q.create("new", exclusive_key="review:repo:42")
    carried, acquired = q.reserve_spawn(next_task.id)
    assert acquired is True
    assert carried.worktree == "wt-review"
    assert carried.session_handle == "local-body:session-idle"


def test_completed_live_exclusive_fleet_body_is_ended_before_release(
    q, client
):
    task = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:session-live",
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")
    q.complete(task.id, "fleet-owner", result_ref="result/1")
    ended = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        fleet_verdict_fn=lambda _host, _sid: "live",
        fleet_activity_fn=lambda _host, _sid: "IDLE",
        fleet_end_fn=lambda host, sid: ended.append((host, sid)) or True,
        nudge=False,
    )

    assert sup.reconcile() == 1
    assert ended == [("worker-host", "session-live")]
    assert q.get_reservation(reservation.key).state == SpawnState.SETTLED


@pytest.mark.parametrize(
    ("verdict", "expected_state"),
    [
        ("gone", SpawnState.FAILED),
        ("unknown", SpawnState.RESERVING),
    ],
)
def test_reconcile_reserving_fails_only_confirmed_gone_worktree(
    q, client, verdict, expected_state
):
    task = q.create("work", exclusive_key="resource:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *_args: verdict,
        nudge=False,
    )

    expected_count = 1 if verdict == "gone" else 0
    assert sup.reconcile_reserving() == expected_count
    assert q.get_reservation(reservation.key).state == expected_state


def test_reconcile_reserving_adopts_confirmed_live_worktree(q, client):
    task = q.create("work", exclusive_key="resource:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda worktree, _machine: {
            "session_id": "session-live",
            "worktree_id": worktree,
        },
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    adopted = q.get_reservation(reservation.key)
    assert adopted.state == SpawnState.SPAWNED
    assert adopted.session_handle == "session-live"
    assert adopted.worktree == "wt-precreated"


def test_reconcile_reserving_rearms_idle_carried_local_session(q, client):
    prior_task = q.create("old", exclusive_key="resource:42")
    prior, _ = q.reserve_spawn(prior_task.id)
    q.record_spawn(
        prior.key,
        session_handle="local-body:session-idle",
        worktree="wt-review",
    )
    q.settle_spawn(prior.key)
    task = q.create("new", exclusive_key="resource:42")
    reservation, _ = q.reserve_spawn(task.id)
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    assert q.get_reservation(reservation.key).state == SpawnState.FAILED
    fresh, acquired = q.reserve_spawn(task.id)
    assert acquired is True
    assert fresh.worktree == "wt-review"
    assert fresh.session_handle == "local-body:session-idle"


def test_reconcile_reserving_without_durable_handle_stays_reserved(q, client):
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id)
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *_args: pytest.fail(
            "unbound reservation cannot be classified"
        ),
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert q.get_reservation(reservation.key).state == SpawnState.RESERVING


@pytest.mark.parametrize(
    ("session_handle", "body_kind"),
    [
        ("worktree-session", "worktree"),
        ("fleet-body:host-a:fleet-session", "fleet"),
        ("local-body:local-session", "local"),
    ],
)
def test_recover_gone_ignores_suspended_bodies(
    q, client, session_handle, body_kind
):
    task = q.create("wait")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(
        reservation.key,
        session_handle=session_handle,
        worktree="wt-1" if body_kind == "worktree" else None,
    )
    owner = "host-a/wt-1"
    q.claim_one(owner, task_id=task.id)
    q.start(task.id, owner, owner_session_id="owner-session")
    q.suspend(task.id, owner, reason="awaiting review")
    before = q.get(task.id)
    probes = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *args: probes.append(("worktree", args)) or "gone",
        fleet_verdict_fn=lambda *args: probes.append(("fleet", args)) or "gone",
        local_body_verdict_fn=lambda *args: probes.append(("local", args)) or "gone",
        nudge=False,
    )

    assert sup.recover_gone() == 0
    after_reservation = q.latest_reservation(task.id)
    assert probes == []
    assert after_reservation.state == SpawnState.SPAWNED
    assert after_reservation.attempt == 1
    assert q.get(task.id).attempts == before.attempts


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


def test_redrive_live_spawned_worker_that_never_claimed(q, client):
    """A live spawned worker with a queued task is re-prompted, not duplicated."""
    task = q.create("work")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(reservation.key, session_handle="wt-wt-1", worktree=None)
    redriven = []
    spawn = _ok_spawn({"session": "new", "worktree": "new-wt"})
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        liveness_fn=lambda wt, mc: {
            "session_id": "live-session-1",
            "worktree_id": wt,
            "liveness": "idle",
        },
        redrive_fn=lambda *args: redriven.append(args) or True,
        nudge=False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert len(redriven) == 1
    assert redriven[0][0] == "wt-1"
    updated = q.get_reservation(reservation.key)
    assert updated.state == SpawnState.SPAWNED
    assert updated.worktree == "wt-1"
    assert updated.session_handle == "live-session-1"


def test_redrive_unknown_spawned_worker_is_left_reserved(q, client):
    """Unknown bridge liveness is not treated as death and is not re-driven."""
    task = q.create("work")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(reservation.key, session_handle="wt-wt-1", worktree=None)
    redriven = []
    spawn = _ok_spawn({"session": "new", "worktree": "new-wt"})
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        liveness_fn=lambda wt, mc: None,
        redrive_fn=lambda *args: redriven.append(args) or True,
        nudge=False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert redriven == []
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED


def test_redrive_is_once_per_supervisor_process(q, client):
    task = q.create("work")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(reservation.key, session_handle="s", worktree="wt-1")
    redriven = []
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        max_concurrent=5,
        liveness_fn=lambda wt, mc: {"session_id": "live-session-1", "worktree_id": wt},
        redrive_fn=lambda *args: redriven.append(args) or True,
        nudge=False,
    )

    assert sup.redrive_unclaimed_spawns() == 1
    assert sup.redrive_unclaimed_spawns() == 0
    assert len(redriven) == 1


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


def test_completion_verify_accepts_goal_with_structured_result(q, client):
    """A non-null structured result is recorded completion evidence."""
    t = q.create("goal work", goal="reach X", done_criteria="X is done")
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1", result={"outcome": "complete"})

    assert sup.reconcile() == 1
    detail = q.latest_reservation(t.id).detail or ""
    assert "UNVERIFIED" not in detail
    assert "structured result" in detail


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


# -- retired turn-state polling ----------------------------------------------


def test_wait_for_turn_end_never_polls_workers(q, client, monkeypatch):
    """The compatibility path performs one full sleep and no bridge/SSH probes."""
    task = q.create("work")
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        max_concurrent=5,
        reactive=True,
    )
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=task.id, machine="m", worktree="wt-1")
    q.start(task.id, "m/wt-1")
    monkeypatch.setattr(
        tracking,
        "resolve_live_session",
        lambda *_args, **_kwargs: pytest.fail("turn-state polling is forbidden"),
    )
    slept: list[float] = []
    assert sup.wait_for_turn_end(30.0, sleep=slept.append) is False
    assert slept == [30.0]


def test_wait_for_turn_end_ignores_retired_reactive_flag(q, client):
    """reactive=False still performs the one fixed-interval sleep."""
    sup = Supervisor(
        client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5, reactive=False
    )
    slept: list[float] = []
    assert sup.wait_for_turn_end(30.0, sleep=slept.append) is False
    assert slept == [30.0]


def test_wait_for_turn_end_zero_interval_yields(q, client):
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    slept: list[float] = []

    assert sup.wait_for_turn_end(0.0, sleep=slept.append) is False
    assert slept == [0.0]


def test_wait_for_turn_end_rejects_negative_interval(q, client):
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    slept: list[float] = []

    with pytest.raises(ValueError, match="interval must be non-negative"):
        sup.wait_for_turn_end(-0.1, sleep=slept.append)
    assert slept == []


def test_serve_uses_one_full_interval_wait(q, client):
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    polls = 0
    waits: list[float] = []

    def poll_once():
        nonlocal polls
        polls += 1
        if polls == 2:
            raise KeyboardInterrupt
        return []

    sup.poll_once = poll_once
    sup.wait_for_turn_end = lambda timeout: waits.append(timeout) or False

    sup.serve(interval=30.0)

    assert waits == [30.0]


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


def test_supervisor_publishes_fleet_body_activity(q, client):
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-activity")
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        heartbeat=False,
        publish_activity=True,
        fleet_activity_fn=lambda host, sid: "STALLED",
        fleet_verdict_fn=lambda host, sid: "live",
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert q.get(t.id).activity == "ACTIVE"  # immediate spawn observation
    assert sup.hold_live_leases() == 0
    assert q.get(t.id).activity == "STALLED"


def test_hold_live_leases_does_not_observe_suspended_fleet_body(
    q, client, monkeypatch
):
    t = q.create("dormant")
    spawn = _fleet_spawn("fleet-body:h:brg-dormant")
    activity_calls: list[tuple[str, str]] = []
    verdict_calls: list[tuple[str, str]] = []
    heartbeat_calls: list[tuple[str, str]] = []
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        publish_activity=True,
        fleet_activity_fn=lambda host, sid: activity_calls.append((host, sid)),
        fleet_verdict_fn=lambda host, sid: verdict_calls.append((host, sid)),
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("fleet-o", task_id=t.id)
    q.start(t.id, "fleet-o")
    q.suspend(t.id, "fleet-o", reason="waiting")
    monkeypatch.setattr(
        client,
        "heartbeat",
        lambda tid, wid: heartbeat_calls.append((tid, wid)),
    )

    assert sup.hold_live_leases() == 0
    assert activity_calls == []
    assert verdict_calls == []
    assert heartbeat_calls == []
    assert q.get(t.id).activity is None


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


def test_productive_gone_local_body_does_not_burn_spawn_failure_budget(q, client):
    """A one-turn body that durably posted progress ended successfully. Its gone
    reservation is settled, not failed, so a later steer can re-embody even when
    max_attempts=1."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-productive")
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        max_attempts=1,
        local_body_verdict_fn=lambda sid: "gone",
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    first = q.latest_reservation(t.id)
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.record_progress(
        t.id,
        "local-o",
        phase="awaiting steer",
        summary="posted review card",
        now=first.reserved_at + 1,
    )

    assert sup.recover_gone() == 1
    assert q.get(t.id).status == Status.QUEUED
    first = q.list_reservations(task_id=t.id)[0]
    assert first.state == SpawnState.SETTLED
    assert q.list_reservations(task_id=t.id, state=SpawnState.FAILED) == []

    # max_attempts=1 applies to failed spawns, not successful one-turn rounds.
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]


def test_interactive_awaiting_steer_stays_suspended_until_submit(q, client):
    """A blocking card parks an interactive task without spawning a replacement;
    the operator answer resumes its existing owner."""
    from agent_dispatch import steering

    t = q.create("review")
    q.claim_one("reviewer", task_id=t.id)
    q.start(t.id, "reviewer")
    q.set_card(
        t.id,
        "reviewer",
        card=steering.build_card(
            request_input=steering.parse_request_input("feedback:textarea")
        ),
    )
    q.yield_task(t.id, "reviewer")
    spawn = _ok_spawn({"session": "replacement", "worktree": None})
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        nudge=False,
    )

    assert q.get(t.id).status == Status.SUSPENDED
    assert q.get(t.id).awaiting_steer is True
    assert sup.poll_once() == []
    assert spawn.calls == []

    q.submit_steer(t.id, fields={"feedback": ""}, sender="operator")
    assert q.get(t.id).awaiting_steer is False
    assert q.get(t.id).status == Status.STARTED
    assert sup.poll_once() == []
    assert spawn.calls == []


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


def test_supervisor_publishes_local_body_activity_while_task_is_queued(
    q, client, monkeypatch
):
    """Activity is independent from phase: a spawned body may execute before it
    claims, so a queued task can legitimately read ACTIVE."""
    from agent_dispatch import tracking

    t = q.create("work")
    spawn = _local_spawn("local-body:brg-activity")
    sessions = [{
        "session_id": "brg-activity",
        "status": "running",
        "liveness": "active",
    }]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda: sessions)
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        heartbeat=False,
        publish_activity=True,
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert q.get(t.id).status == Status.QUEUED
    assert q.get(t.id).activity == "ACTIVE"

    assert sup.hold_live_leases() == 0
    assert q.get(t.id).activity == "ACTIVE"
    sessions[0] = {
        "session_id": "brg-activity",
        "status": "idle",
        "liveness": "idle",
    }
    assert sup.hold_live_leases() == 0
    assert q.get(t.id).activity == "IDLE"


def test_hold_live_leases_degrades_when_local_session_listing_fails(
    q, client, monkeypatch
):
    from agent_dispatch import tracking

    t = q.create("work")
    spawn = _local_spawn("local-body:brg-unavailable")
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda: [])
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        heartbeat=False,
        publish_activity=True,
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]

    assert sup.hold_live_leases() == 0
    assert q.get(t.id).activity is None


def test_hold_live_leases_does_not_observe_suspended_local_body(
    q, client, monkeypatch
):
    from agent_dispatch import tracking

    t = q.create("dormant")
    spawn = _local_spawn("local-body:brg-dormant")
    session_list_calls: list[bool] = []
    verdict_calls: list[str] = []
    heartbeat_calls: list[tuple[str, str]] = []
    sup = Supervisor(
        client,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        publish_activity=True,
        local_body_verdict_fn=lambda sid: verdict_calls.append(sid),
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.suspend(t.id, "local-o", reason="waiting")
    monkeypatch.setattr(
        tracking,
        "list_local_body_sessions",
        lambda: session_list_calls.append(True) or [],
    )
    monkeypatch.setattr(
        client,
        "heartbeat",
        lambda tid, wid: heartbeat_calls.append((tid, wid)),
    )

    assert sup.hold_live_leases() == 0
    assert session_list_calls == []
    assert verdict_calls == []
    assert heartbeat_calls == []
    assert q.get(t.id).activity is None


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


def _complete(q, title, *, labels=None, **fields):
    t = q.create(title, labels=labels or [], **fields)
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


def test_evaluator_ref_consumes_only_producer_associated_tasks(q, client):
    _complete(
        q, "mine", labels=["recipe:reviewer"], evaluator_ref="review-loop"
    )
    _complete(
        q, "other", labels=["recipe:reviewer"], evaluator_ref="other-loop"
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
        evaluator_ref="review-loop",
    )
    assert sup.advance_via_evaluator() == 1
    assert [t.title for t in _conflict_followups(q)] == [
        "unstick follow-up for mine"
    ]


def test_unscoped_evaluator_does_not_consume_associated_tasks(q, client):
    _complete(
        q, "associated", labels=["recipe:reviewer"], evaluator_ref="review-loop"
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 0
    assert _conflict_followups(q) == []


def test_evaluator_filter_applies_before_terminal_result_limit(q, client):
    mine = _complete(
        q, "mine", labels=["recipe:reviewer"], evaluator_ref="review-loop"
    )
    for index in range(3):
        _complete(
            q,
            f"other-{index}",
            labels=["recipe:reviewer"],
            evaluator_ref="other-loop",
        )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
        evaluator_ref="review-loop",
        evaluate_limit=1,
    )
    assert sup.advance_via_evaluator() == 1
    followup = _conflict_followups(q)[0]
    assert followup.dedup_key == f"eval:conflict:{mine.id}"


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
