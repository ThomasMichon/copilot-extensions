"""Contained configured-owner projection; no runtime publisher or adoption."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import sqlite3
from threading import Barrier

import pytest

from agent_dispatch.pool_instances import PoolInstanceDefinition
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.pool_publication_response import read_pool_scope
from agent_dispatch.coordinator_pool_catalog import _scope_payload
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.registrar import Filters
from agent_dispatch.registrar_configuration import (
    ConfiguredSupervisorCohort, SupervisorScopeIntent, observe_configured_supervisors,
)


@pytest.fixture
def q(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "queue.db")
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    return queue


def _cohort(*scopes, revision=0, source="source"):
    return ConfiguredSupervisorCohort(
        "complete", hashlib.sha256(source.encode()).hexdigest(), revision,
        tuple(SupervisorScopeIntent("logical-host", machine, env) for machine, env in scopes),
    )


def _observe(q, cohort, expected=0):
    return q.observe_pool_configured_cohort(cohort, expected_revision=expected)


def _begin(q, machine="host-linux", env="linux", publisher="publisher-a", cohort=1, expected=0):
    return q.begin_pool_publication(
        machine, env, publisher, expected_generation=expected, expected_cohort_revision=cohort,
    )


def _publish(q, scope, *, cohort=1, now=100.0, reason=None):
    return q.publish_pool_catalog_scope(
        scope.machine, scope.env, scope.publisher_id, generation=scope.generation,
        expected_revision=scope.revision, registration_revision=q.pool_registration_revision(),
        expected_cohort_revision=cohort, input_revision="inputs-a",
        definitions=() if reason else (
            PoolInstanceDefinition("worker", scope.machine, scope.env, 0, 2, Filters()),
        ),
        reason=reason, now=now,
    )


def test_registrar_observation_projects_whole_actual_cohort_not_reported_rows(q, tmp_path):
    path = tmp_path / "pointers.json"
    path.write_text(json.dumps({
        "version": 1, "pointers": [], "configured_supervisors": {
            "revision": 0, "members": [
                {"machine": "logical-host", "execution_machine": "host-linux", "env": "linux"},
                {"machine": "logical-host", "execution_machine": "host-windows", "env": "windows"},
            ],
        },
    }))
    original = observe_configured_supervisors(path)
    projected = _observe(q, original)
    assert projected.observation == original
    assert projected.revision == 1
    _publish(q, _begin(q))
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.blockers == ("host-windows/windows: configured scope has not enrolled",)
    assert not snapshot.ready and not snapshot.configured_ready
    _publish(q, _begin(q, "host-windows", "windows", "publisher-b"))
    assert q.pool_catalog_snapshot().configured_ready
    assert len(q.pool_catalog_snapshot().definitions) == 2
    with q._connect() as conn:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM spawn_reservations").fetchone()[0] == 0


def test_explicit_empty_differs_from_no_observation(q):
    before = q.pool_catalog_snapshot()
    assert before.configured_cohort.revision == 0
    assert before.configured_cohort.observation is None
    assert not before.configured_ready
    _observe(q, _cohort())
    empty = q.pool_catalog_snapshot()
    assert empty.configured_ready and not empty.ready and empty.blockers == ()
    with pytest.raises(TaskError, match="not in"):
        _begin(q)


@pytest.mark.parametrize("state", ["unconfirmed", "unavailable"])
def test_failed_owner_observation_blocks_without_erasing_history_or_custody(q, state):
    _observe(q, _cohort(("host-linux", "linux")))
    published = _publish(q, _begin(q))
    failed = ConfiguredSupervisorCohort(state, None, None, (), "source-unreadable")
    _observe(q, failed, expected=1)
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.scopes == (published,) and snapshot.definitions == published.definitions
    assert not snapshot.ready and not snapshot.configured_ready
    with pytest.raises(TaskError, match="unconfirmed or unavailable"):
        _publish(q, published, cohort=2)
    restored = _observe(q, _cohort(("host-linux", "linux")), expected=2)
    assert restored.revision == 3
    assert not q.pool_catalog_snapshot().ready
    _publish(q, published, cohort=3)
    assert q.pool_catalog_snapshot().ready
    with q._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM pool_publication_history").fetchone()[0] == 2


@pytest.mark.parametrize("cohort", [None, 0, 2])
def test_unfenced_or_stale_fresh_write_refuses_without_enrollment(q, cohort):
    _observe(q, _cohort(("host-linux", "linux")))
    before = q.pool_catalog_snapshot()
    with pytest.raises(TaskError, match="revision changed or was not supplied"):
        _begin(q, cohort=cohort)
    assert q.pool_catalog_snapshot() == before


def test_logical_identity_is_not_an_execution_scope(q):
    _observe(q, _cohort(("host-linux", "linux")))
    with pytest.raises(TaskError, match="not in"):
        _begin(q, machine="logical-host")
    with pytest.raises(TaskError, match="not in"):
        _begin(q, env="windows")
    assert q.pool_catalog_snapshot().scopes == ()


def test_changed_cohort_fences_original_writes_but_recovers_accepted_results(q):
    initial = _cohort(("host-linux", "linux"))
    observation = _observe(q, initial)
    pending = _begin(q)
    published = _publish(q, pending)
    _observe(q, _cohort(revision=1, source="changed"), expected=1)
    before = q.pool_catalog_snapshot()
    assert not before.configured_ready
    assert "host-linux/linux: enrolled scope is not configured" in before.blockers
    assert _publish(q, pending, now=140.0) == published
    assert _begin(q) == published
    with pytest.raises(TaskError):
        _publish(q, published)
    with pytest.raises(TaskError, match="not in"):
        _publish(q, published, cohort=2)
    assert _observe(q, initial) == observation
    assert observation.revision == 1
    assert q.pool_catalog_snapshot() == before
    assert q.pool_catalog_snapshot(now=140.0).scopes[0].valid_until == 130.0


def test_pointer_byte_change_invalidates_even_with_unchanged_configured_revision(q):
    _observe(q, _cohort(("host-linux", "linux")))
    published = _publish(q, _begin(q))
    _observe(q, _cohort(("host-linux", "linux"), source="pointer-edit"), expected=1)
    assert q.pool_registration_revision() == 0
    assert q.pool_catalog_snapshot().blockers == ("host-linux/linux: configured cohort changed",)
    with pytest.raises(TaskError):
        _publish(q, published)
    _publish(q, published, cohort=2)
    assert q.pool_catalog_snapshot().ready


def test_legacy_projection_retains_scope_without_backfilling_or_silent_withdrawal(q):
    pending = _begin(q, cohort=None)
    published = _publish(q, pending, cohort=None)
    _observe(q, _cohort(("host-linux", "linux")))
    snapshot = q.pool_catalog_snapshot()
    assert snapshot.scopes == (published,) and published.definitions_cohort_revision is None
    assert not snapshot.ready
    assert _publish(q, pending, cohort=None) == published
    with pytest.raises(TaskError):
        _publish(q, published, cohort=None)
    updated = _publish(q, published)
    assert updated.enrollment_cohort_revision is None
    assert updated.definitions_cohort_revision == 1
    assert q.pool_catalog_snapshot().ready
    _observe(q, _cohort(revision=1), expected=1)
    assert q.pool_catalog_snapshot().scopes == (updated,)
    assert not q.pool_catalog_snapshot().configured_ready


def test_unavailable_publication_retains_definition_cohort_attribution(q):
    _observe(q, _cohort(("host-linux", "linux")))
    published = _publish(q, _begin(q))
    _observe(q, _cohort(("host-linux", "linux"), source="changed"), expected=1)
    unavailable = _publish(q, published, cohort=2, reason="read failed")
    assert unavailable.definitions_cohort_revision == 1
    assert unavailable.definitions == published.definitions
    assert not q.pool_catalog_snapshot().ready
    with pytest.raises(TaskError, match="revision changed"):
        _publish(q, unavailable, cohort=1)


def test_identical_observations_and_original_retry_do_not_advance_clock(q):
    cohort = _cohort(("host-linux", "linux"))
    accepted = _observe(q, cohort)
    before = q.pool_catalog_snapshot()
    assert _observe(q, cohort) == accepted
    assert _observe(q, cohort, expected=1) == accepted
    assert q.pool_catalog_snapshot() == before
    with q._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 1


@pytest.mark.parametrize("change", [
    {"revision": -1}, {"revision": True}, {"source_revision": None},
    {"source_revision": "not-a-digest"}, {"members": []}, {"reason": "partial"},
    {"members": (SupervisorScopeIntent("logical", " ", "linux"),)},
    {"members": (SupervisorScopeIntent("logical", "host-linux", "linux"),) * 2},
    {"state": "unsupported"}, {"state": "unconfirmed"}, {"state": "unavailable"},
])
def test_invalid_typed_observation_is_atomic(q, change):
    before = q.pool_catalog_snapshot()
    with pytest.raises(TaskError):
        _observe(q, replace(_cohort(("host-linux", "linux")), **change))
    assert q.pool_catalog_snapshot() == before


@pytest.mark.parametrize("failed", [False, True])
def test_configured_revision_regression_and_reuse_refuse_after_unavailability(q, failed):
    _observe(q, _cohort(("host-linux", "linux"), revision=4))
    expected = 1
    if failed:
        _observe(q, ConfiguredSupervisorCohort("unavailable", None, None, (), "read failed"), 1)
        expected = 2
    before = q.pool_catalog_snapshot()
    with pytest.raises(TaskError, match="cannot regress"):
        _observe(q, _cohort(revision=3), expected)
    with pytest.raises(TaskError, match="reused for changed members"):
        _observe(q, _cohort(revision=4), expected)
    assert q.pool_catalog_snapshot() == before


def test_cohort_failure_rolls_back_projection_and_history(q):
    before = q.pool_catalog_snapshot()
    with q._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_cohort BEFORE UPDATE ON pool_catalog_clock "
            "BEGIN SELECT RAISE(ABORT, 'synthetic cohort failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic cohort failure"):
        _observe(q, _cohort())
    assert q.pool_catalog_snapshot() == before
    with q._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 0


def test_concurrent_cohort_observations_have_one_cas_winner(q):
    barrier = Barrier(2)

    def observe(machine):
        barrier.wait(timeout=5)
        try:
            return _observe(q, _cohort((machine, "linux")))
        except TaskError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(observe, ("host-a", "host-b")))
    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1 and q.pool_catalog_snapshot().configured_cohort == winners[0]


def test_reopen_preserves_cohort_and_original_response_recovery(q):
    _observe(q, _cohort(("host-linux", "linux")))
    published = _publish(q, _begin(q))
    restored = TaskQueue(q.db_path)
    assert restored.pool_catalog_snapshot(now=100.0) == q.pool_catalog_snapshot()
    assert _begin(restored) == published
    with restored._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 1


def test_response_reader_retains_optional_fences_and_accepts_legacy_omission(q):
    _observe(q, _cohort(("host-linux", "linux")))
    scope = _publish(q, _begin(q))
    raw = _scope_payload(scope)
    assert read_pool_scope(raw) == scope
    old = {key: value for key, value in raw.items() if not key.endswith("_cohort_revision")}
    assert read_pool_scope(old) == replace(
        scope, enrollment_cohort_revision=None, definitions_cohort_revision=None,
    )
    for field in ("enrollment_cohort_revision", "definitions_cohort_revision"):
        for invalid in (True, -1, 0, "1"):
            with pytest.raises(PoolPublicationError, match="cohort attribution"):
                read_pool_scope({**raw, field: invalid})


@pytest.mark.parametrize("expected,now", [
    (True, 100.0), (-1, 100.0), ("0", 100.0), (0, float("nan")), (0, float("inf")),
])
def test_invalid_observation_fence_and_clock_have_no_effects(q, expected, now):
    before = q.pool_catalog_snapshot()
    with pytest.raises(TaskError):
        q.observe_pool_configured_cohort(_cohort(), expected_revision=expected, now=now)
    assert q.pool_catalog_snapshot() == before


def test_prior_schema_upgrade_retains_unattributed_scope_and_registration_clock(tmp_path):
    path = tmp_path / "prior.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE pool_catalog_clock (singleton INTEGER PRIMARY KEY,"
            " revision INTEGER NOT NULL, registration_revision INTEGER NOT NULL);"
            "INSERT INTO pool_catalog_clock VALUES (1, 7, 3);"
            "CREATE TABLE pool_publication_scopes (machine TEXT NOT NULL,"
            " env TEXT NOT NULL, publisher_id TEXT NOT NULL, generation INTEGER NOT NULL,"
            " revision INTEGER NOT NULL, state TEXT NOT NULL, input_revision TEXT,"
            " registration_revision INTEGER, valid_until REAL, definitions TEXT NOT NULL,"
            " reason TEXT, definitions_generation INTEGER, definitions_revision INTEGER,"
            " request TEXT, PRIMARY KEY(machine, env));"
            "INSERT INTO pool_publication_scopes VALUES"
            " ('host-linux','linux','retained',2,1,'complete','old-inputs',3,130,'[]',"
            " NULL,2,1,'original-request');"
        )
    queue = TaskQueue(path)
    snapshot = queue.pool_catalog_snapshot(now=100.0)
    assert snapshot.revision == 7 and snapshot.registration_revision == 3
    assert snapshot.ready and not snapshot.configured_ready
    assert snapshot.configured_cohort.revision == 0
    retained = snapshot.scopes[0]
    assert retained.publisher_id == "retained" and retained.generation == 2
    assert retained.definitions_cohort_revision is None
    assert retained.enrollment_cohort_revision is None
    with queue._connect() as conn:
        assert conn.execute("SELECT request FROM pool_publication_scopes").fetchone()[0] == "original-request"
    assert TaskQueue(path).pool_catalog_snapshot(now=100.0) == snapshot
