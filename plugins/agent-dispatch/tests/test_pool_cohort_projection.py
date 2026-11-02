"""Explicit original-request projection with isolated registrar/queue owners."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_dispatch.pool_cohort_preparation import PoolCohortPreparation
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from agent_dispatch.registrar_discovery import RegistrarSources, save_pointers
from agent_dispatch.single_instance import SingleInstance


def _write(base: Path, *, revision: int = 0) -> None:
    (base / "pointers.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pointers": [],
                "configured_supervisors": {"revision": revision, "members": []},
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def owner(tmp_path):
    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    sources = RegistrarSources(base=base)
    queue = TaskQueue(tmp_path / "queue.db")
    return base, sources, queue, PoolCohortPreparation(sources, queue)


def test_explicit_projection_uses_original_request_and_existing_writer_lock(owner, monkeypatch):
    base, _sources, queue, custody = owner
    request = custody.prepare()
    observe = queue.observe_pool_configured_cohort
    calls = []

    def project(observation, *, expected_revision):
        contender = SingleInstance(base / "pointers.lock")
        try:
            assert not contender.acquire()
        finally:
            contender.release()
        calls.append((observation, expected_revision))
        return observe(observation, expected_revision=expected_revision, now=100.0)

    monkeypatch.setattr(queue, "observe_pool_configured_cohort", project)
    result = custody.project(request)
    assert calls == [(request.observation, request.expected_revision)]
    assert custody.pending is None and custody.settled == (request, result)
    assert queue.pool_configured_cohort() == result
    save_pointers([], base)
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM spawn_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM pool_publication_scopes").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["source", "source-target", "queue-target", "unreadable"])
def test_changed_original_refuses_before_projection_and_keeps_pending(owner, monkeypatch, change):
    base, sources, queue, custody = owner
    request = custody.prepare()
    if change == "source":
        _write(base, revision=1)
    elif change == "source-target":
        other = base.parent / "other"
        other.mkdir()
        _write(other)
        sources.base = other
    elif change == "queue-target":
        queue.db_path = TaskQueue(base.parent / "other.db").db_path
    else:
        (base / "pointers.json").write_text("{bad", encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("changed source or target must not project")

    monkeypatch.setattr(queue, "observe_pool_configured_cohort", forbidden)
    with pytest.raises(PoolPublicationError):
        custody.project(request)
    assert custody.pending is request and custody.settled is None
    assert queue.pool_configured_cohort().revision == 0


def test_original_coordinator_cas_refuses_changed_projection_without_rebase(owner):
    base, sources, queue, custody = owner
    request = custody.prepare()
    _write(base, revision=1)
    queue.observe_pool_configured_cohort(
        sources.configured_supervisor_cohort(), expected_revision=0, now=100.0
    )
    _write(base)
    with pytest.raises(TaskError, match="revision changed"):
        custody.project(request)
    assert custody.pending is request and custody.settled is None
    assert queue.pool_configured_cohort().revision == 1


@pytest.mark.parametrize("committed", [False, True])
def test_unknown_projection_outcome_retains_original_and_recovers_without_rewrite(
    owner, monkeypatch, committed
):
    base, sources, queue, custody = owner
    request = custody.prepare()
    observe = queue.observe_pool_configured_cohort
    calls = 0

    def interrupted(observation, *, expected_revision):
        nonlocal calls
        calls += 1
        if committed:
            observe(observation, expected_revision=expected_revision, now=100.0)
        raise OSError("synthetic lost projection result")

    monkeypatch.setattr(queue, "observe_pool_configured_cohort", interrupted)
    with pytest.raises(OSError, match="lost projection"):
        custody.project(request)
    assert custody.pending is request and custody.settled is None
    _write(base, revision=1)
    if committed:

        def forbidden():
            raise AssertionError("original accepted recovery must not read a newer source")

        monkeypatch.setattr(sources, "configured_supervisor_cohort", forbidden)
        result = custody.project(request)
        assert custody.settled == (request, result) and custody.pending is None
        assert calls == 1
        with queue._connect() as conn:
            row = conn.execute("SELECT * FROM pool_catalog_cohort_history").fetchone()
            assert row["accepted_at"] == 100.0
    else:
        assert custody.recover() is None
        with pytest.raises(PoolPublicationError, match="source changed"):
            custody.project(request)
        assert calls == 1 and custody.pending is request


@pytest.mark.parametrize("change", ["source", "source-target", "queue-target"])
def test_change_during_write_never_manufactures_current_source_settlement(
    owner, monkeypatch, change
):
    base, sources, queue, custody = owner
    request = custody.prepare()
    observe = queue.observe_pool_configured_cohort
    original_queue_path = queue.db_path

    def mutate(observation, *, expected_revision):
        result = observe(observation, expected_revision=expected_revision, now=100.0)
        if change == "source":
            _write(base, revision=1)
        elif change == "source-target":
            sources.base = base.parent / "other"
        else:
            queue.db_path = TaskQueue(base.parent / "other.db").db_path
        return result

    monkeypatch.setattr(queue, "observe_pool_configured_cohort", mutate)
    with pytest.raises(PoolPublicationError):
        custody.project(request)
    assert custody.pending is request and custody.settled is None
    queue.db_path = original_queue_path
    result = custody.recover()
    assert custody.settled == (request, result) and custody.pending is None
    assert result.revision == 1


def test_wrong_request_or_completed_request_cannot_project(owner):
    _base, _sources, _queue, custody = owner
    request = custody.prepare()
    with pytest.raises(PoolPublicationError, match="original pending"):
        custody.project(replace(request))
    result = custody.project(request)
    with pytest.raises(PoolPublicationError, match="original pending"):
        custody.project(request)
    assert custody.settled == (request, result)


def test_project_recovers_satisfied_noop_after_source_loss_without_new_write(owner, monkeypatch):
    base, sources, queue, custody = owner
    first = custody.prepare()
    accepted = custody.project(first)
    noop = custody.prepare()
    _write(base, revision=1)
    queue.observe_pool_configured_cohort(
        sources.configured_supervisor_cohort(), expected_revision=1, now=150.0
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("accepted no-op requires no source read or projection")

    monkeypatch.setattr(sources, "configured_supervisor_cohort", forbidden)
    monkeypatch.setattr(queue, "observe_pool_configured_cohort", forbidden)
    assert custody.project(noop) == accepted
    assert custody.settled == (noop, accepted)
    assert queue.pool_configured_cohort().revision == 2


def test_lock_failure_preserves_original_and_never_projects(owner, monkeypatch):
    _base, _sources, queue, custody = owner
    request = custody.prepare()

    def busy(_self):
        return False

    monkeypatch.setattr(SingleInstance, "acquire", busy)
    ticks = iter([0.0, 10.0])
    monkeypatch.setattr(
        "agent_dispatch.registrar_discovery.time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    with pytest.raises(PoolPublicationError, match="source lock failed"):
        custody.project(request)
    assert custody.pending is request and custody.settled is None
    assert queue.pool_configured_cohort().revision == 0
