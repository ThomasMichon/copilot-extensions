"""Retained resource custody and process capacity have different lifetimes."""

from __future__ import annotations

from dataclasses import asdict
from threading import Event, Thread

import pytest

from agent_dispatch import tracking
from agent_dispatch.queue import SpawnState
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.spawn_script_body import _encode_script_body_handle
from tests._helpers import TEST_REPO
from tests.test_supervisor import OwnerResultQueue, QueueBackedClient


@pytest.mark.parametrize("state", [SpawnState.RESERVING, SpawnState.SPAWNED])
@pytest.mark.parametrize("startup_complete", [False, True])
@pytest.mark.parametrize("verdict", [tracking.LIVE, tracking.UNKNOWN, tracking.GONE])
def test_terminal_capacity_requires_closed_start_and_exact_absence(
    tmp_path, state, verdict, startup_complete,
):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Retained task", exclusive_key="resource:retained")
    reservation, acquired = queue.reserve_spawn(task.id, reserved_by="original")
    assert acquired
    queue.record_spawn(
        reservation.key, session_handle="local-body:retained", worktree="retained-worktree",
        startup_complete=True if startup_complete else None, startup_revision=1,
    )
    if state == SpawnState.RESERVING:
        # A carried identity can exist before the next startup completes.
        with queue._connect() as conn:
            conn.execute(
                "UPDATE spawn_reservations SET state=? WHERE key=?",
                (state, reservation.key),
            )
    queue.set_hold(task.id, reason="Keep retained custody", actor="operator")
    queue.abandon(task.id, permitted=True, reason="Assignment withdrawn")
    before = asdict(queue.get_reservation(reservation.key))
    task_before = asdict(queue.get(task.id))
    probes = []

    def observe(session):
        probes.append(session)
        return verdict

    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("read-only capacity"),
        repo=TEST_REPO, max_concurrent=1, local_body_verdict_fn=observe,
    )

    active = supervisor._active_reservations()

    expected = not (
        state == SpawnState.SPAWNED and startup_complete and verdict == tracking.GONE
    )
    assert bool(active) is expected
    after = asdict(queue.get_reservation(reservation.key))
    expected_fence = state == SpawnState.SPAWNED and startup_complete
    assert after == {
        **before, "startup_fenced": expected_fence,
        "updated_at": after["updated_at"] if expected_fence else before["updated_at"],
    }
    assert asdict(queue.get(task.id)) == task_before
    if state == SpawnState.SPAWNED and startup_complete:
        assert probes == ["retained"]


@pytest.mark.parametrize("identity", ["missing", "worktree-only"])
def test_terminal_reserving_without_closed_start_keeps_capacity(tmp_path, identity):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Unresolved start", exclusive_key="resource:retained")
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="original")
    if identity == "worktree-only":
        queue.record_spawn_worktree(reservation.key, "retained-worktree")
    queue.abandon(task.id, permitted=True, reason="Assignment withdrawn")
    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("unresolved start"),
        repo=TEST_REPO, max_concurrent=1, verdict_fn=lambda *_: tracking.GONE,
    )

    assert len(supervisor._active_reservations()) == 1
    assert queue.get_reservation(reservation.key).state == SpawnState.RESERVING


@pytest.mark.parametrize("kind", ["local", "fleet", "script", "cli"])
@pytest.mark.parametrize("verdict", [tracking.LIVE, tracking.UNKNOWN, tracking.GONE, "error"])
def test_closed_terminal_capacity_probes_the_exact_body(tmp_path, kind, verdict):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Retained task")
    reservation, _ = queue.reserve_spawn(task.id)
    handles = {
        "local": "local-body:retained",
        "fleet": "fleet-body:worker-host:retained",
        "script": _encode_script_body_handle("worker", 42, "start-token"),
        "cli": "retained-native",
    }
    queue.record_spawn(
        reservation.key, session_handle=handles[kind], worktree="retained-worktree",
        startup_complete=True, startup_revision=1,
    )
    if kind == "cli":
        queue.claim_one("peer-host/retained-worktree", task_id=task.id)
        queue.start(task.id, "peer-host/retained-worktree", owner_session_id=handles[kind])
    queue.abandon(task.id, permitted=True, reason="Assignment withdrawn")
    calls = []

    def observe(*args):
        calls.append(args)
        if verdict == "error":
            raise OSError("Synthetic liveness transport unavailable")
        return verdict

    client = QueueBackedClient(queue)
    client.attachments = lambda task_id: [asdict(item) for item in queue.attachment_history(task_id)]
    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("capacity is read-only"),
        repo=TEST_REPO, local_body_verdict_fn=observe, fleet_verdict_fn=observe,
        script_body_verdict_fn=observe, verdict_fn=observe,
    )
    assert bool(supervisor._active_reservations()) is (verdict != tracking.GONE)
    expected = {
        "local": ("retained",),
        "fleet": ("worker-host", "retained"),
        "script": (42, "start-token"),
        "cli": ("retained-worktree", "peer-host", "retained-native"),
    }
    assert calls == [expected[kind]]

def test_terminal_cli_without_location_never_uses_an_empty_local_registry(tmp_path):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Unlocated retained native body")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="unlocated-native", worktree="retained-worktree",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.abandon(task.id, permitted=True, reason="Assignment withdrawn")
    client = QueueBackedClient(queue)
    client.attachments = lambda _task_id: []
    probes = []
    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("no launch"), repo=TEST_REPO,
        verdict_fn=lambda *args: probes.append(args) or tracking.GONE,
    )
    assert len(supervisor._active_reservations()) == 1
    assert probes == []


def test_closed_absent_body_frees_capacity_without_releasing_held_cleanup(tmp_path):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    original = queue.create(
        "Retained cleanup", labels=["retained-pool"], exclusive_key="resource:original",
    )
    reservation, _ = queue.reserve_spawn(original.id, reserved_by="original")
    queue.record_spawn(
        reservation.key, session_handle="local-body:retained", worktree="retained-worktree",
        startup_complete=True, startup_revision=1,
    )
    queue.record_spawn_conclusion(
        reservation.key, conclusion_state="held",
        conclusion_detail='{"action":"preserved","reason":"operator-retention"}',
    )
    queue.set_hold(original.id, reason="Keep retained custody", actor="operator")
    queue.abandon(original.id, permitted=True, reason="Assignment withdrawn")
    before = asdict(queue.get_reservation(reservation.key))
    task_before = asdict(queue.get(original.id))
    newer = queue.create(
        "Eligible task", labels=["retained-pool"], exclusive_key="resource:newer",
    )
    calls = []

    def spawn(task):
        calls.append(task["id"])
        return True, {"session": "local-body:newer", "worktree": "newer-worktree"}

    supervisor = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1,
        labels=["retained-pool"], disposable_cli_labels=["retained-pool"],
        local_body_verdict_fn=lambda _: tracking.GONE,
        local_end_fn=lambda _: pytest.fail("retained body must not be ended"),
        conclusion_fn=lambda *_args, **_kwargs: pytest.fail("cleanup is held"),
    )

    assert supervisor.poll_once() == [newer.id]
    assert calls == [newer.id]
    after = asdict(queue.get_reservation(reservation.key))
    assert after == {**before, "startup_fenced": True, "updated_at": after["updated_at"]}
    assert asdict(queue.get(original.id)) == task_before
    sibling = queue.create("Same resource", exclusive_key="resource:original")
    _, acquired = queue.reserve_spawn(sibling.id)
    assert not acquired


@pytest.mark.parametrize("phase", ["capacity", "reconcile"])
def test_terminal_snapshot_cannot_release_a_newer_pending_startup(tmp_path, phase):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    original = queue.create("Original", exclusive_key="resource:original")
    reservation, _ = queue.reserve_spawn(original.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original", startup_complete=True,
        startup_revision=reservation.startup_revision,
    )
    newer = queue.create("Eligible", exclusive_key="resource:newer")
    list_reservations = client.list_reservations
    raced = []
    target = "reserving,spawned,releasing,cold" if phase == "capacity" else "reserving,spawned,cold"

    def race(**kwargs):
        snapshot = list_reservations(**kwargs)
        if kwargs.get("state") == target and not raced:
            raced.append(True)
            queue.record_spawn(
                reservation.key, session_handle="local-body:original",
                startup_complete=False, startup_revision=reservation.startup_revision,
            )
            queue.set_hold(original.id, reason="Keep custody", actor="operator")
            queue.abandon(original.id, permitted=True, reason="Withdraw during pending start")
        return snapshot

    client.list_reservations = race
    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("new startup must not be admitted"),
        repo=TEST_REPO, max_concurrent=1,
        recover=False, heartbeat=False, nudge=False, consistency_sweep=False,
        local_body_verdict_fn=lambda _: pytest.fail("stale startup cannot authorize a probe"),
        local_end_fn=lambda _: pytest.fail("pending startup cannot be ended"),
    )
    if phase == "capacity":
        assert supervisor.poll_once() == []
        assert queue.latest_reservation(newer.id) is None
    else:
        assert supervisor.reconcile() == 0
    assert raced == [True]
    retained = queue.get_reservation(reservation.key)
    assert retained.state == SpawnState.SPAWNED
    assert retained.startup_revision == reservation.startup_revision + 1
    assert retained.startup_complete is False
    assert retained.startup_fenced is False
    assert retained.exclusive_key == "resource:original"
    assert queue.get(original.id).hold_reason == "Keep custody"


@pytest.mark.parametrize("identity", [None, "wt-retained-worktree"])
@pytest.mark.parametrize("durable", [False, True])
def test_incomplete_terminal_identity_refresh_cannot_close_startup(
    tmp_path, identity, durable,
):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Pending publication", labels=["review"], exclusive_key="resource:retained")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle=identity, worktree="retained-worktree",
    )
    worker = "host-a/retained-worktree"
    queue.claim_one(worker, task_id=task.id)
    queue.start(task.id, worker, owner_session_id="retained-native" if durable else None)
    queue.complete(task.id, worker)
    before = asdict(queue.get_reservation(reservation.key))
    probes = []

    def live(*args):
        probes.append(args)
        return {"session_id": "retained-native", "worktree_id": "retained-worktree"}

    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("no launch"),
        repo=TEST_REPO, disposable_cli_labels=["review"],
        liveness_fn=live, local_end_fn=lambda _: pytest.fail("pending startup cannot end"),
    )
    assert supervisor.reconcile() == 0
    assert asdict(queue.get_reservation(reservation.key)) == before
    assert probes == []


@pytest.mark.parametrize("ownership", ["created", "targeted"])
@pytest.mark.parametrize("promoted", [False, True])
def test_absence_before_pending_launch_does_not_admit_another_process(
    tmp_path, monkeypatch, ownership, promoted,
):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    original = queue.create("Pending launch", exclusive_key="resource:original")
    entered, finish = Event(), Event()
    launched, errors = [], []

    def pending_spawn(task):
        entered.set()
        assert finish.wait(5)
        launched.append(task["id"])
        return True, {"session": "original-native", "worktree": "original-worktree"}

    first = Supervisor(
        client, spawn_fn=pending_spawn, repo=TEST_REPO, max_concurrent=1,
        supervisor_id="original-supervisor",
    )

    def prepare(task, reservation):
        queue.record_spawn_worktree(
            reservation["key"], "original-worktree", ownership=ownership,
            creating_host="host-a",
        )
        return task

    monkeypatch.setattr(first, "_prepare_spawn_task", prepare)

    def run_original():
        try:
            first.poll_once()
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=run_original)
    thread.start()
    try:
        assert entered.wait(2)
        if promoted:
            recovery = Supervisor(
                client, spawn_fn=lambda _: pytest.fail("recovery must not launch"),
                repo=TEST_REPO, supervisor_id="recovery-supervisor",
                verdict_fn=lambda *_: tracking.LIVE,
                liveness_fn=lambda *_: {
                    "session_id": "original-native", "worktree_id": "original-worktree",
                },
            )
            assert recovery.reconcile_reserving() == 1
            recovered = queue.latest_reservation(original.id)
            assert recovered.state == SpawnState.SPAWNED
            assert recovered.startup_complete is False
        queue.set_hold(original.id, reason="Retain original workspace", actor="operator")
        queue.abandon(original.id, permitted=True, reason="Withdraw during startup")
        newer = queue.create("New eligible task", exclusive_key="resource:newer")
        second = Supervisor(
            client,
            spawn_fn=lambda task: (
                launched.append(task["id"]) or True,
                {"session": "local-body:newer", "worktree": "newer-worktree"},
            ),
            repo=TEST_REPO, max_concurrent=1, supervisor_id="replacement-supervisor",
            verdict_fn=lambda *_: tracking.GONE,
            worktree_directory_present_fn=lambda *_: False,
        )

        assert second.poll_once() == []
        assert queue.latest_reservation(newer.id) is None
        expected_state = SpawnState.SPAWNED if promoted else SpawnState.RESERVING
        assert queue.latest_reservation(original.id).state == expected_state
        assert queue.get(original.id).hold_reason == "Retain original workspace"
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert launched == [original.id]
