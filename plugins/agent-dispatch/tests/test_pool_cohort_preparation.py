"""Contained original configured-owner custody; no installed publisher."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Barrier

import pytest

from agent_dispatch.pool_cohort_preparation import PoolCohortPreparation
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.queue import TaskQueue
from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort
from agent_dispatch.registrar_discovery import RegistrarSources


def _write(base: Path, *, revision: int = 0, members: list | None = None) -> Path:
    path = base / "pointers.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "pointers": [],
                "configured_supervisors": {
                    "revision": revision,
                    "members": members
                    if members is not None
                    else [
                        {"machine": "logical", "execution_machine": "host-linux", "env": "linux"},
                        {
                            "machine": "logical",
                            "execution_machine": "host-windows",
                            "env": "windows",
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def owner(tmp_path):
    base = tmp_path / "registrar"
    base.mkdir()
    sources = RegistrarSources(base=base)
    queue = TaskQueue(tmp_path / "queue.db")
    return base, sources, queue, PoolCohortPreparation(sources, queue)


def _project(queue, request):
    return queue.observe_pool_configured_cohort(
        request.observation,
        expected_revision=request.expected_revision,
        now=100.0,
    )


def test_fresh_whole_owner_request_is_immutable_and_does_not_project(owner, monkeypatch):
    base, sources, queue, custody = owner
    path = _write(base)
    original = path.read_bytes()
    before = queue.pool_catalog_snapshot(now=100.0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preparation must not discover, enroll or project")

    monkeypatch.setattr(sources, "refresh", forbidden)
    monkeypatch.setattr(queue, "observe_pool_configured_cohort", forbidden)
    monkeypatch.setattr(queue, "begin_pool_publication", forbidden)
    request = custody.prepare()
    assert request is custody.pending
    assert request.previous == before.configured_cohort and request.expected_revision == 0
    assert request.source_path == path.absolute()
    assert request.coordinator_path == Path(queue.db_path).absolute()
    assert request.observation.source_revision == hashlib.sha256(original).hexdigest()
    assert [member.execution_machine for member in request.observation.members] == [
        "host-linux",
        "host-windows",
    ]
    assert {member.machine for member in request.observation.members} == {"logical"}
    with pytest.raises(FrozenInstanceError):
        request.expected_revision = 1
    wire = request.to_dict()
    wire["observation"]["members"].clear()
    assert len(request.observation.members) == 2
    assert queue.pool_catalog_snapshot(now=100.0) == before
    assert path.read_bytes() == original


@pytest.mark.parametrize("state", ["missing", "legacy", "empty", "invalid-cohort"])
def test_distinct_comparable_owner_states_are_retained_whole(owner, state):
    base, _sources, queue, custody = owner
    if state == "legacy":
        (base / "pointers.json").write_text("[]", encoding="utf-8")
    elif state == "empty":
        _write(base, members=[])
    elif state == "invalid-cohort":
        _write(base, members=[{"machine": "missing-execution"}])
    request = custody.prepare()
    observation = request.observation
    assert observation.members == ()
    assert observation.state == (
        "complete"
        if state == "empty"
        else "unavailable"
        if state == "invalid-cohort"
        else "unconfirmed"
    )
    assert (observation.source_revision is None) == (state == "missing")
    result = _project(queue, request)
    custody.acknowledge(request, result)
    assert custody.settled == (request, result) and custody.pending is None
    assert queue.pool_catalog_snapshot(now=100.0).configured_ready == (state == "empty")


@pytest.mark.parametrize("failure", ["unreadable", "invalid-json", "duplicate-keys"])
def test_uncomparable_failures_never_manufacture_stable_source_evidence(
    owner, monkeypatch, failure
):
    base, _sources, queue, custody = owner
    path = _write(base)
    if failure == "unreadable":

        def denied(_path):
            raise PermissionError("synthetic source refusal")

        monkeypatch.setattr(Path, "read_bytes", denied)
    else:
        path.write_text(
            "{bad" if failure == "invalid-json" else '{"pointers":[],"pointers":[]}',
            encoding="utf-8",
        )
    before = queue.pool_configured_cohort()
    with pytest.raises(PoolPublicationError, match="no comparable source"):
        custody.prepare()
    assert custody.pending is None and custody.settled is None
    assert queue.pool_configured_cohort() == before


@pytest.mark.parametrize("change", ["bytes", "members", "revision", "absent"])
def test_changed_owner_source_refuses_before_retaining_request(owner, monkeypatch, change):
    base, sources, queue, custody = owner
    path = _write(base) if change != "absent" else base / "pointers.json"
    original = sources.configured_supervisor_cohort
    reads = 0

    def read():
        nonlocal reads
        observed = original()
        reads += 1
        if reads == 1:
            if change == "bytes":
                path.write_bytes(path.read_bytes() + b"\r\n")
            elif change == "members":
                _write(base, revision=1, members=[])
            else:
                _write(base, revision=1)
        return observed

    monkeypatch.setattr(sources, "configured_supervisor_cohort", read)
    with pytest.raises(PoolPublicationError, match="source changed"):
        custody.prepare()
    assert reads == 2 and custody.pending is None
    assert queue.pool_configured_cohort().revision == 0


def test_coordinator_change_during_owner_reads_refuses_no_rebase(owner, monkeypatch):
    base, sources, queue, custody = owner
    _write(base)
    read = sources.configured_supervisor_cohort
    observed = read()

    def mutate():
        queue.observe_pool_configured_cohort(observed, expected_revision=0, now=100.0)
        return read()

    monkeypatch.setattr(sources, "configured_supervisor_cohort", mutate)
    with pytest.raises(PoolPublicationError, match="coordinator cohort changed"):
        custody.prepare()
    assert custody.pending is None and queue.pool_configured_cohort().revision == 1


def test_unknown_result_retains_original_without_new_discovery_or_rebase(owner, monkeypatch):
    base, sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    wire = request.to_dict()
    assert custody.recover() is None
    _write(base, revision=1, members=[])

    def forbidden():
        raise AssertionError("pending request cannot read a newer source")

    monkeypatch.setattr(sources, "configured_supervisor_cohort", forbidden)
    with pytest.raises(PoolPublicationError, match="remains unresolved"):
        custody.prepare()
    assert custody.recover() is None
    assert custody.pending is request and request.to_dict() == wire
    assert queue.pool_configured_cohort().revision == 0


@pytest.mark.parametrize("change", ["complete", "unavailable", "unconfirmed"])
def test_accepted_original_recovers_after_newer_source_projection_and_reopen(
    owner, monkeypatch, change
):
    base, sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    _write(base, revision=1, members=[])
    newer = sources.configured_supervisor_cohort()
    if change != "complete":
        newer = ConfiguredSupervisorCohort(change, None, None, (), "source-changed")
    queue.observe_pool_configured_cohort(newer, expected_revision=1, now=150.0)
    reopened = TaskQueue(queue.db_path)
    before = reopened.pool_catalog_snapshot(now=200.0)

    def forbidden():
        raise AssertionError("recovery must not reread or republish the current source")

    monkeypatch.setattr(sources, "configured_supervisor_cohort", forbidden)
    assert (
        reopened.recover_pool_configured_cohort(
            request.observation,
            expected_revision=0,
        )
        == accepted
    )
    assert (
        reopened.observe_pool_configured_cohort(
            request.observation,
            expected_revision=0,
            now=999.0,
        )
        == accepted
    )
    assert custody.recover() == accepted
    assert custody.pending is None and custody.settled == (request, accepted)
    assert reopened.pool_catalog_snapshot(now=200.0) == before
    with reopened._connect() as conn:
        assert (
            conn.execute(
                "SELECT accepted_at FROM pool_catalog_cohort_history ORDER BY revision",
            ).fetchall()[0]["accepted_at"]
            == 100.0
        )
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM spawn_reservations").fetchone()[0] == 0


def test_satisfied_noop_recovers_original_revision_after_subsequent_change(owner):
    base, sources, queue, custody = owner
    _write(base)
    first = custody.prepare()
    accepted = _project(queue, first)
    custody.acknowledge(first, accepted)
    noop = custody.prepare()
    assert noop.expected_revision == 1
    assert _project(queue, noop) == accepted
    _write(base, revision=1, members=[])
    queue.observe_pool_configured_cohort(
        sources.configured_supervisor_cohort(),
        expected_revision=1,
        now=150.0,
    )
    assert custody.recover() == accepted
    assert custody.settled == (noop, accepted)
    assert queue.pool_configured_cohort().revision == 2


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 0},
        {"revision": 2},
        {"revision": True},
        {"observation": None},
    ],
)
def test_wrong_result_never_drops_original_custody(owner, change):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    with pytest.raises(PoolPublicationError, match="original observation and CAS"):
        custody.acknowledge(request, replace(accepted, **change))
    with pytest.raises(PoolPublicationError, match="original request"):
        custody.acknowledge(replace(request), accepted)
    assert custody.pending is request and custody.settled is None
    custody.acknowledge(request, accepted)
    with pytest.raises(PoolPublicationError, match="original request"):
        custody.acknowledge(request, accepted)
    assert custody.settled == (request, accepted)


def test_forged_subset_same_revision_cannot_acknowledge_or_recover(owner):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    subset = replace(request.observation, members=request.observation.members[:1])
    with pytest.raises(PoolPublicationError, match="original observation and CAS"):
        custody.acknowledge(request, replace(accepted, observation=subset))
    assert queue.recover_pool_configured_cohort(subset, expected_revision=0) is None
    assert queue.recover_pool_configured_cohort(request.observation, expected_revision=9) is None
    assert custody.pending is request


@pytest.mark.parametrize("revision, malformed", [(0, False), (1, True), (0, 0.0), (1, 1.0)])
def test_equal_but_malformed_configured_revision_cannot_settle_custody(
    owner, revision, malformed
):
    base, _sources, queue, custody = owner
    _write(base, revision=revision)
    first = custody.prepare()
    accepted = _project(queue, first)
    custody.acknowledge(first, accepted)
    request = custody.prepare()
    forged = replace(
        accepted,
        observation=replace(request.observation, revision=malformed),
    )
    assert forged == accepted
    with pytest.raises(PoolPublicationError, match="observation is malformed"):
        custody.acknowledge(request, forged)
    assert custody.pending is request and custody.settled == (first, accepted)
    assert custody.recover() == accepted
    assert custody.pending is None and custody.settled == (request, accepted)


@pytest.mark.parametrize("change", [{"revision": 0}, {"observation": None}])
def test_settled_projection_cannot_regress_or_reuse_coordinator_revision(
    owner, monkeypatch, change
):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    custody.acknowledge(request, accepted)
    monkeypatch.setattr(queue, "pool_configured_cohort", lambda: replace(accepted, **change))
    with pytest.raises(PoolPublicationError, match="regressed or reused"):
        custody.prepare()
    assert custody.pending is None and custody.settled == (request, accepted)


def test_concurrent_preparers_retain_one_original_request(owner):
    base, _sources, _queue, custody = owner
    _write(base)
    barrier = Barrier(2)

    def prepare():
        barrier.wait(timeout=5)
        try:
            return custody.prepare()
        except PoolPublicationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: prepare(), range(2)))
    winners = [result for result in results if result is not None]
    assert winners == [custody.pending] and len(winners) == 1


def test_preparation_requires_actual_owners(owner):
    _base, sources, queue, _custody = owner
    with pytest.raises(PoolPublicationError, match="registrar and queue owners"):
        PoolCohortPreparation(lambda: (), queue)
    with pytest.raises(PoolPublicationError, match="registrar and queue owners"):
        PoolCohortPreparation(sources, object())


@pytest.mark.parametrize("target", ["source", "coordinator"])
def test_same_bytes_or_revision_at_another_target_cannot_replace_owner(owner, monkeypatch, target):
    base, sources, queue, custody = owner
    _write(base)
    other = base.parent / "other-registrar"
    other.mkdir()
    _write(other)
    other_queue = TaskQueue(base.parent / "other-queue.db")
    read = sources.configured_supervisor_cohort

    def retarget():
        observed = read()
        if target == "source":
            sources.base = other
        else:
            queue.db_path = other_queue.db_path
        return observed

    monkeypatch.setattr(sources, "configured_supervisor_cohort", retarget)
    with pytest.raises(PoolPublicationError, match="target changed"):
        custody.prepare()
    assert custody.pending is None
    assert queue.pool_configured_cohort().revision == 0
    assert other_queue.pool_configured_cohort().revision == 0


def test_recovery_cannot_use_another_coordinator_with_identical_result(owner):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    other = TaskQueue(base.parent / "other-queue.db")
    assert _project(other, request) == accepted
    queue.db_path = other.db_path
    with pytest.raises(PoolPublicationError, match="original coordinator target"):
        custody.recover()
    assert custody.pending is request and custody.settled is None


def test_settled_source_target_cannot_change_even_with_identical_bytes(owner):
    base, sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    custody.acknowledge(request, accepted)
    other = base.parent / "other-registrar"
    other.mkdir()
    _write(other)
    sources.base = other
    with pytest.raises(PoolPublicationError, match="target changed after settlement"):
        custody.prepare()
    assert custody.pending is None and custody.settled == (request, accepted)


def test_acknowledgment_from_retargeted_coordinator_cannot_clear_unknown_original(owner):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    original_path = queue.db_path
    other = TaskQueue(base.parent / "other-queue.db")
    lookalike = _project(other, request)
    queue.db_path = other.db_path
    with pytest.raises(PoolPublicationError, match="acknowledgment lost the original coordinator"):
        custody.acknowledge(request, lookalike)
    assert custody.pending is request and custody.settled is None
    queue.db_path = original_path
    assert custody.recover() is None
    assert queue.pool_configured_cohort().revision == 0
    accepted = _project(queue, request)
    custody.acknowledge(request, accepted)
    assert custody.settled == (request, accepted)


def test_coordinator_retarget_during_recovery_read_preserves_original_custody(owner, monkeypatch):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    other = TaskQueue(base.parent / "other-queue.db")

    def read(*_args, **_kwargs):
        queue.db_path = other.db_path
        return accepted

    monkeypatch.setattr(queue, "recover_pool_configured_cohort", read)
    with pytest.raises(PoolPublicationError, match="acknowledgment lost the original coordinator"):
        custody.recover()
    assert custody.pending is request and custody.settled is None


def test_matching_foreign_result_cannot_settle_unchanged_original_coordinator(owner):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    other = TaskQueue(base.parent / "other-queue.db")
    lookalike = _project(other, request)
    assert Path(queue.db_path).absolute() == request.coordinator_path
    with pytest.raises(PoolPublicationError, match="not accepted by the original coordinator"):
        custody.acknowledge(request, lookalike)
    assert custody.pending is request and custody.settled is None
    assert custody.recover() is None
    accepted = _project(queue, request)
    custody.acknowledge(request, accepted)
    assert custody.settled == (request, accepted)


def test_coordinator_retarget_during_acknowledgment_verification_preserves_custody(
    owner, monkeypatch
):
    base, _sources, queue, custody = owner
    _write(base)
    request = custody.prepare()
    accepted = _project(queue, request)
    other = TaskQueue(base.parent / "other-queue.db")
    original = queue.recover_pool_configured_cohort

    def read(*args, **kwargs):
        result = original(*args, **kwargs)
        queue.db_path = other.db_path
        return result

    monkeypatch.setattr(queue, "recover_pool_configured_cohort", read)
    with pytest.raises(PoolPublicationError, match="acknowledgment lost the original coordinator"):
        custody.acknowledge(request, accepted)
    assert custody.pending is request and custody.settled is None
