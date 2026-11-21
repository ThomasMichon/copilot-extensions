"""Reconstructed services require explicit, owner-qualified native recovery."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
from typing import Any

import httpx
import pytest

import test_azure_devops_point_runtime as runtime
import test_azure_devops_point_worker as worker

from agent_index.indexing.component_application import PreparedComponentApplication
from agent_index.indexing.component_publication import reconcile_component_publication
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_hints import CommentVersionTarget, WorkItemRevisionTarget
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PreparedPoints
from agent_index.indexing.source_ownership import source_writer
from agent_index.indexing.task_store import TaskStatus
from agent_index.store.source_layout import SourceLayoutError

application = runtime.application
subject = runtime.subject
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("service"),
]


def _reopen_connector(f: worker.Fixture) -> None:
    previous = f.connector._http.client
    assert previous.is_closed
    f.connector._http.client = httpx.Client(
        base_url=previous.base_url, headers=previous.headers,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        transport=httpx.MockTransport(f.provider.handle),
    )


def _native_versions(f: worker.Fixture) -> dict[str, int]:
    domain = f.store._source_domain
    assert domain is not None
    return {
        name: f.store.content_store.db.open_table(name).version
        for name in domain.receipt()["versions"] if name != "component_ordering_evidence"
    }


def _reconcile(f: worker.Fixture) -> tuple[int, int]:
    with source_writer(f.config.data_dir):
        with closing(f.tasks._connect()) as conn:
            assert f.tasks._require_point_claim(conn, f.claim).status == TaskStatus.QUEUED_HINTS_V2
        payload = PendingCrawls(f.paths).read_payload(f.claim.hints.source, worker.SCOPE)
        assert payload is not None
        manifest = PreparedPoints(PreparedJournal(
            f.paths, f.claim.hints.source, worker.SCOPE, payload,
            point_claim=f.claim, require_existing=True,
        )).compose()
        return reconcile_component_publication(
            manifest, scope=worker.SCOPE, family=f.claim.hints.canonical_family,
            max_rows=32, config=f.config, paths=f.paths, store=f.store, point_claim=f.claim,
        )


@pytest.mark.parametrize("stage", ["sealed-checkpoint", "unsealed-native"])
def test_reconstructed_service_does_not_infer_native_recovery(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    f = application
    poll = f.config.state_file.read_bytes()

    def interrupt(*_: Any, **__: Any) -> None:
        raise RuntimeError("fixture interrupted native publication")

    async def exercise() -> None:
        with monkeypatch.context() as fault:
            if stage == "sealed-checkpoint":
                fault.setattr(PathIndex, "mark_indexed_batch", interrupt)
            else:
                publish = PreparedComponentApplication._publish_point

                def partial(self: PreparedComponentApplication, *args: Any, **kwargs: Any) -> None:
                    publish(self, *args, **kwargs)
                    interrupt()

                fault.setattr(PreparedComponentApplication, "_publish_point", partial)
            app, clients = runtime._point_service_app(f, monkeypatch)
            async with app.router.lifespan_context(app):
                runner = app.state.task_runner
                await runner.point_service.activate()
                runner.notify()
                await runtime._wait_point_state(lambda: runner.point_service.status()["failed"])
            assert clients == ["open", "closed"] and f.connector._http.client.is_closed

        domain = f.store._source_domain
        assert domain is not None
        receipt = domain.receipt()
        assert receipt["state"] == "dirty"
        assert bool(receipt.get("reconciliation")) == (stage == "sealed-checkpoint")
        failed = f.tasks.get_task(f.claim.task_id)
        assert failed is not None and failed.status == TaskStatus.QUEUED_HINTS_V2
        assert failed.point_claim_id == f.claim.point_claim_id and f.retained_count() == 2
        assert f.provider.body_reads == 2 and f.calls
        versions = _native_versions(f)

        await asyncio.to_thread(f.reopen)
        _reopen_connector(f)
        app, clients = runtime._point_service_app(f, monkeypatch)
        async with app.router.lifespan_context(app):
            runner = app.state.task_runner
            assert runner.point_service.status()["paused"]
            await asyncio.sleep(2.1)
            assert f.tasks.get_task(f.claim.task_id) == failed
            assert not f.provider.requests and not f.calls
            assert domain.receipt() == receipt and _native_versions(f) == versions
            await runner.point_service.activate()
            runner.notify()
            await runtime._wait_point_state(lambda: runner.point_service.status()["failed"])
            assert not f.provider.requests and not f.calls
        assert clients == ["open", "closed"] and f.connector._http.client.is_closed
        failed = f.tasks.get_task(f.claim.task_id)
        assert failed is not None and failed.status == TaskStatus.QUEUED_HINTS_V2

        if stage == "unsealed-native":
            with pytest.raises(SourceLayoutError):
                await asyncio.to_thread(_reconcile, f)
            assert domain.receipt() == receipt and _native_versions(f) == versions
            assert f.tasks.get_task(f.claim.task_id) == failed
            assert not f.provider.requests and not f.calls
            assert f.config.state_file.read_bytes() == poll
            return

        assert await asyncio.to_thread(_reconcile, f) == (2, 0)
        assert domain.receipt()["state"] == "clean" and _native_versions(f) == versions
        assert f.tasks.get_task(f.claim.task_id) == failed
        assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, worker.SCOPE) is not None
        assert not f.provider.requests and not f.calls

        _reopen_connector(f)
        app, clients = runtime._point_service_app(f, monkeypatch)
        async with app.router.lifespan_context(app):
            runner = app.state.task_runner
            assert runner.point_service.status()["paused"]
            assert f.tasks.get_task(f.claim.task_id) == failed
            await runner.point_service.activate()
            runner.notify()
            await runtime._wait_point_state(lambda: bool(
                (task := f.tasks.get_task(f.claim.task_id)) and task.status == TaskStatus.COMPLETE
            ))
            assert f.provider.body_reads == 0 and not f.calls
            assert _native_versions(f) == versions
        assert clients == ["open", "closed"] and f.connector._http.client.is_closed
        assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, worker.SCOPE) is None
        assert f.config.state_file.read_bytes() == poll

    asyncio.run(exercise())


@pytest.mark.parametrize("deleted", [False, True])
def test_http_superseded_claim_completes_and_retries_without_body_or_native_work(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, deleted: bool,
) -> None:
    f = application
    if deleted:
        f.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    original = f.run()
    f.reopen()
    before = worker._projection(f)
    hints = replace(f.claim.hints, targets=(
        WorkItemRevisionTarget("7", 1, ("older-first",)),
        WorkItemRevisionTarget("7", 2, ("older-second",)),
        CommentVersionTarget("7", "5", 1, ("older-comment",)),
    ))
    model_events: list[tuple[str, str]] = []
    app, clients = runtime._point_service_app(f, monkeypatch, model_events=model_events)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            runner = app.state.task_runner
            assert runner.point_service.status()["paused"] and clients == ["open"]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                response = await client.post("/reindex/hints", json={
                    "source": hints.source, "change_hints": hints.to_dict(),
                })
                assert response.status_code == 200 and response.json()["accepted"]
                task_id = response.json()["task"]["id"]
                assert runner.store.get_task(task_id) is None
                await asyncio.sleep(2.1)
                parked = await asyncio.to_thread(f.tasks.get_task, task_id)
                assert parked is not None and parked.status == TaskStatus.QUEUED_HINTS_V2
                assert not f.provider.requests and not f.calls
                await runner.point_service.activate()
                runner.notify()
                await runtime._wait_point_state(lambda: bool(
                    (task := f.tasks.get_task(task_id)) and task.status == TaskStatus.COMPLETE
                ))
                completed = await asyncio.to_thread(f.tasks.get_task, task_id)
                assert completed is not None and completed.change_hints == hints
                assert completed.result_stats is not None
                outcome = completed.result_stats["point_completion"]
                assert outcome["version"] == 2 and outcome["mode"] == "higher-covered"
                assert f.provider.body_reads == 0 and not f.calls and len(f.provider.requests) == 2
                assert await asyncio.to_thread(worker._projection, f) == before
                assert await asyncio.to_thread(f.retained_count) == 0
                assert await asyncio.to_thread(f.tasks.get_task, original.id) == original
                assert 2 not in (await client.get("/health")).json()[
                    "indexing_contract"
                ]["execution_versions"]
        assert clients == ["open", "closed"] and f.connector._http.client.is_closed
        assert sorted(model_events) == [("close", "first"), ("close", "second")]
        await asyncio.to_thread(f.reopen)
        f.provider.selected = False
        retry_lifetime: list[str] = []
        retry_events: list[tuple[str, str]] = []
        async with runtime._owned_executor_context(f, retry_lifetime, retry_events) as execute:
            assert await asyncio.to_thread(execute, completed) == completed
            assert retry_events == []
        assert retry_lifetime == ["open", "closed"]
        assert sorted(retry_events) == [("close", "first"), ("close", "second")]
        assert await asyncio.to_thread(
            runtime.run, f, completed, expected_hints=hints, clients={},
        ) == completed
        assert not f.provider.requests and not f.calls
        assert await asyncio.to_thread(worker._projection, f) == before
        assert PendingCrawls(f.paths).read_payload(hints.source, worker.SCOPE) is None

    asyncio.run(exercise())
