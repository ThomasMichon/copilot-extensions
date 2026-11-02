"""Retained workspace location constrains placement before claiming capacity."""

from __future__ import annotations

import json

import pytest

from agent_dispatch.queue import SpawnReservation, Status, TaskError, TaskQueue
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, task

q = placement_controls.q


def retired_workspace(
    q: TaskQueue, *, execution_machine: str | None = "host-a", driver: str = "cli",
    pooled: bool = True,
) -> SpawnReservation:
    if pooled:
        publish(q, pool(), pool(machine="host-b", priority=10))
    predecessor = task(q, dedup_key="logical-workspace", target_machine="host-a")
    original, _ = (
        place(q, predecessor) if pooled else q.reserve_spawn(predecessor.id, driver=driver)
    )
    q.record_spawn_worktree(
        original.key, "worktree", ownership="created", creating_host="creation-host",
        execution_machine=execution_machine, driver=driver,
        allocation_project="example", worktree_path="/synthetic/worktree",
    )
    q.record_spawn(original.key, session_handle="old-native", worktree="worktree")
    q.request_spawn_release(original.key, finished_startup_revision=1)
    retired = q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    q.abandon(predecessor.id, permitted=True, reason="synthetic prior request")
    return retired


@pytest.mark.parametrize("recorded_host", ["host-a", " HOST-A "])
@pytest.mark.parametrize("selector", ["unbound", "target", "required"])
def test_distinct_successor_retains_workspace_host_before_claim(
    q: TaskQueue, recorded_host: str, selector: str,
) -> None:
    predecessor = retired_workspace(q, execution_machine=recorded_host)
    successor = task(
        q, dedup_key="logical-workspace",
        target_machine="HOST-A" if selector == "target" else None,
        requires=["machine:HOST-A"] if selector == "required" else [],
    )
    replacement, reserved = place(q, successor)
    assert reserved and replacement.task_id != predecessor.task_id
    assert replacement.worktree == predecessor.worktree
    assert replacement.worktree_path == predecessor.worktree_path
    assert replacement.allocation_project == predecessor.allocation_project
    assert replacement.creating_host == "creation-host"
    assert replacement.execution_machine == recorded_host
    assert replacement.worktree_ownership == "reused"
    assert replacement.session_handle is None and replacement.conversation_retired
    assert q.task_pool_assignment(successor.id).machine == "host-a"
    assert q.get(successor.id).status == Status.CLAIMED
    assert q.get(successor.id).target_machine == successor.target_machine
    q.record_spawn_worktree(
        replacement.key, "worktree", ownership="reused", execution_machine="host-a",
        driver="cli", allocation_project="example", worktree_path="/synthetic/worktree",
    )
    q.record_spawn(replacement.key, session_handle="replacement", worktree="worktree")
    assert q.start(
        successor.id, "host-a/worktree", owner_session_id="replacement",
        expected_generation=q.get(successor.id).generation,
    ).status == Status.STARTED
    assert q.get_reservation(replacement.key).execution_machine == recorded_host


@pytest.mark.parametrize("selector", ["target", "required", "excluded", "pool", "full", "unavailable"])
def test_incompatible_successor_keeps_task_and_capacity_unchanged(
    q: TaskQueue, selector: str,
) -> None:
    predecessor = retired_workspace(q, pooled=selector != "unavailable")
    if selector == "unavailable":
        publish(q, pool(machine="host-b"))
    if selector == "full":
        place(q, task(q, target_machine="host-a"))
    successor = task(
        q, dedup_key="logical-workspace",
        target_machine="host-b" if selector == "target" else None,
        requires=(
            ["machine:host-b"] if selector == "required" else
            ["pool:other"] if selector == "pool" else []
        ),
        excludes=["machine:host-a"] if selector == "excluded" else [],
    )
    with q._connect() as conn:
        bindings = conn.execute("SELECT * FROM pool_reservation_bindings").fetchall()
    with pytest.raises(TaskError, match="no eligible pool"):
        place(q, successor)
    assert q.get(successor.id) == successor
    assert q.list_reservations(task_id=successor.id) == []
    assert q.task_pool_assignment(successor.id) is None
    assert q.get_reservation(predecessor.key) == predecessor
    with q._connect() as conn:
        assert conn.execute("SELECT * FROM pool_reservation_bindings").fetchall() == bindings
    available, reserved = place(q, task(q, target_machine="host-b"))
    assert reserved and q.task_pool_assignment(available.task_id).machine == "host-b"


def test_legacy_unknown_execution_host_is_not_inferred_from_creation(q: TaskQueue) -> None:
    retired_workspace(q, execution_machine=None, pooled=False)
    publish(q, pool(machine="host-b"))
    successor = task(q, dedup_key="logical-workspace")
    replacement, reserved = place(q, successor)
    assert reserved and replacement.worktree == "worktree"
    assert replacement.execution_machine is None
    assert replacement.creating_host == "creation-host"
    assert q.task_pool_assignment(successor.id).machine == "host-b"


def test_nonpool_successor_keeps_known_workspace_host(q: TaskQueue) -> None:
    retired_workspace(q, pooled=False)
    successor = task(q, dedup_key="logical-workspace")
    replacement, reserved = q.reserve_spawn(successor.id, driver="cli")
    assert reserved and replacement.execution_machine == "host-a"
    assert replacement.worktree == "worktree"
    assert q.task_pool_assignment(successor.id) is None


def test_discarded_optional_driver_workspace_does_not_pin_new_claim(q: TaskQueue) -> None:
    retired_workspace(q, driver="ordinary-bridge", pooled=False)
    publish(q, pool(machine="host-b"))
    successor = task(q, dedup_key="logical-workspace")
    replacement, reserved = place(q, successor)
    assert reserved and replacement.worktree is None
    assert replacement.execution_machine is None
    assert replacement.creating_host is None
    assert q.task_pool_assignment(successor.id).machine == "host-b"


@pytest.mark.parametrize("machine", ["host-a", "HOST-A"])
@pytest.mark.parametrize("excluded", ["machine:host-a", "machine:HOST-A", "machine: Host-A "])
def test_normalized_machine_exclusion_refuses_before_claim(
    q: TaskQueue, machine: str, excluded: str,
) -> None:
    publish(q, pool(machine=machine))
    requested = task(q, excludes=[excluded])
    with pytest.raises(TaskError, match="no eligible pool"):
        place(q, requested)
    assert q.get(requested.id) == requested
    assert q.list_reservations(task_id=requested.id) == []
    assert q.task_pool_assignment(requested.id) is None
    assert place(q, task(q))[1]


@pytest.mark.parametrize("boundary", ["workspace", "publication", "start"])
@pytest.mark.parametrize("excluded", ["machine:HOST-A", "machine: Host-A "])
def test_changed_machine_exclusion_blocks_actual_execution_without_mutation(
    q: TaskQueue, boundary: str, excluded: str,
) -> None:
    publish(q, pool())
    requested = task(q)
    reservation, _ = place(q, requested)
    if boundary != "workspace":
        record_native(q, reservation)
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET excludes = ? WHERE id = ?", (json.dumps([excluded]), requested.id),
        )
    task_before = q.get(requested.id)
    reservation_before = q.get_reservation(reservation.key)
    assignment_before = q.task_pool_assignment(requested.id)
    with pytest.raises(TaskError, match="excluded"):
        if boundary == "workspace":
            q.record_spawn_worktree(
                reservation.key, "worktree", ownership="created", creating_host="host-a",
                execution_machine="host-a", driver="cli",
            )
        elif boundary == "publication":
            q.record_spawn(reservation.key, session_handle="native", worktree="worktree")
        else:
            q.start(
                requested.id, "host-a/worktree", owner_session_id="native",
                expected_generation=task_before.generation,
            )
    assert q.get(requested.id) == task_before
    assert q.get_reservation(reservation.key) == reservation_before
    assert q.task_pool_assignment(requested.id) == assignment_before


def test_normalized_machine_exclusion_selects_a_different_free_host(q: TaskQueue) -> None:
    publish(q, pool(priority=10), pool(machine="host-b"))
    requested = task(q, excludes=["machine: HOST-A "])
    assert place(q, requested)[1]
    assert q.task_pool_assignment(requested.id).machine == "host-b"


@pytest.mark.parametrize("machine", ["host-a", "HOST-A"])
@pytest.mark.parametrize("worker", ["host-a/worktree", "HOST-A/worktree", " Host-A /worktree"])
def test_affirmative_start_normalizes_only_worker_machine(
    q: TaskQueue, machine: str, worker: str,
) -> None:
    publish(q, pool(machine=machine))
    requested = task(q)
    reservation, _ = place(q, requested)
    record_native(q, reservation)
    generation = q.get(requested.id).generation
    started = q.start(
        requested.id, worker, owner_session_id="native", expected_generation=generation,
    )
    assert started.status == Status.STARTED and started.owner == worker
    assert started.owner_session_id == "native" and started.generation == generation
    assignment = q.task_pool_assignment(requested.id)
    assert assignment.machine == machine
    assert q.get_reservation(reservation.key).execution_machine == "host-a"
    replay = q.start(
        requested.id, worker, owner_session_id="native", expected_generation=generation,
        expected_owner_session_id="native",
    )
    assert replay == started
    assert q.task_pool_assignment(requested.id) == assignment


@pytest.mark.parametrize("worker", [
    "host-b/worktree", "host-a/WORKTREE", "host-a/ worktree ", "host-a/worktree/other",
    "host-a", "/worktree",
])
def test_normalized_worker_host_cannot_change_exact_workspace_or_machine(
    q: TaskQueue, worker: str,
) -> None:
    publish(q, pool(machine="HOST-A"))
    requested = task(q)
    reservation, _ = place(q, requested)
    record_native(q, reservation)
    before = q.get(requested.id)
    recorded = q.get_reservation(reservation.key)
    with pytest.raises(TaskError, match="current reservation"):
        q.start(
            requested.id, worker, owner_session_id="native", expected_generation=before.generation,
        )
    assert q.get(requested.id) == before
    assert q.get_reservation(reservation.key) == recorded


def test_normalized_start_preserves_existing_exact_owner_fence(q: TaskQueue) -> None:
    publish(q, pool())
    requested = task(q)
    reservation, _ = place(q, requested)
    record_native(q, reservation)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET owner = ? WHERE id = ?", ("host-a/worktree", requested.id))
    before = q.get(requested.id)
    with pytest.raises(TaskError, match="current reservation"):
        q.start(
            requested.id, "HOST-A/worktree", owner_session_id="native",
            expected_generation=before.generation,
        )
    assert q.get(requested.id) == before
