"""Retained worker composition uses real queue, pending and native application owners."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from typing import Any, cast

import pytest

import test_point_component_application as native
import test_point_prepared_application as prepared

from agent_index.engine.client import EngineClient
from agent_index.indexing.component_application import PreparedComponentApplication
from agent_index.indexing.component_read import read_component
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_hints import WorkItemRevisionTarget
from agent_index.indexing.point_worker import run_retained_point_worker
from agent_index.indexing.source_ownership import SourcePipelineBusy, source_writer
from agent_index.indexing.task_store import TaskRecord, TaskStatus
from agent_index.indexing.worker import run_worker
from agent_index.store.component_ledger import ComponentLedger
from agent_index.store.source_layout import SourceLayoutError

point_preparation = native.point_preparation
application = native.application
SCOPE, FAMILY = native.SCOPE, native.FAMILY


def run(f: prepared.Fixture, **kwargs: Any) -> TaskRecord:
    ledger = kwargs.pop("ledger", None)
    options: dict[str, Any] = {
        "claim": f.points.claim, "scope": SCOPE, "paths": f.paths, "tasks": f.tasks,
        "ledger": ComponentLedger(f.config, f.store) if ledger is None else ledger, "max_rows": 8,
        "clients": {model: cast(EngineClient, prepared.Client(f, model))
                    for model in f.config.model_profiles},
    }
    return run_retained_point_worker(**(options | kwargs))


@pytest.mark.parametrize("point_preparation", [False, True], indirect=True)
def test_retained_worker_applies_completes_reads_and_retries_without_polling(
    application: prepared.Fixture,
) -> None:
    f = application
    claim = f.points.claim
    trailing = f.tasks.enqueue("selected", change_hints=replace(
        claim.hints, targets=(WorkItemRevisionTarget("one", 4, ("trailing",)),),
    ))
    poll = f.config.state_file.read_bytes()
    result = run(f)
    assert result.status == TaskStatus.COMPLETE and f.calls
    assert result.worker_pid is None and result.change_hints == claim.hints
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) is None
    assert f.tasks.get_task(trailing.id) == trailing
    native.reopen(f)
    assert run(f, clients={}) == result
    assert not f.calls and f.config.state_file.read_bytes() == poll
    ledger = ComponentLedger(f.config, f.store)
    for path in ("opaque-body", "opaque-comment"):
        component = read_component(
            ledger, f.paths, path, point_scope=claim.hints.scope,
            application_scope=SCOPE, max_rows=8,
        )
        assert component.coverage == "named-target-only"
        assert component.applied_version == (2 if path == "opaque-body" else 3)
        assert bool(component.chunks) != component.deleted
    f.paths.mark_indexed(FAMILY, "opaque-body", content_hash="stale", chunk_count=7)
    with pytest.raises(SourceLayoutError):
        run(f, clients={})
    assert not f.calls


def test_interrupted_preparation_resumes_the_same_retained_claim_and_cache(
    application: prepared.Fixture,
) -> None:
    f = application
    parent, poll = f.points.journal.payload, f.config.state_file.read_bytes()
    domain = f.store._source_domain
    assert domain is not None
    receipt = domain.receipt()

    def interrupt(model: str) -> None:
        if model == "second":
            raise RuntimeError("fixture interrupted second model")

    f.hook = interrupt
    with pytest.raises(RuntimeError, match="interrupted second model"):
        run(f)
    task = f.tasks.get_task(f.points.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    assert task.result_stats is None and task.worker_pid is None
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert domain.receipt() == receipt
    native.reopen(f)
    f.hook = None
    assert run(f).status == TaskStatus.COMPLETE
    assert [model for model, _texts in f.calls] == ["second"]
    assert f.config.state_file.read_bytes() == poll


@pytest.mark.parametrize("boundary", ["before-completion", "before-retirement"])
def test_lost_return_after_application_reopens_without_native_replay(
    application: prepared.Fixture, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None

    def interrupt(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("fixture lost worker result")

    with monkeypatch.context() as patch:
        if boundary == "before-completion":
            patch.setattr("agent_index.indexing.point_worker.complete_point_claim", interrupt)
        else:
            patch.setattr(PendingCrawls, "_retire_point_claim", staticmethod(interrupt))
        with pytest.raises(RuntimeError, match="lost worker result"):
            run(f)
    task = f.tasks.get_task(f.points.claim.task_id)
    assert task is not None
    assert task.status == (
        TaskStatus.QUEUED_HINTS_V2 if boundary == "before-completion" else TaskStatus.COMPLETE
    )
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == f.points.journal.payload
    receipt, poll = domain.receipt(), f.config.state_file.read_bytes()
    native.reopen(f)
    assert run(f).status == TaskStatus.COMPLETE
    assert not f.calls and domain.receipt() == receipt
    assert f.config.state_file.read_bytes() == poll


def test_noncurrent_queue_during_embedding_cannot_publish_or_acknowledge(
    application: prepared.Fixture,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None
    before = domain.receipt(), f.config.state_file.read_bytes()
    def cancel(_model: str) -> None:
        assert not f.tasks.cancel(f.points.claim.task_id)
        f.tasks.update_status(f.points.claim.task_id, TaskStatus.CANCELLED)

    f.hook = cancel
    with pytest.raises(ValueError, match="no longer eligible"):
        run(f)
    task = f.tasks.get_task(f.points.claim.task_id)
    assert task is not None and task.status == TaskStatus.CANCELLED
    assert task.result_stats is None
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == f.points.journal.payload
    assert f.paths.get_all_entries() == {}
    assert (domain.receipt(), f.config.state_file.read_bytes()) == before


@pytest.mark.parametrize("invalid", ["incomplete", "claim", "path-owner", "clients"])
def test_invalid_retained_inputs_refuse_before_native_application(
    application: prepared.Fixture, invalid: str,
) -> None:
    f = application
    options: dict[str, Any] = {}
    if invalid == "incomplete":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM prepared_crawl_rows WHERE kind = 'named-point-acquisition/v1'")
    elif invalid == "claim":
        options["claim"] = replace(f.points.claim, point_claim_id="b" * 32)
    elif invalid == "path-owner":
        options["paths"] = PathIndex(f.config.data_dir / "foreign.db")
    else:
        options["clients"] = {}
    domain = f.store._source_domain
    assert domain is not None
    before = f.cache(), domain.receipt(), f.tasks.get_task(f.points.claim.task_id)
    with pytest.raises(ValueError):
        run(f, **options)
    assert (f.cache(), domain.receipt(), f.tasks.get_task(f.points.claim.task_id)) == before
    assert not f.calls


def test_uncertain_native_failure_is_retained_and_not_automatically_reconciled(
    application: prepared.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    original = PreparedComponentApplication._publish_point
    ledger = ComponentLedger(f.config, f.store)

    def interrupt(self: PreparedComponentApplication, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        raise RuntimeError("fixture interrupted native effect")

    monkeypatch.setattr(PreparedComponentApplication, "_publish_point", interrupt)
    with pytest.raises(RuntimeError, match="interrupted native effect"):
        run(f, ledger=ledger)
    f.calls.clear()
    with pytest.raises(SourceLayoutError):
        run(f, ledger=ledger)
    task = f.tasks.get_task(f.points.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    assert task.result_stats is None and not f.calls
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == f.points.journal.payload


def test_existing_writer_and_legacy_worker_cannot_claim_this_work(
    application: prepared.Fixture,
) -> None:
    f = application
    before = f.cache(), f.tasks.get_task(f.points.claim.task_id)
    with source_writer(f.config.data_dir), ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(SourcePipelineBusy):
            pool.submit(run, f).result(timeout=10)
    assert run_worker(
        f.points.claim.task_id, config=f.config,
        indexer=lambda **kwargs: pytest.fail("Legacy worker must not execute a parked v2 claim"),
    ) == 2
    assert (f.cache(), f.tasks.get_task(f.points.claim.task_id)) == before
    assert not f.calls and f.tasks.dequeue_next(accept_change_hints=True) is None
