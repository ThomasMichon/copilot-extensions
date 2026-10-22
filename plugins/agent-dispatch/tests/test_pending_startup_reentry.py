"""A later operation cannot hide an earlier startup caller still in flight."""

from __future__ import annotations

from threading import Event, Thread

import pytest

from agent_dispatch import tracking
from agent_dispatch.queue import SpawnState, TaskError
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests.test_supervisor import OwnerResultQueue, QueueBackedClient


@pytest.mark.parametrize("release", [False, True])
def test_recovery_redrive_cannot_close_a_pending_original_start(tmp_path, monkeypatch, release):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("Original startup")
    entered, finish = Event(), Event()
    launched, errors, redriven = [], [], []

    def launch(_task):
        entered.set()
        assert finish.wait(5)
        launched.append(task.id)
        return True, {"session": "local-body:original-native", "worktree": "original-worktree"}

    original = Supervisor(client, spawn_fn=launch, repo=TEST_REPO)

    def prepare(current, reservation):
        queue.record_spawn_worktree(
            reservation["key"], "original-worktree", ownership="targeted",
        )
        return current

    monkeypatch.setattr(original, "_prepare_spawn_task", prepare)

    def run():
        try:
            original.poll_once()
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        recovery = Supervisor(
            client, spawn_fn=lambda _: pytest.fail("no second startup"),
            repo=TEST_REPO, verdict_fn=lambda *_: tracking.LIVE,
            local_end_fn=lambda _: True, local_acp_session_fn=lambda _: None,
            liveness_fn=lambda *_: {
                "session_id": "local-body:original-native", "worktree_id": "original-worktree",
            },
            redrive_fn=lambda *_: redriven.append(task.id) or True,
        )
        assert recovery.reconcile_reserving() == 1
        before = queue.latest_reservation(task.id)
        assert before.state == SpawnState.SPAWNED
        assert before.startup_complete is False
        recovery.redrive_unclaimed_spawns()
        if release:
            queue.request_spawn_release(before.key, detail="release during blocked launch")
        queue.abandon(task.id, permitted=True, reason="withdraw while launch is pending")
        recovery.verdict_fn = lambda *_: tracking.GONE
        assert len(recovery._active_reservations()) == 1
        assert redriven == []
        retained = queue.latest_reservation(task.id)
        assert retained.startup_revision == before.startup_revision
        assert retained.startup_complete is False
        assert retained.startup_fenced is False
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert launched == [task.id]
    completed = queue.latest_reservation(task.id)
    assert completed.startup_complete is True
    assert completed.session_handle == "local-body:original-native"
    if release:
        assert completed.state == SpawnState.RELEASING
        assert completed.release_requested is True
        recovery.local_body_verdict_fn = lambda _: tracking.GONE
        assert recovery.release_requested_bodies() == 1
        assert queue.latest_reservation(task.id).state == SpawnState.FAILED
        assert recovery._active_reservations() == []


@pytest.mark.parametrize("release", [None, "direct", "suspended"])
def test_concurrent_cooling_cannot_hide_a_pending_resume(tmp_path, release):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("Suspended review", labels=["review"])
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original", startup_complete=True,
        startup_revision=reservation.startup_revision,
    )
    queue.claim_one("original-owner", task_id=task.id)
    queue.start(task.id, "original-owner")
    queue.suspend(task.id, "original-owner", reason="turn ended")
    queue.record_cold(reservation.key)
    queue.submit_steer(task.id, fields={"decision": "continue"}, sender="operator")
    entered, finish = Event(), Event()
    resumed, errors = [], []

    def resume(_sid, _prompt):
        entered.set()
        assert finish.wait(5)
        resumed.append(task.id)
        return True

    original = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("must reuse the original body"),
        repo=TEST_REPO, labels=["review"], local_resume_fn=resume,
        local_body_target_dir_fn=lambda _: None,
    )

    def run():
        try:
            original.release_resumed_cold_tasks()
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        before = queue.get_reservation(reservation.key)
        assert before.state == SpawnState.SPAWNED
        assert before.startup_complete is False
        concurrent = Supervisor(
            client, spawn_fn=lambda _: pytest.fail("no second startup"),
            repo=TEST_REPO, labels=["review"],
            local_body_verdict_fn=lambda _: tracking.GONE,
            local_end_fn=lambda _: True, local_acp_session_fn=lambda _: None,
            local_body_target_dir_fn=lambda _: None,
            local_cold_fn=lambda _: pytest.fail("pending resume must not be stopped"),
        )
        concurrent.cool_dormant_bodies()
        if release == "direct":
            queue.request_spawn_release(reservation.key, detail="requested during pending resume")
        elif release == "suspended":
            queue.release_suspended(task.id, "original-owner", reason="requested during pending resume")
        if release:
            with pytest.raises(TaskError, match="startup"):
                queue.retire_spawn(reservation.key, exact_absence=True)
        queue.abandon(task.id, permitted=True, reason="withdraw during pending resume")
        assert len(concurrent._active_reservations()) == 1
        retained = queue.get_reservation(reservation.key)
        assert retained.state == (SpawnState.RELEASING if release else SpawnState.SPAWNED)
        assert retained.startup_revision == before.startup_revision
        assert retained.startup_complete is False
        assert retained.startup_fenced is False
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert resumed == [task.id]
    completed = queue.get_reservation(reservation.key)
    assert completed.startup_complete is (release is not None)
    if release:
        assert completed.state == SpawnState.RELEASING
        assert completed.release_requested is True
        assert concurrent.release_requested_bodies() == 1
        assert queue.get_reservation(reservation.key).state == (
            SpawnState.SETTLED if release == "suspended" else SpawnState.FAILED
        )
        assert concurrent._active_reservations() == []


def test_pending_start_cannot_be_replaced_or_cooled_in_storage(tmp_path):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Unproven startup")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(reservation.key, session_handle="local-body:original")
    for operation in (
        lambda: queue.record_spawn(
            reservation.key, session_handle="local-body:original",
            startup_complete=False, startup_revision=reservation.startup_revision,
        ),
        lambda: queue.record_cold(reservation.key),
    ):
        with pytest.raises(TaskError, match="previous startup remains incomplete"):
            operation()
    retained = queue.get_reservation(reservation.key)
    assert retained.startup_revision == reservation.startup_revision
    assert retained.startup_complete is False
    assert retained.state == SpawnState.SPAWNED


def test_cooling_preserves_completion_and_rejects_stale_revision(tmp_path):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Completed startup")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    cold = queue.record_cold(reservation.key, startup_revision=reservation.startup_revision)
    assert cold.startup_complete is True
    pending = queue.record_spawn(
        reservation.key, session_handle=cold.session_handle, startup_complete=False,
        startup_revision=cold.startup_revision,
    )
    queue.record_spawn(
        reservation.key, session_handle=cold.session_handle, startup_complete=True,
        startup_revision=pending.startup_revision,
    )
    with pytest.raises(TaskError, match="startup revision changed"):
        queue.record_cold(reservation.key, startup_revision=cold.startup_revision)
    assert queue.get_reservation(reservation.key).state == SpawnState.SPAWNED


def test_legacy_cold_resume_cannot_inherit_predecessor_completion(tmp_path):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Legacy resume")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.record_cold(reservation.key)
    resumed = queue.record_spawn(reservation.key, session_handle="local-body:original")
    assert resumed.startup_complete is False
    assert resumed.startup_revision == reservation.startup_revision + 1
    with pytest.raises(TaskError, match="startup revision changed"):
        queue.record_spawn(
            reservation.key, session_handle="local-body:original",
            startup_complete=True, startup_revision=reservation.startup_revision,
        )


def test_stale_cooling_snapshot_does_not_stop_newer_pending_resume(tmp_path, monkeypatch):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("Suspended review", labels=["review"])
    reservation, _ = queue.reserve_spawn(task.id)
    completed = queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.claim_one("original-owner", task_id=task.id)
    queue.start(task.id, "original-owner")
    queue.suspend(task.id, "original-owner", reason="turn ended")
    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("must reuse body"), repo=TEST_REPO,
        labels=["review"], local_body_verdict_fn=lambda _: tracking.LIVE,
        local_cold_fn=lambda _: pytest.fail("stale snapshot must not stop pending startup"),
    )
    list_reservations = supervisor._pool_reservations

    def race(**kwargs):
        snapshots = list_reservations(**kwargs)
        queue.record_cold(reservation.key, startup_revision=completed.startup_revision)
        queue.record_spawn(
            reservation.key, session_handle=completed.session_handle,
            startup_complete=False, startup_revision=completed.startup_revision,
        )
        return snapshots

    monkeypatch.setattr(supervisor, "_pool_reservations", race)
    assert supervisor.cool_dormant_bodies() == 0
    assert queue.get_reservation(reservation.key).startup_complete is False


def test_stale_cold_snapshot_cannot_hide_newer_pending_resume_capacity(tmp_path, monkeypatch):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("Suspended review", labels=["review"])
    reservation, _ = queue.reserve_spawn(task.id)
    completed = queue.record_spawn(
        reservation.key, session_handle="local-body:original",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.record_cold(reservation.key, startup_revision=completed.startup_revision)
    supervisor = Supervisor(
        client, spawn_fn=lambda _: pytest.fail("pending resume keeps capacity"),
        repo=TEST_REPO, labels=["review"],
    )
    list_reservations = supervisor._pool_reservations

    def race(**kwargs):
        snapshots = list_reservations(**kwargs)
        queue.record_spawn(
            reservation.key, session_handle=completed.session_handle,
            startup_complete=False, startup_revision=completed.startup_revision,
        )
        return snapshots

    monkeypatch.setattr(supervisor, "_pool_reservations", race)
    assert len(supervisor._active_reservations()) == 1


@pytest.mark.parametrize("terminal", [False, True])
def test_legacy_unproven_cold_cannot_hide_capacity_resume_or_cleanup(tmp_path, terminal):
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("Legacy cold", labels=["review"])
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(reservation.key, session_handle="local-body:legacy")
    queue.claim_one("original-owner", task_id=task.id)
    queue.start(task.id, "original-owner")
    queue.suspend(task.id, "original-owner", reason="turn ended")
    # Historical cold rows have no original startup completion evidence.
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state=? WHERE key=?",
            (SpawnState.COLD, reservation.key),
        )
    queue.submit_steer(task.id, fields={"decision": "continue"}, sender="operator")
    if terminal:
        queue.abandon(task.id, permitted=True, reason="withdraw legacy assignment")
    supervisor = Supervisor(
        QueueBackedClient(queue), spawn_fn=lambda _: pytest.fail("unproven startup"),
        repo=TEST_REPO, labels=["review"],
        local_resume_fn=lambda *_: pytest.fail("unproven cold must not resume"),
        local_body_target_dir_fn=lambda _: pytest.fail("must not begin cold recovery"),
        local_body_verdict_fn=lambda _: tracking.GONE,
    )
    assert len(supervisor._active_reservations()) == 1
    assert supervisor.release_resumed_cold_tasks() == 0
    assert supervisor.reconcile() == 0
    retained = queue.get_reservation(reservation.key)
    assert retained.state == SpawnState.COLD
    assert retained.startup_complete is False
    assert retained.startup_fenced is False
