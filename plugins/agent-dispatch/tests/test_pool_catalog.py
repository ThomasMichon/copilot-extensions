"""Contained provider transactions, not complete-source or native enrollment proof."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_dispatch.pool_instances import PoolDefinitionError, PoolInstanceDefinition
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.registrar import Filters
from agent_dispatch.registrations import RegistrationKind


@pytest.fixture
def q(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "queue.db")
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    return queue


def _pool(name="review", *, machine="host-a", env="default", priority=0):
    return PoolInstanceDefinition(
        name, machine, env, priority, 2,
        Filters(
            permit={"repo": frozenset({"example"}), "role": frozenset({"review"})},
            reject={"worktree": frozenset({"excluded"})},
        ),
    )


def _begin(q, machine="host-a", env="default", publisher="incarnation-a", expected=0):
    return q.begin_pool_publication(machine, env, publisher, expected_generation=expected)


def _publish(q, scope, definitions=None, **overrides):
    args = {
        "generation": scope.generation, "expected_revision": scope.revision,
        "registration_revision": q.pool_registration_revision(),
        "input_revision": "inputs-1",
        "definitions": (_pool(machine=scope.machine, env=scope.env),)
        if definitions is None else definitions,
    }
    args.update(overrides)
    return q.publish_pool_catalog_scope(scope.machine, scope.env, scope.publisher_id, **args)


def test_empty_provider_and_pending_scope_are_not_ready(q):
    assert not q.pool_catalog_snapshot().ready
    pending = _begin(q)
    snapshot = q.pool_catalog_snapshot()
    assert pending.state == "pending"
    assert snapshot.scopes == (pending,)
    assert not snapshot.ready and snapshot.blockers == ("host-a/default: pending",)


def test_complete_snapshot_preserves_full_predicate_and_order(q):
    scope = _begin(q)
    scope = _publish(q, scope, (_pool("low", priority=-1), _pool("high", priority=8)))
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.ready and snapshot.version == 1 and snapshot.revision == 2
    assert [pool.registration_id for pool in snapshot.definitions] == ["high", "low"]
    assert snapshot.definitions[0].filters == _pool().filters
    assert scope.valid_until == 130.0
    with pytest.raises(TypeError):
        snapshot.definitions[0].filters.permit["repo"] = frozenset({"other"})


@pytest.mark.parametrize("now,ready", [(129.999, True), (130.0, False), (131.0, False)])
def test_freshness_is_enforced_at_exact_deadline_without_retiring_scope(q, now, ready):
    scope = _publish(q, _begin(q))
    snapshot = q.pool_catalog_snapshot(now=now)
    assert snapshot.ready is ready
    assert snapshot.scopes == (scope,)
    assert snapshot.definitions == scope.definitions


def test_all_enrolled_machine_environment_instances_are_required(q):
    a = _publish(q, _begin(q))
    b = _begin(q, machine="host-b")
    assert not q.pool_catalog_snapshot().ready
    b = _publish(q, b)
    c = _begin(q, env="other", publisher="incarnation-c")
    assert not q.pool_catalog_snapshot().ready
    _publish(q, c)
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.ready
    assert len(snapshot.definitions) == 3
    assert len({pool.identity for pool in snapshot.definitions}) == 3
    assert a.definitions[0].registration_id == b.definitions[0].registration_id


def test_empty_complete_scope_is_a_confirmed_withdrawal(q):
    previous = _publish(q, _begin(q))
    current = _publish(q, previous, ())
    assert current.definitions == ()
    assert q.pool_catalog_snapshot().ready
    assert q.pool_catalog_snapshot().definitions == ()
    with q._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_publication_history").fetchone()[0] == 2
        first = conn.execute("SELECT request FROM pool_publication_history ORDER BY revision").fetchone()[0]
        assert "review" in first and "excluded" in first


def test_unavailable_scope_retains_last_confirmed_definitions_but_blocks_all(q):
    scope = _publish(q, _begin(q))
    unavailable = _publish(q, scope, (), reason="overrides unreadable")
    assert unavailable.state == "unavailable"
    assert unavailable.reason == "overrides unreadable"
    assert unavailable.definitions == scope.definitions
    assert (unavailable.definitions_generation, unavailable.definitions_revision) == (
        scope.generation, scope.revision,
    )
    assert not q.pool_catalog_snapshot().ready
    restored = _publish(q, unavailable, ())
    assert restored.state == "complete" and restored.reason is None
    assert restored.definitions == ()


def test_successor_fences_old_publication_and_preserves_incumbent_metadata(q):
    old = _publish(q, _begin(q))
    new = _begin(q, publisher="incarnation-b", expected=old.generation)
    assert new.generation == 2 and new.revision == 0 and new.state == "pending"
    assert new.definitions == old.definitions and not q.pool_catalog_snapshot().ready
    assert (new.definitions_generation, new.definitions_revision) == (old.generation, old.revision)
    with pytest.raises(TaskError, match="current enrolled generation"):
        _publish(q, old, (), reason="late withdrawal")
    with pytest.raises(TaskError, match="re-enrolled"):
        _begin(q, publisher=old.publisher_id, expected=2)
    with pytest.raises(TaskError, match="generation changed"):
        _begin(q, publisher="incarnation-c", expected=0)
    assert _publish(q, new).generation == 2


def test_lost_enrollment_response_replays_without_new_generation_or_clock(q):
    scope = _begin(q)
    revision = q.pool_catalog_snapshot().revision
    assert _begin(q) == scope
    assert q.pool_catalog_snapshot().revision == revision
    with q._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_publication_generations").fetchone()[0] == 1


def test_lost_publication_response_does_not_renew_freshness(q):
    pending = _begin(q)
    current = _publish(q, pending)
    revision = q.pool_catalog_snapshot().revision
    assert _publish(q, pending, now=140.0) == current
    assert q.pool_catalog_snapshot(now=140.0).revision == revision
    assert not q.pool_catalog_snapshot(now=140.0).ready
    fresh = _publish(q, current, now=140.0)
    assert fresh.valid_until == 170.0 and q.pool_catalog_snapshot(now=140.0).ready


def test_changed_payload_or_older_revision_cannot_replay(q):
    pending = _begin(q)
    first = _publish(q, pending)
    with pytest.raises(TaskError, match="revision changed"):
        _publish(q, pending, (_pool("different"),))
    second = _publish(q, first, (_pool("new"),))
    with pytest.raises(TaskError, match="revision changed"):
        _publish(q, pending)
    assert q.pool_catalog_snapshot().scopes == (second,)


def test_registration_changes_invalidate_before_publication_commit(q):
    pending = _begin(q)
    observed = q.pool_registration_revision()
    q.register_registration(RegistrationKind.SUPERVISED_LANE, {"all_repos": True})
    with pytest.raises(TaskError, match="inputs changed"):
        _publish(q, pending, registration_revision=observed)
    assert q.pool_catalog_snapshot().scopes == (pending,)


@pytest.mark.parametrize("change", ["spec", "machine", "env", "kind", "pause", "remove"])
def test_every_effective_registration_mutation_invalidates_confirmed_sets(q, change):
    record = q.register_registration(
        RegistrationKind.SUPERVISED_LANE, {"all_repos": True}, reg_id="direct"
    )
    _publish(q, _begin(q))
    observed = q.pool_registration_revision()
    if change == "pause":
        q.set_registration_status(record.id, "paused")
    elif change == "remove":
        assert q.remove_registration(record.id)
    else:
        q.register_registration(
            RegistrationKind.EMITTER if change == "kind" else record.kind,
            {"id": "tick"} if change == "kind" else
            {"all_repos": True, "max_concurrent": 2} if change == "spec" else record.spec,
            reg_id=record.id, machine="host-b" if change == "machine" else record.machine,
            env="other" if change == "env" else record.env,
        )
    assert q.pool_registration_revision() == observed + 1
    assert q.pool_catalog_snapshot().blockers == ("host-a/default: registration inputs changed",)


def test_idempotent_registration_writes_do_not_manufacture_dirty_inputs(q):
    record = q.register_registration(RegistrationKind.SUPERVISED_LANE, {"all_repos": True})
    _publish(q, _begin(q))
    revision = q.pool_catalog_snapshot().revision
    q.register_registration(record.kind, record.spec, reg_id=record.id, now=110.0)
    q.set_registration_status(record.id, record.status, now=111.0)
    assert not q.remove_registration("absent")
    assert q.pool_catalog_snapshot().revision == revision
    assert q.pool_catalog_snapshot().ready
    assert q.get_registration(record.id).updated_at == 111.0


def test_json_type_changes_invalidate_even_when_python_values_compare_equal(q):
    record = q.register_registration(
        RegistrationKind.EMITTER, {"custom": {"enabled": False}}, reg_id="direct"
    )
    _publish(q, _begin(q))
    observed = q.pool_registration_revision()
    q.register_registration(record.kind, {"custom": {"enabled": 0}}, reg_id=record.id)
    assert q.pool_registration_revision() == observed + 1
    assert not q.pool_catalog_snapshot().ready


def test_registry_failure_rolls_back_definition_and_invalidation(q):
    record = q.register_registration(RegistrationKind.SUPERVISED_LANE, {"all_repos": True})
    _publish(q, _begin(q))
    before = q.pool_catalog_snapshot()
    with q._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_pool_clock BEFORE UPDATE ON pool_catalog_clock"
            " BEGIN SELECT RAISE(ABORT, 'synthetic registry failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic registry failure"):
        q.remove_registration(record.id)
    assert q.get_registration(record.id) == record
    assert q.pool_catalog_snapshot() == before


@pytest.mark.parametrize("field,value", [
    ("machine", None), ("machine", " "), ("env", ""), ("publisher_id", " spaced "),
    ("generation", True), ("generation", -1), ("expected_revision", 1.5),
    ("registration_revision", False), ("input_revision", None),
    ("now", float("nan")), ("now", float("inf")), ("reason", ""),
])
def test_invalid_publication_refuses_without_mutation(q, field, value):
    scope = _begin(q)
    args = {
        "machine": scope.machine, "env": scope.env, "publisher_id": scope.publisher_id,
        "generation": scope.generation, "expected_revision": scope.revision,
        "registration_revision": 0, "input_revision": "inputs-1", "definitions": (_pool(),),
    }
    args[field] = value
    before = q.pool_catalog_snapshot()
    with pytest.raises(TaskError):
        q.publish_pool_catalog_scope(**args)
    assert q.pool_catalog_snapshot() == before


@pytest.mark.parametrize("definitions,reason", [
    ([], None), (({},), None), ((_pool(machine="host-b"),), None),
    ((_pool(env="other"),), None), ((_pool(), _pool()), None),
    ((_pool(),), "partial discovery"),
])
def test_partial_invalid_or_foreign_sets_refuse_atomically(q, definitions, reason):
    scope = _begin(q)
    with pytest.raises((TaskError, PoolDefinitionError)):
        _publish(q, scope, definitions, reason=reason)
    assert q.pool_catalog_snapshot().scopes == (scope,)


def test_failed_write_rolls_back_scope_clock_and_history(q):
    scope = _begin(q)
    before = q.pool_catalog_snapshot()
    with q._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_pool_history BEFORE INSERT ON pool_publication_history"
            " BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic storage failure"):
        _publish(q, scope)
    assert q.pool_catalog_snapshot() == before


def test_concurrent_publication_has_one_cas_winner(q):
    scope = _begin(q)
    barrier = Barrier(2)

    def publish(name):
        barrier.wait(timeout=5)
        try:
            return _publish(q, scope, (_pool(name),))
        except TaskError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, ("one", "two")))
    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    assert q.pool_catalog_snapshot().scopes == tuple(winners)


def test_concurrent_enrollment_has_one_cas_winner(q):
    barrier = Barrier(2)

    def enroll(publisher):
        barrier.wait(timeout=5)
        try:
            return _begin(q, publisher=publisher)
        except TaskError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(enroll, ("incarnation-a", "incarnation-b")))
    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    assert q.pool_catalog_snapshot().scopes == tuple(winners)


def test_reopening_queue_retains_generation_history_and_snapshot(q, monkeypatch):
    scope = _publish(q, _begin(q))
    restored = TaskQueue(q.db_path)
    monkeypatch.setattr(restored, "_now", lambda now=None: 110.0 if now is None else now)
    assert restored.pool_catalog_snapshot().scopes == (scope,)
    assert restored.pool_catalog_snapshot().ready
    with pytest.raises(TaskError, match="re-enrolled"):
        restored.begin_pool_publication(
            scope.machine, scope.env, scope.publisher_id, expected_generation=scope.generation
        )
    with restored._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM spawn_reservations").fetchone()[0] == 0


def test_begin_and_snapshot_refuse_nonfinite_clock(q):
    for now in (float("nan"), float("inf")):
        with pytest.raises(TaskError, match="finite"):
            q.begin_pool_publication("host-a", "default", "new", expected_generation=0, now=now)
        with pytest.raises(TaskError, match="finite"):
            q.pool_catalog_snapshot(now=now)
    assert q.pool_catalog_snapshot().scopes == ()
