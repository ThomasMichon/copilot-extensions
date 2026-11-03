"""Configured exclusion preserves original publication and execution custody."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from agent_dispatch.coordinator import create_app
from agent_dispatch.pool_instances import PoolInstanceDefinition
from agent_dispatch.pool_publication_custody import PoolCatalogRead
from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_pool_catalog import PoolPublicationScope
from agent_dispatch.queue_records import SpawnReservation
from agent_dispatch.registrar import Filters
from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort, SupervisorScopeIntent


def _project(q: TaskQueue, *members: tuple[str, str]) -> int:
    current = q.pool_configured_cohort()
    previous = current.observation
    assert previous is None or previous.revision is not None
    revision = 0 if previous is None else previous.revision + 1
    cohort = ConfiguredSupervisorCohort(
        "complete", hashlib.sha256(json.dumps([revision, members]).encode()).hexdigest(),
        revision, tuple(SupervisorScopeIntent(machine, machine, env) for machine, env in members),
    )
    return q.observe_pool_configured_cohort(cohort, expected_revision=current.revision).revision


def _publish(
    q: TaskQueue, machine: str, env: str = "linux", *, now: float = 100.0,
) -> PoolPublicationScope:
    cohort = q.pool_configured_cohort().revision
    scope = next(
        (s for s in q.pool_catalog_snapshot().scopes if (s.machine, s.env) == (machine, env)),
        None,
    )
    if scope is None:
        scope = q.begin_pool_publication(
            machine, env, f"publisher-{machine}-{env}", expected_generation=0,
            expected_cohort_revision=cohort,
        )
    return q.publish_pool_catalog_scope(
        machine, env, scope.publisher_id, generation=scope.generation,
        expected_revision=scope.revision, registration_revision=q.pool_registration_revision(),
        expected_cohort_revision=cohort, input_revision="inputs",
        definitions=(PoolInstanceDefinition("worker", machine, env, 0, 1, Filters()),),
        now=now,
    )


def _place(q: TaskQueue, task_id: str) -> SpawnReservation:
    return q.reserve_spawn(
        task_id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=q.pool_catalog_snapshot().revision,
    )[0]


@pytest.fixture
def q(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskQueue:
    queue = TaskQueue(tmp_path / "queue.db")
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    _project(queue, ("host-a", "linux"), ("host-b", "linux"))
    _publish(queue, "host-a")
    _publish(queue, "host-b")
    assert queue.pool_catalog_snapshot().configured_ready
    return queue


@pytest.mark.parametrize("removed_state", ["complete", "pending", "unavailable", "expired"])
def test_removed_scope_is_retained_but_not_required_or_eligible(
    q: TaskQueue, removed_state: str,
) -> None:
    old = q.pool_catalog_snapshot().scopes[0]
    if removed_state == "pending":
        q.begin_pool_publication(
            old.machine, old.env, "successor", expected_generation=old.generation,
            expected_cohort_revision=1,
        )
    elif removed_state == "unavailable":
        q.publish_pool_catalog_scope(
            old.machine, old.env, old.publisher_id, generation=old.generation,
            expected_revision=old.revision, registration_revision=q.pool_registration_revision(),
            expected_cohort_revision=1, input_revision="failed-inputs",
            definitions=(), reason="source unavailable",
        )
    elif removed_state == "expired":
        _publish(q, "host-a", now=60.0)
    retained = q.pool_catalog_snapshot().scopes[0]
    _project(q, ("host-b", "linux"))
    assert q.pool_catalog_snapshot().blockers == ("host-b/linux: configured cohort changed",)
    current = _publish(q, "host-b")
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.ready and snapshot.configured_ready and snapshot.blockers == ()
    assert snapshot.scopes == (retained, current)
    assert snapshot.definitions == current.definitions
    fresh = q.create("work", repo="example")
    _place(q, fresh.id)
    assert q.task_pool_assignment(fresh.id).machine == "host-b"
    restored = TaskQueue(q.db_path).pool_catalog_snapshot(now=100.0)
    assert restored == snapshot


def test_remaining_missing_or_unavailable_scope_still_blocks_every_new_claim(q: TaskQueue) -> None:
    _project(q, ("host-b", "linux"), ("host-c", "linux"))
    b = _publish(q, "host-b")
    assert q.pool_catalog_snapshot().blockers == ("host-c/linux: configured scope has not enrolled",)
    fresh = q.create("work", repo="example")
    with pytest.raises(TaskError, match="complete configured"):
        _place(q, fresh.id)
    c = _publish(q, "host-c")
    q.publish_pool_catalog_scope(
        b.machine, b.env, b.publisher_id, generation=b.generation,
        expected_revision=b.revision, registration_revision=q.pool_registration_revision(),
        expected_cohort_revision=2, input_revision="failed", definitions=(), reason="unreadable",
    )
    assert q.pool_catalog_snapshot().blockers == ("host-b/linux: unavailable",)
    assert c.definitions[0] in q.pool_catalog_snapshot().definitions
    with pytest.raises(TaskError, match="complete configured"):
        _place(q, fresh.id)
    assert q.get(fresh.id).status == Status.QUEUED
    assert q.task_pool_assignment(fresh.id) is None and not q.list_reservations(task_id=fresh.id)


def test_confirmed_empty_excludes_every_scope_without_erasing_original_tables(q: TaskQueue) -> None:
    def retained_rows() -> dict[str, list[tuple]]:
        with q._connect() as conn:
            return {
                name: [tuple(row) for row in conn.execute(f"SELECT * FROM {name}")]
                for name in (
                    "pool_publication_scopes", "pool_publication_generations",
                    "pool_publication_history", "spawn_reservations",
                    "task_pool_assignments", "pool_reservation_bindings",
                )
            }

    before = q.pool_catalog_snapshot()
    original = retained_rows()
    _project(q)
    empty = q.pool_catalog_snapshot()
    assert empty.configured_ready and not empty.ready and empty.blockers == ()
    assert empty.definitions == () and empty.scopes == before.scopes
    assert retained_rows() == original
    fresh = q.create("work", repo="example")
    with pytest.raises(TaskError, match="capacity"):
        _place(q, fresh.id)
    with pytest.raises(TaskError, match="pool placement operation"):
        q.reserve_spawn(fresh.id)
    assert q.get(fresh.id).status == Status.QUEUED


def test_withdrawal_is_exact_to_execution_machine_and_environment(q: TaskQueue) -> None:
    _project(q, ("host-a", "linux"), ("host-a", "windows"), ("host-b", "linux"))
    _publish(q, "host-a")
    _publish(q, "host-a", "windows")
    _publish(q, "host-b")
    _project(q, ("host-a", "windows"), ("host-b", "linux"))
    windows = _publish(q, "host-a", "windows")
    other = _publish(q, "host-b")
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.configured_ready and len(snapshot.scopes) == 3
    assert {definition.identity for definition in snapshot.definitions} == {
        definition.identity for definition in (*windows.definitions, *other.definitions)
    }
    assigned = q.create("work", repo="example", target_machine="host-a")
    _place(q, assigned.id)
    assert q.task_pool_assignment(assigned.id).env == "windows"
    assert all(scope.generation == 1 for scope in snapshot.scopes)


@pytest.mark.parametrize("legacy", [False, True])
def test_remove_readd_requires_current_publication_without_generation_reset(
    q: TaskQueue, legacy: bool,
) -> None:
    initial = q.pool_configured_cohort()
    old = q.pool_catalog_snapshot().scopes[0]
    _project(q)
    removed = q.pool_catalog_snapshot()
    cohort = None if legacy else 1
    with pytest.raises(TaskError, match="revision changed"):
        q.publish_pool_catalog_scope(
            old.machine, old.env, old.publisher_id, generation=old.generation,
            expected_revision=old.revision, registration_revision=q.pool_registration_revision(),
            expected_cohort_revision=cohort, input_revision="old",
            definitions=old.definitions,
        )
    with pytest.raises(TaskError, match="not in"):
        q.begin_pool_publication(
            old.machine, old.env, "premature-successor", expected_generation=old.generation,
            expected_cohort_revision=2,
        )
    assert q.observe_pool_configured_cohort(
        initial.observation, expected_revision=0,
    ) == initial
    assert q.pool_catalog_snapshot() == removed
    _project(q, ("host-a", "linux"))
    assert q.pool_catalog_snapshot().blockers == ("host-a/linux: configured cohort changed",)
    successor = q.begin_pool_publication(
        old.machine, old.env, "successor", expected_generation=old.generation,
        expected_cohort_revision=3,
    )
    assert successor.generation == old.generation + 1
    assert successor.definitions == old.definitions
    assert successor.definitions_cohort_revision == 1
    assert not q.pool_catalog_snapshot().configured_ready
    with pytest.raises(TaskError, match="current enrolled generation"):
        q.publish_pool_catalog_scope(
            old.machine, old.env, old.publisher_id, generation=old.generation,
            expected_revision=old.revision, registration_revision=q.pool_registration_revision(),
            expected_cohort_revision=3, input_revision="late",
            definitions=old.definitions,
        )
    current = _publish(q, "host-a")
    assert q.pool_catalog_snapshot().configured_ready
    assert current.generation == successor.generation and current.definitions_cohort_revision == 3
    with q._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_publication_generations").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM pool_catalog_cohort_history").fetchone()[0] == 3


def test_original_accepted_response_replay_never_reactivates_removed_scope(q: TaskQueue) -> None:
    old = q.pool_catalog_snapshot().scopes[0]
    _project(q)
    before = q.pool_catalog_snapshot()
    replay = q.publish_pool_catalog_scope(
        old.machine, old.env, old.publisher_id, generation=old.generation,
        expected_revision=0, registration_revision=0, expected_cohort_revision=1,
        input_revision="inputs", definitions=old.definitions, now=200.0,
    )
    assert replay == old and replay.valid_until == 130.0
    assert q.begin_pool_publication(
        old.machine, old.env, old.publisher_id, expected_generation=0,
        expected_cohort_revision=1,
    ) == old
    assert q.pool_catalog_snapshot() == before and not before.definitions


def test_removed_claim_keeps_native_start_capacity_and_exclusive_custody(q: TaskQueue) -> None:
    assigned = q.create("assigned", repo="example", target_machine="host-a", exclusive_key="shared")
    reservation = _place(q, assigned.id)
    q.record_spawn_worktree(
        reservation.key, "worktree", ownership="created", creating_host="host-a",
        execution_machine="host-a", driver="cli",
    )
    q.record_spawn(reservation.key, session_handle="native", worktree="worktree")
    assignment = q.task_pool_assignment(assigned.id)
    before = q.get_reservation(reservation.key)
    _project(q, ("host-b", "linux"))
    _publish(q, "host-b")
    assert q.get_reservation(reservation.key) == before
    assert q.task_pool_assignment(assigned.id) == assignment
    assert _place(q, assigned.id) == before
    started = q.start(
        assigned.id, "host-a/worktree", owner_session_id="native",
        expected_generation=q.get(assigned.id).generation,
    )
    assert started.status == Status.STARTED
    contender = q.create("contender", repo="example", exclusive_key="shared")
    with pytest.raises(TaskError, match="exclusive key"):
        _place(q, contender.id)
    _project(q, ("host-a", "linux"), ("host-b", "linux"))
    _publish(q, "host-a")
    _publish(q, "host-b")
    same_pool = q.create("same pool", repo="example", target_machine="host-a")
    with pytest.raises(TaskError, match="capacity"):
        _place(q, same_pool.id)
    independent = q.create("independent", repo="example", target_machine="host-b")
    assert _place(q, independent.id).key != reservation.key
    assert q.get_reservation(reservation.key).session_handle == "native"
    assert q.task_pool_assignment(assigned.id) == assignment


def test_failed_retained_assignment_cannot_move_to_remaining_scope(q: TaskQueue) -> None:
    assigned = q.create("assigned", repo="example", target_machine="host-a")
    reservation = _place(q, assigned.id)
    q.record_spawn_worktree(
        reservation.key, "worktree", ownership="created", creating_host="host-a",
        execution_machine="host-a", driver="cli",
    )
    q.record_spawn(reservation.key, session_handle="native", worktree="worktree")
    q.start(assigned.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(assigned.id).generation)
    q.fail_spawn(reservation.key)
    q.yield_task(assigned.id, "host-a/worktree")
    _project(q, ("host-b", "linux"))
    _publish(q, "host-b")
    assignment = q.task_pool_assignment(assigned.id)
    with pytest.raises(TaskError, match="retained pool assignment is unavailable"):
        _place(q, assigned.id)
    _project(q, ("host-a", "linux"), ("host-b", "linux"))
    _publish(q, "host-a")
    _publish(q, "host-b")
    with pytest.raises(TaskError, match="capacity"):
        _place(q, assigned.id)
    assert q.task_pool_assignment(assigned.id) == assignment
    assert q.get_reservation(reservation.key).session_handle == "native"


def test_http_catalog_preserves_all_original_positions_but_only_current_definitions(q: TaskQueue) -> None:
    old = q.pool_catalog_snapshot().scopes[0]
    _project(q, ("host-b", "linux"))
    current = _publish(q, "host-b")
    client = TestClient(create_app(
        q, token=None, control_token="synthetic-control", enable_mcp=False, verification_interval=0,
    ))
    headers = {"Authorization": "Bearer synthetic-control"}
    response = client.get("/pool-catalog", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["metadata_ready"] and body["configured_metadata_ready"]
    assert body["definitions"] == [definition.to_dict() for definition in current.definitions]
    read = PoolCatalogRead.from_dict({**body, "future": True})
    assert read.scopes == (old, current) and len(read.positions) == 2
    assert not read.permits_scope("host-a", "linux") and read.permits_scope("host-b", "linux")
    before = q.pool_catalog_snapshot()
    refusal = client.post("/pool-catalog/scopes/publish", headers=headers, json={
        "version": 1, "machine": old.machine, "env": old.env, "publisher_id": old.publisher_id,
        "generation": old.generation, "expected_revision": old.revision,
        "registration_revision": 0, "input_revision": "legacy", "definitions": [],
    })
    assert refusal.status_code == 409
    assert "cohort revision" in refusal.json()["detail"]["reason"]
    assert q.pool_catalog_snapshot() == before


def test_withdrawal_and_new_placement_serialize_without_lost_custody(q: TaskQueue) -> None:
    fresh = q.create("work", repo="example", target_machine="host-a")
    revision = q.pool_catalog_snapshot().revision
    barrier = Barrier(2)

    def withdraw() -> None:
        barrier.wait(timeout=5)
        _project(q)

    def place() -> bool:
        barrier.wait(timeout=5)
        try:
            return q.reserve_spawn(
                fresh.id, reserved_by="allocator", driver="cli", pool_catalog_revision=revision,
            )[1]
        except TaskError as exc:
            assert "complete configured" in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        withdrawal = executor.submit(withdraw)
        placement = executor.submit(place)
        withdrawal.result(timeout=5)
        admitted = placement.result(timeout=5)
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.configured_ready and not snapshot.definitions and len(snapshot.scopes) == 2
    assert q.get(fresh.id).status == (Status.CLAIMED if admitted else Status.QUEUED)
    assert len(q.list_reservations(task_id=fresh.id)) == int(admitted)
    assert (q.task_pool_assignment(fresh.id) is not None) == admitted
