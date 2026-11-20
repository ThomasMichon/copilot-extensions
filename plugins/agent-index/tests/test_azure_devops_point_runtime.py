"""Configured native owners and queue pins through the actual acquisition caller."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, closing, suppress
from contextvars import ContextVar
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

import test_azure_devops_point_worker as worker
import test_azure_devops_points as named
import test_change_hints_transport as hinted

from agent_index.engine.client import EngineClient
from agent_index.index_config import ModelProfile
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.point_hints import WorkItemRevisionTarget
from agent_index.indexing.point_queue_consumer import PointQueueConsumer
from agent_index.indexing.point_service import PointService
from agent_index.indexing.point_service_contract import point_scope_fingerprint, require_point_service
from agent_index.indexing.point_worker_host import PointWorkerHost
from agent_index.indexing.task_store import TaskRecord, TaskStatus, TaskStore
from agent_index.indexing.worker import run_worker
from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.sources.azure_devops_point_executor import (
    configured_azure_devops_point_executor, configured_azure_devops_point_runtime,
)
from agent_index.sources.azure_devops_point_runtime import run_configured_azure_devops_points
from agent_index.store.source_layout import SourceLayoutError

if TYPE_CHECKING:
    from fastapi import FastAPI

subject = worker.subject
application = worker.application


def run(f: worker.Fixture, task: TaskRecord, **kwargs: Any) -> TaskRecord:
    options: dict[str, Any] = {
        "expected_hints": f.claim.hints, "config": f.config,
        "legacy_data_dir": f.config.data_dir.parent / "legacy",
        "point_scope": f.claim.hints.scope, "scope": worker.SCOPE,
        "clients": {model: cast(EngineClient, worker.Client(f, model))
                    for model in f.config.model_profiles},
        "max_rows": 32,
    }
    return run_configured_azure_devops_points(f.connector, task.id, **(options | kwargs))


def _owned_executor_context(
    f: worker.Fixture, lifetime: list[str], events: list[tuple[str, str]],
    *, embedding_runtime: EmbeddingRuntime | None = None, paired: bool = False,
) -> AbstractAsyncContextManager[Any]:
    class Transport(httpx.MockTransport):
        def close(self) -> None:
            super().close()
            lifetime.append("closed")

    def create_connector() -> AzureDevOpsConnector:
        lifetime.append("open")
        f.connector = AzureDevOpsConnector(
            "ado:acme/widgets", token="fixture-token", min_interval_s=0,
            work_item_queries=[{"wiql": named._QUERY}], pull_request_queries=[],
            transport=Transport(f.provider.handle),
        )
        f.connector._http._max_retries = 0
        return f.connector

    class Client(worker.Client):
        def embed_texts(self, texts: list[str]) -> Any:
            assert lifetime == ["open"] and not f.connector._http.client.is_closed
            assert ("ensure", self.model) in events
            return super().embed_texts(texts)

        def close(self) -> None:
            events.append(("close", self.model))

    def ensure(profile: ModelProfile, _: EngineClient) -> bool:
        events.append(("ensure", profile.model_id))
        return profile.model_id == "first"

    runtime = embedding_runtime or EmbeddingRuntime(
        lambda profile: cast(EngineClient, Client(f, profile.model_id)),
        ensure, lambda profile: events.append(("stop", profile.model_id)),
    )
    context = (
        configured_azure_devops_point_runtime if paired else configured_azure_devops_point_executor
    )
    return context(
        config=f.config, legacy_data_dir=f.config.data_dir.parent / "legacy",
        point_scope=f.claim.hints.scope, scope=worker.SCOPE, max_rows=32,
        create_connector=create_connector, embedding_runtime=runtime,
    )


def _point_service_app(
    f: worker.Fixture, monkeypatch: pytest.MonkeyPatch, *, passive: bool = False,
    model_events: list[tuple[str, str]] | None = None,
) -> tuple[FastAPI, list[str]]:
    pytest.importorskip("fastapi")
    from agent_index import server
    from agent_index.indexing.runner import TaskRunner

    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(f.config.data_dir.parent / "legacy"))
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "")
    monkeypatch.setenv("AGENT_INDEX_NATIVE_INSTALLATION_ID", "")
    monkeypatch.setattr(server, "_missing_indexing_dependencies", lambda: None)
    monkeypatch.setattr(server, "_publish_active_evidence", lambda *_, **__: None)
    create_runner = server._create_task_runner

    def create() -> Any:
        store, runner = create_runner()
        runner.set_governance_recheck_fn(None)
        return store, runner

    def no_spawn(*_: Any) -> None:
        pytest.fail("point service must not launch a detached worker")

    monkeypatch.setattr(server, "_create_task_runner", create)
    monkeypatch.setattr(TaskRunner, "_spawn_worker", no_spawn)
    lifetime: list[str] = []
    events = model_events if model_events is not None else []

    return server.build_app(
        passive=passive,
        point_service_factory=lambda: PointService(
            config=f.config, point_scope=f.claim.hints.scope,
            runtime_context=lambda: _owned_executor_context(f, lifetime, events, paired=True),
        ),
    ), lifetime


async def _wait_point_state(predicate: Callable[[], bool]) -> None:
    deadline = asyncio.get_running_loop().time() + 8
    while not await asyncio.to_thread(predicate):
        assert asyncio.get_running_loop().time() < deadline, "point service did not settle"
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("phase", ["opening", "closing"])
@pytest.mark.parametrize("cancelled_owner", ["caller", "loop"])
def test_owned_executor_cancelled_open_and_close_join_resource_cleanup(
    application: worker.Fixture, phase: str, cancelled_owner: str,
) -> None:
    f = application
    entered, release = threading.Event(), threading.Event()
    closed: list[str] = []
    selection: ContextVar[str] = ContextVar("executor_selection", default="unselected")

    def block() -> None:
        entered.set()
        assert release.wait(timeout=8)

    class Transport(httpx.MockTransport):
        def close(self) -> None:
            assert selection.get() == "selected"
            if phase == "closing":
                block()
            super().close()
            closed.append("connector")

    def create_connector() -> AzureDevOpsConnector:
        assert selection.get() == "selected"
        connector = AzureDevOpsConnector(
            "ado:acme/widgets", token="fixture-token", min_interval_s=0,
            work_item_queries=[{"wiql": named._QUERY}], pull_request_queries=[],
            transport=Transport(f.provider.handle),
        )
        if phase == "opening":
            block()
        return connector

    class Client(worker.Client):
        def close(self) -> None:
            closed.append(self.model)

    def no_readiness(*_: Any) -> Any:
        pytest.fail("inert context must not ensure or stop engines")

    async def use() -> None:
        selection.set("selected")
        async with configured_azure_devops_point_executor(
            config=f.config, legacy_data_dir=f.config.data_dir.parent / "legacy",
            point_scope=f.claim.hints.scope, scope=worker.SCOPE, max_rows=32,
            create_connector=create_connector,
            embedding_runtime=EmbeddingRuntime(
                lambda profile: cast(EngineClient, Client(f, profile.model_id)),
                no_readiness, no_readiness,
            ),
        ):
            assert not f.provider.requests and not f.calls

    async def exercise() -> None:
        task = asyncio.create_task(use())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(2):
                if cancelled_owner == "loop":
                    for owned in asyncio.all_tasks():
                        if owned is not asyncio.current_task():
                            owned.cancel()
                else:
                    task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == ["second", "first", "connector"]
        assert not f.provider.requests and not f.calls

    asyncio.run(exercise())


def test_owned_executor_rechecks_expired_engines_for_a_second_missing_target(
    application: worker.Fixture,
) -> None:
    f = application
    available = {model: False for model in f.config.model_profiles}
    ensured: list[str] = []
    stopped: list[str] = []
    closed: list[str] = []
    lifetime: list[str] = []

    class Client(worker.Client):
        def embed_texts(self, texts: list[str]) -> Any:
            assert available[self.model], "expired engine was not prepared again"
            return super().embed_texts(texts)

        def close(self) -> None:
            closed.append(self.model)

    def ensure(profile: ModelProfile, _: EngineClient) -> bool:
        model = profile.model_id
        ensured.append(model)
        started = not available[model]
        available[model] = True
        return started

    async def exercise() -> None:
        async with _owned_executor_context(
            f, lifetime, [], embedding_runtime=EmbeddingRuntime(
                lambda profile: cast(EngineClient, Client(f, profile.model_id)),
                ensure, lambda profile: stopped.append(profile.model_id),
            ),
        ) as execute:
            for target in f.claim.hints.targets:
                task = f.tasks.enqueue(
                    f.claim.hints.source, change_hints=replace(f.claim.hints, targets=(target,)),
                )
                completed = await asyncio.to_thread(execute, task)
                assert isinstance(completed, TaskRecord) and completed.status == TaskStatus.COMPLETE
                assert all(available.values()) and not stopped and not closed
                for model in available:
                    available[model] = False
            assert ensured == ["first", "second", "first", "second"]
        assert sorted(stopped) == sorted(closed) == ["first", "second"]
        assert lifetime == ["open", "closed"] and f.provider.body_reads == 2

    asyncio.run(exercise())


def test_selected_service_refuses_legacy_overlap_before_opening_clients(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    f.config = replace(f.config, data_dir=f.config.data_dir.parent / "legacy")
    app, lifetime = _point_service_app(f, monkeypatch)

    async def exercise() -> None:
        with pytest.raises(ValueError, match="separate legacy data directory"):
            async with app.router.lifespan_context(app):
                pytest.fail("explicit point configuration must not fall back to legacy startup")
        assert lifetime == [] and not f.provider.requests

    asyncio.run(exercise())


def test_service_promotes_inert_binding_and_routes_only_exact_point_intake(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    events: list[tuple[str, str]] = []
    app, lifetime = _point_service_app(f, monkeypatch, passive=True, model_events=events)
    other_hints = replace(f.claim.hints, account_id="another-account")

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                assert lifetime == []
                assert (await client.get("/health")).json()["point_service_contract"]["reason"] == "passive"
                body = {"source": f.claim.hints.source, "change_hints": f.claim.hints.to_dict()}
                assert (await client.post("/reindex/hints", json=body)).status_code == 503
                assert (await client.post("/promote")).json()["promoted"]
                runner = app.state.task_runner
                assert lifetime == ["open"] and runner.point_service.status()["paused"]
                assert events == []
                health = (await client.get("/health")).json()
                assert 2 not in health["indexing_contract"]["execution_versions"]
                contract = health["point_service_contract"]
                assert contract["paired"] and contract["state"] == "paused"
                assert contract["scope_fingerprint"] == point_scope_fingerprint(f.claim.hints.scope)
                assert contract["read_admitted"] and not contract["execution_admitted"]
                require_point_service(health, f.claim.hints.scope, operation="read")
                with pytest.raises(RuntimeError, match="point-service v1"):
                    require_point_service(health, f.claim.hints.scope, operation="execute")
                accepted = (await client.post("/reindex/hints", json=body)).json()
                assert accepted["accepted"]
                task_id = accepted["task"]["id"]
                assert runner.store.get_task(task_id) is None
                assert f.tasks.get_task(task_id) is not None
                other = (await client.post("/reindex/hints", json={
                    "source": other_hints.source, "change_hints": other_hints.to_dict(),
                })).json()["task"]
                assert runner.store.get_task(other["id"]) is not None
                assert f.tasks.get_task(other["id"]) is None
                await asyncio.sleep(2.1)
                assert not f.provider.requests
                await runner.point_service.activate()
                require_point_service(
                    (await client.get("/health")).json(), f.claim.hints.scope, operation="execute",
                )
                runner.notify()
                await _wait_point_state(lambda: bool(
                    (task := f.tasks.get_task(task_id)) and task.status == TaskStatus.COMPLETE
                ))
                original = f.tasks.get_task(f.claim.task_id)
                assert original is not None and original.status == TaskStatus.COMPLETE
                unselected = runner.store.get_task(other["id"])
                assert unselected is not None and unselected.status == TaskStatus.QUEUED_HINTS_V2
                assert f.provider.body_reads == 4
                await _wait_point_state(lambda: not runner.point_service.running)
                before = list(f.provider.requests), list(events), list(f.calls)
                observed = await runner.point_service.read_component("workitems/7/body.md")
                assert observed.coverage == "named-target-only"
                assert observed.applied_version == 3 and not observed.deleted and observed.chunks
                assert (f.provider.requests, events, f.calls) == before
                runner.point_service.pause()
                closed = (await client.get("/health")).json()["point_service_contract"]
                assert closed["state"] == "draining"
                assert not closed["read_admitted"] and not closed["execution_admitted"]
                with pytest.raises(RuntimeError, match="not accepting"):
                    await runner.point_service.read_component("workitems/7/body.md")
                await runner.point_service.resume()
        assert lifetime == ["open", "closed"] and f.connector._http.client.is_closed
        assert events.count(("ensure", "first")) == events.count(("ensure", "second")) == 1
        assert [model for action, model in events if action == "stop"] == ["first"]
        assert sorted(model for action, model in events if action == "close") == ["first", "second"]

    asyncio.run(exercise())


def _read_state(f: worker.Fixture) -> dict[str, str]:
    return {
        str(path.relative_to(f.config.data_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in f.config.data_dir.rglob("*") if path.is_file()
    }


def test_required_point_http_admission_preserves_scope_identity_and_paused_retention(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    app, lifetime = _point_service_app(f, monkeypatch)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                from agent_index import server
                from agent_index.indexing.point_hints import PointChangeHints

                runner = app.state.task_runner
                points = runner.point_service
                health = (await client.get("/health")).json()
                assert health["point_service_contract"]["required_admission_versions"] == [1]
                headers = {
                    server.INSTALLATION_HEADER: health["installationId"],
                    server.INSTANCE_HEADER: health["instanceToken"],
                }
                body = {
                    "source": f.claim.hints.source, "change_hints": f.claim.hints.to_dict(),
                    "required_point_service_version": 1,
                }
                before = f.tasks.get_queue(), runner.store.get_queue()
                assert (await client.post("/reindex/hints", json=body, headers=headers)).status_code == 409
                assert (f.tasks.get_queue(), runner.store.get_queue()) == before
                assert not f.provider.requests and not f.calls
                await points.activate()
                for bad_headers in ({}, headers | {server.INSTANCE_HEADER: "previous"}):
                    assert (await client.post(
                        "/reindex/hints", json=body, headers=bad_headers,
                    )).status_code == 409
                wrong = replace(f.claim.hints, account_id="different")
                assert (await client.post("/reindex/hints", json={
                    **body, "change_hints": wrong.to_dict(),
                }, headers=headers)).status_code == 409
                accepted = (await client.post("/reindex/hints", json=body, headers=headers)).json()
                points.pause()
                assert accepted["accepted"] and accepted["installationId"] == health["installationId"]
                assert accepted["instanceToken"] == health["instanceToken"]
                assert accepted["point_service_admission"] == {
                    "id": "agent-index.point-service", "version": 1,
                    "scope_fingerprint": point_scope_fingerprint(f.claim.hints.scope),
                }
                retained = PointChangeHints.from_dict(accepted["task"]["change_hints"])
                assert retained.union(f.claim.hints) == retained
                assert runner.store.get_task(accepted["task"]["id"]) is None
                assert (await client.post("/reindex/hints", json=body, headers=headers)).status_code == 409
                unrequired = {key: value for key, value in body.items() if key != "required_point_service_version"}
                assert (await client.post("/reindex/hints", json=unrequired)).json()["accepted"]
                assert not points.contract()["execution_admitted"]
        assert lifetime == ["open", "closed"]
        reopened = TaskStore(f.config.data_dir / "tasks.db")
        assert reopened.get_queue()
        assert reopened.dequeue_next() is None

    asyncio.run(exercise())


def test_explicit_point_client_uses_actual_headers_and_preserves_uncertain_admission(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from agent_index import server
    from agent_index.indexing.point_hints import PointChangeHints

    f = application
    app, lifetime = _point_service_app(f, monkeypatch)
    with TestClient(app) as transport:
        points = app.state.task_runner.point_service
        assert transport.portal is not None
        transport.portal.call(points.activate)
        api, calls = hinted._wire_api(
            transport, monkeypatch, installation_id=app.state.installation_id,
            instance_token=app.state.instance_token,
        )
        receipt = api.reindex_points(f.claim.hints)
        assert receipt["accepted"] is True
        stored = PointChangeHints.from_dict(receipt["task"]["change_hints"])
        assert stored.union(f.claim.hints) == stored
        post = calls[-1]
        assert post.headers[server.INSTALLATION_HEADER] == app.state.installation_id == ""
        assert post.headers[server.INSTANCE_HEADER] == app.state.instance_token
        assert receipt["instanceToken"] == app.state.instance_token
        assert receipt["installationId"] == app.state.installation_id
        assert app.state.task_runner.store.get_task(receipt["task"]["id"]) is None

        health = api.health
        current_instance = app.state.instance_token

        def changed_peer() -> dict[str, Any]:
            observed = health()
            app.state.instance_token = "replacement"
            return observed

        monkeypatch.setattr(api, "health", changed_peer)
        calls.clear()
        with pytest.raises(RuntimeError, match="acceptance outcome unknown") as outcome:
            api.reindex_points(f.claim.hints)
        assert isinstance(outcome.value.__cause__, httpx.HTTPStatusError)
        assert outcome.value.__cause__.response.status_code == 409
        assert [(r.method, r.url.path) for r in calls] == [
            ("GET", "/health"), ("POST", "/reindex/hints"),
        ]
        app.state.instance_token = current_instance
        monkeypatch.setattr(api, "health", health)
        request = api._request

        def lost_return(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            result = request(method, path, **kwargs)
            if method == "POST":
                raise httpx.ReadError("lost admission receipt")
            return result

        monkeypatch.setattr(api, "_request", lost_return)
        calls.clear()
        with pytest.raises(RuntimeError, match="acceptance outcome unknown"):
            api.reindex_points(f.claim.hints)
        assert [(r.method, r.url.path) for r in calls] == [
            ("GET", "/health"), ("POST", "/reindex/hints"),
        ]
        transport.portal.call(points.pause)
    assert lifetime == ["open", "closed"]
    reopened = TaskStore(f.config.data_dir / "tasks.db")
    assert reopened.get_queue() and reopened.dequeue_next() is None


@pytest.mark.parametrize("closure", ["drain", "stop"])
@pytest.mark.parametrize("cancelled", ["none", "caller", "loop"])
def test_required_point_enqueue_is_joined_through_closure_and_cancellation(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, closure: str, cancelled: str,
) -> None:
    from agent_index import server

    f = application
    app, lifetime = _point_service_app(f, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = TaskStore.enqueue
    notifications: list[str] = []

    def enqueue(store: TaskStore, *args: Any, **kwargs: Any) -> TaskRecord:
        if store._db_path == f.tasks._db_path:
            entered.set()
            assert release.wait(timeout=8)
        return original(store, *args, **kwargs)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            points = app.state.task_runner.point_service
            await points.activate()
            monkeypatch.setattr(TaskStore, "enqueue", enqueue)
            monkeypatch.setattr(app.state.task_runner, "notify", lambda: notifications.append("notify"))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                health = (await client.get("/health")).json()
                posting = asyncio.create_task(client.post("/reindex/hints", json={
                    "source": f.claim.hints.source, "change_hints": f.claim.hints.to_dict(),
                    "required_point_service_version": 1,
                }, headers={
                    server.INSTALLATION_HEADER: health["installationId"],
                    server.INSTANCE_HEADER: health["instanceToken"],
                }))
                closing = None
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    if closure == "drain":
                        assert await points.drain(timeout=0) is False
                    else:
                        closing = asyncio.create_task(points.stop())
                        await asyncio.sleep(0.02)
                        assert not closing.done()
                    assert points._intakes and lifetime == ["open"] and notifications == []
                    if cancelled != "none":
                        for _ in range(2):
                            if cancelled == "loop":
                                for task in asyncio.all_tasks():
                                    if task is not asyncio.current_task():
                                        task.cancel()
                            else:
                                posting.cancel()
                            await asyncio.sleep(0.02)
                    assert not posting.done()
                    assert points._intakes and lifetime == ["open"] and notifications == []
                finally:
                    release.set()
                    if cancelled != "none":
                        with pytest.raises(asyncio.CancelledError):
                            await posting
                    else:
                        assert (await posting).json()["accepted"]
                    if closing is not None:
                        if cancelled == "loop":
                            with pytest.raises(asyncio.CancelledError):
                                await closing
                        else:
                            await closing
                assert notifications == ["notify"]
                assert not points._intakes
                if closure == "drain":
                    assert await points.drain(timeout=1)
                    assert lifetime == ["open"]
        assert lifetime == ["open", "closed"]
        assert f.tasks.get_queue() and not f.provider.requests and not f.calls

    asyncio.run(exercise())


@pytest.fixture
def reader_connections(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from agent_index.sources import azure_devops_point_runtime as runtime
    from agent_index.store.multi_model_store import MultiModelStore

    connections: list[Any] = []

    def open_store(*args: Any, **kwargs: Any) -> MultiModelStore:
        store = MultiModelStore(*args, **kwargs)
        connections.append(store.content_store.db)
        return store

    monkeypatch.setattr(runtime, "MultiModelStore", open_store)
    return connections


def test_read_only_path_owner_never_initializes_or_mutates_state(
    application: worker.Fixture,
) -> None:
    from agent_index.indexing.path_index import PathIndex

    f = application
    missing = f.config.data_dir / "missing" / "path_index.db"
    with pytest.raises(sqlite3.OperationalError, match="existing state"):
        PathIndex(missing, read_only=True)
    assert not missing.parent.exists()
    before = _read_state(f)
    paths = PathIndex(f.config.data_dir / "path_index.db", read_only=True)
    with closing(paths._connect()) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM indexed_files")
    assert _read_state(f) == before


@pytest.mark.parametrize("deleted", [False, True])
def test_selected_reader_reopens_paused_without_native_preparation_or_provider_work(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, deleted: bool,
    reader_connections: list[Any],
) -> None:
    from agent_index.indexing.path_index import PathIndex

    f = application
    if deleted:
        f.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    assert f.run().status == TaskStatus.COMPLETE
    f.reopen()
    trailing = f.tasks.enqueue(f.claim.hints.source, change_hints=replace(
        f.claim.hints, targets=(WorkItemRevisionTarget("7", 4, ("trailing",)),),
    ))
    events: list[tuple[str, str]] = []
    app, lifetime = _point_service_app(f, monkeypatch, model_events=events)

    def no_schema(_: PathIndex) -> None:
        pytest.fail("reader must not initialize path schemas")

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            service = app.state.task_runner.point_service
            assert service.status()["paused"] and not service.status()["admitted"]
            monkeypatch.setattr(PathIndex, "_init_schema", no_schema)
            before = _read_state(f)
            for path in ("workitems/7/body.md", "workitems/7/comments/5.md"):
                result = await service.read_component(path)
                assert result.point_scope == f.claim.hints.scope
                assert result.coverage == "named-target-only"
                assert result.deleted == (deleted and "/comments/" in path)
                assert bool(result.chunks) != result.deleted
            assert _read_state(f) == before
            assert len(reader_connections) == 2
            assert all(not connection.is_open() for connection in reader_connections)
            assert f.tasks.get_task(trailing.id) == trailing
            assert not f.provider.requests and not f.calls and not events
            assert service.status()["paused"] and not service.status()["admitted"]
        assert lifetime == ["open", "closed"]
        assert all(action == "close" for action, _ in events)

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", ["unknown", "checkpoint", "receipt"])
def test_selected_reader_requalifies_and_refuses_missing_or_changed_evidence(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, invalid: str,
    reader_connections: list[Any],
) -> None:
    from agent_index.indexing.path_index import PathIndex
    from agent_index.store.source_layout import MARKER

    f = application
    assert f.run().status == TaskStatus.COMPLETE
    f.reopen()
    app, lifetime = _point_service_app(f, monkeypatch)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            service = app.state.task_runner.point_service
            path = "workitems/7/body.md"
            assert (await service.read_component(path)).chunks
            if invalid == "unknown":
                path = "workitems/8/body.md"
            elif invalid == "checkpoint":
                f.paths.invalidate_batch([(f.claim.hints.canonical_family, path)])
            else:
                marker = f.config.lance_dir / MARKER
                marker.write_text("{}")
            monkeypatch.setattr(PathIndex, "_init_schema", lambda _: pytest.fail("read initialized"))
            before = _read_state(f)
            with pytest.raises(SourceLayoutError):
                await service.read_component(path)
            assert _read_state(f) == before
            assert reader_connections
            assert all(not connection.is_open() for connection in reader_connections)
            assert not f.provider.requests and not f.calls
            assert service.status()["paused"] and not service.status()["admitted"]
        assert lifetime == ["open", "closed"]

    asyncio.run(exercise())


@pytest.mark.parametrize("application", [True], indirect=True)
def test_selected_reader_refuses_a_different_retained_point_binding(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    app, lifetime = _point_service_app(f, monkeypatch)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            before = _read_state(f)
            with pytest.raises(SourceLayoutError):
                await app.state.task_runner.point_service.read_component("workitems/7/body.md")
            assert _read_state(f) == before
            assert not f.provider.requests and not f.calls
        assert lifetime == ["open", "closed"]

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled_owner", ["caller", "loop"])
def test_selected_reader_shutdown_joins_cancelled_reads_before_resource_close(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, cancelled_owner: str,
) -> None:
    from agent_index.indexing.component_read import read_component as read
    from agent_index.sources import azure_devops_point_runtime as runtime

    f = application
    assert f.run().status == TaskStatus.COMPLETE
    f.reopen()
    entered, release = threading.Event(), threading.Event()
    lifetime: list[str] = []
    events: list[tuple[str, str]] = []
    selection: ContextVar[str] = ContextVar("reader_selection", default="unselected")

    def blocked(*args: Any, **kwargs: Any) -> Any:
        assert selection.get() == "selected"
        entered.set()
        assert release.wait(timeout=8)
        assert lifetime == ["open"] and not events
        return read(*args, **kwargs)

    monkeypatch.setattr(runtime, "read_component", blocked)

    async def exercise() -> None:
        selection.set("selected")
        service = PointService(
            config=f.config, point_scope=f.claim.hints.scope,
            runtime_context=lambda: _owned_executor_context(f, lifetime, events, paired=True),
        )
        await service.start(legacy_data_dir=f.config.data_dir.parent / "legacy")
        reader = asyncio.create_task(service.read_component("workitems/7/body.md"))
        stopping: asyncio.Task[None] | None = None
        joining: asyncio.Task[None] | None = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert not await service.drain(timeout=0.01)
            with pytest.raises(RuntimeError, match="not accepting"):
                await service.read_component("workitems/7/body.md")
            await service.resume()
            stopping = asyncio.create_task(service.stop())
            await asyncio.sleep(0)
            if cancelled_owner == "loop":
                for task in asyncio.all_tasks():
                    if task is not asyncio.current_task():
                        task.cancel()
            else:
                stopping.cancel()
            await asyncio.sleep(0.02)
            assert not stopping.done() and lifetime == ["open"]
            joining = asyncio.create_task(service.stop())
            await asyncio.sleep(0)
            for _ in range(2):
                if cancelled_owner == "loop":
                    for task in asyncio.all_tasks():
                        if task is not asyncio.current_task():
                            task.cancel()
                else:
                    reader.cancel()
                    stopping.cancel()
                    joining.cancel()
                await asyncio.sleep(0.02)
                assert not reader.done() and not stopping.done() and not joining.done()
                assert lifetime == ["open"] and not events
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert stopping is not None
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert joining is not None
        with pytest.raises(asyncio.CancelledError):
            await joining
        await service.stop()
        assert lifetime == ["open", "closed"]
        assert sorted(events) == [("close", "first"), ("close", "second")]
        assert not f.provider.requests and not f.calls

    asyncio.run(exercise())


@pytest.mark.parametrize("startup_failure", [False, True])
@pytest.mark.parametrize("cancelled_owner", ["caller", "loop"])
def test_service_joins_blocked_caller_owned_async_exit_before_reporting_stop(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
    startup_failure: bool, cancelled_owner: str,
) -> None:
    from contextlib import asynccontextmanager
    from collections.abc import AsyncIterator

    f = application
    lifetime: list[str] = []

    async def exercise() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def context() -> AsyncIterator[Callable[[TaskRecord], object]]:
            lifetime.append("open")
            try:
                yield lambda _: None
            finally:
                lifetime.append("closing")
                entered.set()
                await release.wait()
                lifetime.append("closed")

        service = PointService(
            config=f.config, point_scope=f.claim.hints.scope, executor_context=context,
        )
        if startup_failure:
            async def fail(_: PointQueueConsumer) -> Any:
                raise RuntimeError("fixture startup rollback")

            monkeypatch.setattr(PointQueueConsumer, "__aenter__", fail)
            operation = asyncio.create_task(service.start(
                legacy_data_dir=f.config.data_dir.parent / "legacy",
            ))
        else:
            await service.start(legacy_data_dir=f.config.data_dir.parent / "legacy")
            operation = asyncio.create_task(service.stop())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            for _ in range(2):
                if cancelled_owner == "loop":
                    for task in asyncio.all_tasks():
                        if task is not asyncio.current_task():
                            task.cancel()
                else:
                    operation.cancel()
                await asyncio.sleep(0.02)
                assert not operation.done() and lifetime == ["open", "closing"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await service.stop()
        assert lifetime == ["open", "closing", "closed"]
        assert not f.provider.requests and not f.calls

    asyncio.run(exercise())


def test_service_rechecks_governance_after_blocked_point_selection(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.indexing.runner import TaskRunner

    f = application
    app, lifetime = _point_service_app(f, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    select = TaskStore.next_point_task
    original = f.tasks.get_task(f.claim.task_id)
    governance = {"status": "ready"}
    waiting = asyncio.Event()
    wait_for_wake = TaskRunner._wait_for_wake

    async def observe_wait(self: TaskRunner, timeout: float) -> None:
        waiting.set()
        await wait_for_wake(self, timeout)

    def blocked_select(self: TaskStore, **kwargs: Any) -> TaskRecord | None:
        selected = select(self, **kwargs)
        if self.data_dir == f.config.data_dir and not entered.is_set():
            entered.set()
            assert release.wait(timeout=8)
        return selected

    monkeypatch.setattr(TaskStore, "next_point_task", blocked_select)
    monkeypatch.setattr(TaskRunner, "_wait_for_wake", observe_wait)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            runner = app.state.task_runner
            runner.set_governance_recheck_fn(lambda checkpoint: {
                **governance, "checkpoint": checkpoint,
            })
            await runner.point_service.activate()
            runner.notify()
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                governance["status"] = "backoff"
                release.set()
                await _wait_point_state(lambda: bool(
                    (state := runner.status()["governance"])
                    and state["checkpoint"] == "pre-mutation:point-launch"
                ))
                assert f.tasks.get_task(f.claim.task_id) == original
                assert not f.provider.requests and not f.calls
                assert not runner.point_service.status()["failed"] and lifetime == ["open"]
                await asyncio.wait_for(waiting.wait(), timeout=8)
                governance["status"] = "ready"
                runner.notify()
                await _wait_point_state(lambda: bool(
                    (task := f.tasks.get_task(f.claim.task_id))
                    and task.status == TaskStatus.COMPLETE
                ))
            finally:
                release.set()
        assert lifetime == ["open", "closed"]

    asyncio.run(exercise())


def test_service_failure_stays_paused_across_wake_and_drain_rollback(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    app, lifetime = _point_service_app(f, monkeypatch)

    def interrupt() -> None:
        raise RuntimeError("fixture interrupted embedding")

    f.hook = interrupt

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            runner = app.state.task_runner
            await runner.point_service.activate()
            runner.notify()
            await _wait_point_state(lambda: runner.point_service.status()["failed"])
            pinned = f.tasks.get_task(f.claim.task_id)
            assert pinned is not None and pinned.point_claim_id == f.claim.point_claim_id
            assert f.retained_count() == 2 and f.provider.body_reads == 2
            calls = list(f.calls)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                assert (await client.post("/drain", json={"timeout": 0})).json()["clean"]
                assert (await client.post("/undrain")).json() == {"draining": False}
                runner.notify()
                await asyncio.sleep(0.1)
                assert f.calls == calls and runner.point_service.status()["failed"]
                assert lifetime == ["open"]
                f.reopen()
                f.hook = None
                await runner.point_service.activate()
                runner.notify()
                await _wait_point_state(lambda: bool(
                    (task := f.tasks.get_task(f.claim.task_id))
                    and task.status == TaskStatus.COMPLETE
                ))
                assert not runner.point_service.status()["failed"]
                assert f.provider.body_reads == 0 and f.calls
        assert lifetime == ["open", "closed"]

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ["selection", "embedding"])
def test_service_drain_and_cancelled_shutdown_join_native_effects_before_client_close(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    f = application
    app, lifetime = _point_service_app(f, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def block() -> None:
        entered.set()
        assert release.wait(timeout=8)
        assert lifetime == ["open"] and not f.connector._http.client.is_closed

    if phase == "embedding":
        f.hook = block
    else:
        select = TaskStore.next_point_task

        def blocked_select(self: TaskStore, **kwargs: Any) -> TaskRecord | None:
            selected = select(self, **kwargs)
            if self.data_dir == f.config.data_dir:
                block()
            return selected

        monkeypatch.setattr(TaskStore, "next_point_task", blocked_select)

    async def exercise() -> None:
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        stopping: asyncio.Task[Any] | None = None
        try:
            runner = app.state.task_runner
            await runner.point_service.activate()
            runner.notify()
            assert await asyncio.to_thread(entered.wait, 5)
            assert runner.status()["running"] and runner.status()["point"]["running"]
            trailing = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                drained = (await client.post("/drain", json={"timeout": 0})).json()
                assert drained["clean"] == drained["drained"] == (phase == "selection")
                assert (await client.post("/undrain")).status_code == 200
            stopping = asyncio.create_task(lifespan.__aexit__(None, None, None))
            for _ in range(2):
                await asyncio.sleep(0.03)
                stopping.cancel()
                assert not stopping.done() and lifetime == ["open"]
            release.set()
            with suppress(asyncio.CancelledError):
                await stopping
            assert lifetime == ["open", "closed"] and f.connector._http.client.is_closed
            completed = f.tasks.get_task(f.claim.task_id)
            assert completed is not None and completed.status == (
                TaskStatus.COMPLETE if phase == "embedding" else TaskStatus.QUEUED_HINTS_V2
            )
            if phase == "selection":
                assert not f.provider.requests and not f.calls
            assert f.tasks.get_task(trailing.id) == trailing
        finally:
            release.set()
            if stopping is None:
                await lifespan.__aexit__(None, None, None)
            else:
                with suppress(asyncio.CancelledError):
                    await stopping

    asyncio.run(exercise())


def test_configured_runtime_pins_executes_and_retries_without_ordinary_dequeue(
    application: worker.Fixture,
) -> None:
    f = application
    original = f.tasks.get_task(f.claim.task_id)
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    assert task.point_claim_id is None and f.tasks.dequeue_next(accept_change_hints=True) is None
    poll = f.config.state_file.read_bytes()
    result = run(f, task)
    assert result.status == TaskStatus.COMPLETE and result.point_claim_id is not None
    assert result.worker_pid is None and f.provider.body_reads == 2 and f.calls
    assert f.tasks.get_task(f.claim.task_id) == original
    f.reopen()
    f.provider.user = "changed"
    assert run(f, result, clients={}) == result
    assert not f.provider.requests and not f.calls
    assert f.config.state_file.read_bytes() == poll


@pytest.mark.parametrize(
    "selection", ["hints", "domain", "models", "model_subset", "connector", "clients"],
)
def test_configured_mismatch_preserves_unpinned_snapshot(
    application: worker.Fixture, selection: str,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    options: dict[str, Any] = {}
    if selection in ("hints", "domain"):
        changed = replace(f.claim.hints, account_id="other")
        options["point_scope"] = changed.scope
        if selection == "domain":
            options["expected_hints"] = changed
    elif selection in ("models", "model_subset"):
        profiles = dict(f.config.model_profiles)
        if selection == "models":
            profiles["first"] = replace(profiles["first"], dim=3)
        else:
            profiles.pop("first")
        options["config"] = replace(f.config, model_profiles=profiles)
    elif selection == "connector":
        f.connector._work_item_queries = []
    else:
        options["clients"] = {}
    with pytest.raises((ValueError, SourceLayoutError)):
        run(f, task, **options)
    assert f.tasks.get_task(task.id) == task
    assert not f.provider.requests and not f.calls


def test_changed_queue_snapshot_is_not_pinned_or_acquired(application: worker.Fixture) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    current = f.tasks.enqueue(f.claim.hints.source, change_hints=replace(
        f.claim.hints, targets=(WorkItemRevisionTarget("7", 9),),
    ))
    assert current.id == task.id
    with pytest.raises(ValueError, match="parked target set"):
        run(f, task)
    assert f.tasks.get_task(task.id) == current
    assert not f.provider.requests and not f.calls


def test_fresh_composition_reuses_retained_acquisition_after_interruption(
    application: worker.Fixture,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    def interrupt() -> None:
        raise RuntimeError("fixture interrupted embedding")
    f.hook = interrupt
    assert run_worker(
        task.id, config=f.config, point_executor=lambda selected: run(f, selected),
    ) == 1
    pinned = f.tasks.get_task(task.id)
    assert pinned is not None and pinned.point_claim_id is not None
    assert pinned.status == TaskStatus.QUEUED_HINTS_V2 and f.retained_count() == 2
    f.reopen()
    f.hook = None
    assert run_worker(
        task.id, config=f.config, point_executor=lambda selected: run(f, selected),
    ) == 0
    completed = f.tasks.get_task(task.id)
    assert completed is not None and completed.status == TaskStatus.COMPLETE
    assert f.provider.body_reads == 0 and f.calls


def test_consumer_selects_whole_scope_fifo_and_leaves_trailing_arrivals_for_next_batch(
    application: worker.Fixture,
) -> None:
    f = application
    other_hints = replace(f.claim.hints, account_id="another-account")
    other = f.tasks.enqueue(other_hints.source, change_hints=other_hints)
    selected_ids: list[str] = []
    trailing: list[TaskRecord] = []

    def execute(selected: TaskRecord) -> TaskRecord:
        selected_ids.append(selected.id)
        if not trailing:
            trailing.append(f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints))
        return run(f, selected)

    async def exercise() -> None:
        async with PointQueueConsumer(
            config=f.config, point_scope=f.claim.hints.scope, point_executor=execute,
        ) as consumer:
            await consumer.resume()
            assert await consumer.run_pending() == 1
            assert selected_ids == [f.claim.task_id]
            assert f.tasks.get_task(trailing[0].id) == trailing[0]
            assert f.tasks.get_task(other.id) == other
            assert await consumer.run_pending(max_tasks=2) == 1
            assert selected_ids == [f.claim.task_id, trailing[0].id]
            assert await consumer.run_pending() == 0
            assert f.tasks.get_task(other.id) == other

    poll = f.config.state_file.read_bytes()
    asyncio.run(exercise())
    assert f.config.state_file.read_bytes() == poll
    for task_id in selected_ids:
        completed = f.tasks.get_task(task_id)
        assert completed is not None and completed.status == TaskStatus.COMPLETE


def test_consumer_failure_pauses_and_reconstruction_requires_explicit_retained_retry(
    application: worker.Fixture,
) -> None:
    f = application

    def interrupt() -> None:
        raise RuntimeError("fixture interrupted embedding")

    f.hook = interrupt

    async def exercise() -> None:
        async with PointQueueConsumer(
            config=f.config, point_scope=f.claim.hints.scope,
            point_executor=lambda selected: run(f, selected),
        ) as consumer:
            await consumer.resume()
            with pytest.raises(RuntimeError, match="exited with code 1"):
                await consumer.run_pending(max_tasks=2)
            parked = f.tasks.get_task(f.claim.task_id)
            assert parked is not None and parked.point_claim_id == f.claim.point_claim_id
            assert parked.status == TaskStatus.QUEUED_HINTS_V2 and f.retained_count() == 2
            before = list(f.provider.requests)
            with pytest.raises(RuntimeError, match="explicit resume"):
                await consumer.run_pending()
            assert f.provider.requests == before
        f.reopen()
        f.hook = None
        async with PointQueueConsumer(
            config=f.config, point_scope=f.claim.hints.scope,
            point_executor=lambda selected: run(f, selected),
        ) as restored:
            with pytest.raises(RuntimeError, match="explicit resume"):
                await restored.run_pending()
            assert not f.provider.requests and not f.calls
            await restored.resume()
            assert await restored.run_pending() == 1
            assert f.provider.body_reads == 0 and f.calls

    asyncio.run(exercise())


@pytest.mark.parametrize("exit_mode", ["cancel", "drain", "stop"])
def test_consumer_quiescence_joins_native_effects_and_never_admits_trailing_work(
    application: worker.Fixture, exit_mode: str,
) -> None:
    f = application
    entered, release = threading.Event(), threading.Event()
    clients_closed = False
    trailing = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)

    def block_embedding() -> None:
        entered.set()
        assert release.wait(8), "Test did not release its native worker"
        assert not clients_closed

    f.hook = block_embedding

    async def exercise() -> None:
        nonlocal clients_closed
        active: asyncio.Task[int] | None = None
        stopping: asyncio.Task[None] | None = None
        try:
            async with PointQueueConsumer(
                config=f.config, point_scope=f.claim.hints.scope,
                point_executor=lambda selected: run(f, selected),
            ) as consumer:
                await consumer.resume()
                active = asyncio.create_task(consumer.run_pending(max_tasks=2))
                assert await asyncio.to_thread(entered.wait, 5)
                with pytest.raises(RuntimeError, match="active batch"):
                    await consumer.run_pending()
                assert not await consumer.drain(timeout=0)
                with pytest.raises(RuntimeError, match="active batch"):
                    await consumer.resume()
                if exit_mode == "cancel":
                    for _ in range(2):
                        active.cancel()
                        await asyncio.sleep(0)
                    assert not active.done()
                elif exit_mode == "stop":
                    stopping = asyncio.create_task(consumer.stop())
                    await asyncio.sleep(0)
                    assert not stopping.done()
                assert f.tasks.get_task(trailing.id) == trailing
                release.set()
                if exit_mode == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await active
                else:
                    assert await active == 1
                if stopping is not None:
                    await stopping
                assert consumer.paused and await consumer.drain(timeout=0)
                assert f.tasks.get_task(trailing.id) == trailing
            clients_closed = True
        finally:
            release.set()
            if active is not None:
                with suppress(asyncio.CancelledError):
                    await active
            if stopping is not None:
                await stopping

    asyncio.run(exercise())
    completed = f.tasks.get_task(f.claim.task_id)
    assert completed is not None and completed.status == TaskStatus.COMPLETE
    assert clients_closed and f.tasks.get_task(trailing.id) == trailing


@pytest.mark.parametrize("stop", [False, True])
def test_consumer_rechecks_admission_after_read_only_selection(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch, stop: bool,
) -> None:
    f = application
    entered, release = threading.Event(), threading.Event()
    select = TaskStore.next_point_task
    calls: list[TaskRecord] = []

    def blocked_select(tasks: TaskStore, **kwargs: Any) -> TaskRecord | None:
        entered.set()
        assert release.wait(8), "Test did not release its queue read"
        return select(tasks, **kwargs)

    monkeypatch.setattr(TaskStore, "next_point_task", blocked_select)

    async def exercise() -> None:
        active: asyncio.Task[int] | None = None
        try:
            async with PointQueueConsumer(
                config=f.config, point_scope=f.claim.hints.scope, point_executor=calls.append,
            ) as consumer:
                await consumer.resume()
                active = asyncio.create_task(consumer.run_pending())
                assert await asyncio.to_thread(entered.wait, 5)
                if stop:
                    await consumer.stop()
                else:
                    assert await consumer.drain(timeout=0)
                release.set()
                assert await active == 0
                assert not calls and not f.provider.requests and not f.calls
                assert f.tasks.next_point_task(point_scope=f.claim.hints.scope) is not None
        finally:
            release.set()
            if active is not None:
                await active

    asyncio.run(exercise())


def test_selected_point_dispatch_owns_synchronous_lifetime_without_process_setup(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.indexing import priority, worker as dispatcher

    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Point dispatch must not configure or register a detached process")

    monkeypatch.setattr(priority, "lower_current_process_priority", forbidden)
    monkeypatch.setattr(dispatcher, "_install_signal_handlers", forbidden)
    monkeypatch.setattr(type(f.tasks), "set_worker", forbidden)
    assert run_worker(task.id, config=f.config) == 2
    assert not f.provider.requests and f.tasks.get_task(task.id) == task

    def execute(selected: TaskRecord) -> TaskRecord:
        assert selected == task
        result = run(f, selected)
        assert result.status == TaskStatus.COMPLETE
        return result

    assert run_worker(task.id, config=f.config, point_executor=execute) == 0
    completed = f.tasks.get_task(task.id)
    assert completed is not None and completed.status == TaskStatus.COMPLETE
    assert completed.worker_pid is None and f.provider.body_reads == 2 and f.calls
    f.reopen()
    f.provider.user = "changed"
    assert run_worker(
        task.id, config=f.config, point_executor=lambda selected: run(f, selected, clients={}),
    ) == 0
    assert not f.provider.requests and not f.calls


def test_executor_mutation_cannot_redirect_completion_to_another_task(
    application: worker.Fixture,
) -> None:
    f = application
    selected = f.tasks.get_task(f.claim.task_id)
    assert selected is not None
    completed = run(f, selected)
    assert completed.status == TaskStatus.COMPLETE
    original = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    assert original.id != completed.id and original.point_claim_id is None

    def redirect(task: TaskRecord) -> None:
        task.__dict__.update(completed.__dict__)

    assert run_worker(original.id, config=f.config, point_executor=redirect) == 2
    assert f.tasks.get_task(original.id) == original
    assert f.tasks.get_task(completed.id) == completed


@pytest.mark.parametrize("cancelled_owner", ["run", "stop", "context", "loop"])
def test_host_joins_real_point_effects_before_cancelled_owner_can_exit(
    application: worker.Fixture, cancelled_owner: str,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    entered, release = threading.Event(), threading.Event()
    clients_closed = False

    def block_embedding() -> None:
        entered.set()
        assert release.wait(8), "Test did not release its native worker"
        assert not clients_closed

    f.hook = block_embedding

    async def exercise() -> None:
        nonlocal clients_closed
        host = PointWorkerHost(config=f.config, point_executor=lambda selected: run(f, selected))
        active: asyncio.Task[int] | None = None

        async def hosting() -> None:
            nonlocal active, clients_closed
            try:
                async with host:
                    if cancelled_owner == "run":
                        await host.run(task.id)
                    else:
                        active = asyncio.create_task(host.run(task.id))
                        assert await asyncio.to_thread(entered.wait, 5)
                        if cancelled_owner == "stop":
                            await host.stop()
            finally:
                clients_closed = True

        owner = asyncio.create_task(hosting())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(2):
                if cancelled_owner == "loop":
                    for running in asyncio.all_tasks() - {asyncio.current_task()}:
                        running.cancel()
                else:
                    owner.cancel()
                await asyncio.sleep(0)
            assert not owner.done() and not clients_closed
            assert not await host.drain(timeout=0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            if active is not None:
                if cancelled_owner == "loop":
                    with pytest.raises(asyncio.CancelledError):
                        await active
                else:
                    assert await active == 0
            assert clients_closed and await host.drain(timeout=0)
            completed = f.tasks.get_task(task.id)
            assert completed is not None and completed.status == TaskStatus.COMPLETE
            assert completed.worker_pid is None and f.provider.body_reads == 2 and f.calls
            with pytest.raises(RuntimeError, match="not accepting"):
                await host.run(task.id)
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await owner
            if active is not None:
                with suppress(asyncio.CancelledError):
                    await active
            await host.stop()

    asyncio.run(exercise())


def test_host_drain_timeout_and_cancel_keep_dispatch_owned_until_local_retry(
    application: worker.Fixture,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    entered, release = threading.Event(), threading.Event()

    def block_embedding() -> None:
        entered.set()
        assert release.wait(8), "Test did not release its native worker"

    f.hook = block_embedding

    async def exercise() -> None:
        async with PointWorkerHost(
            config=f.config, point_executor=lambda selected: run(f, selected),
        ) as host:
            active = asyncio.create_task(host.run(task.id))
            draining: asyncio.Task[bool] | None = None
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                with pytest.raises(RuntimeError, match="active operation"):
                    await host.run(task.id)
                assert not await host.drain(timeout=0)
                with pytest.raises(RuntimeError, match="not accepting"):
                    await host.run(task.id)
                draining = asyncio.create_task(host.drain(timeout=10))
                await asyncio.sleep(0)
                draining.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await draining
                assert not active.done()
                await host.resume()
                with pytest.raises(RuntimeError, match="active operation"):
                    await host.run(task.id)
                release.set()
                assert await active == 0
                assert await host.drain(timeout=0)
                await host.resume()
                f.reopen()
                f.provider.user = "changed"
                assert await host.run(task.id) == 0
                assert not f.provider.requests and not f.calls
            finally:
                release.set()
                if draining is not None:
                    with suppress(asyncio.CancelledError):
                        await draining
                await active

    asyncio.run(exercise())


def test_host_returns_dispatch_refusal_and_retains_failed_pin_for_explicit_retry(
    application: worker.Fixture,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)

    def interrupt() -> None:
        raise RuntimeError("fixture interrupted hosted embedding")

    async def exercise() -> None:
        with pytest.raises(ValueError, match="explicit point executor"):
            PointWorkerHost(config=f.config, point_executor=cast(Any, None))
        host = PointWorkerHost(config=f.config, point_executor=lambda selected: run(f, selected))
        with pytest.raises(RuntimeError, match="not accepting"):
            await host.run(task.id)
        async with host:
            assert await host.run("missing-task") == 2
            assert f.tasks.get_task(task.id) == task and not f.provider.requests
            f.hook = interrupt
            assert await host.run(task.id) == 1
            pinned = f.tasks.get_task(task.id)
            assert pinned is not None and pinned.status == TaskStatus.QUEUED_HINTS_V2
            assert pinned.point_claim_id is not None and f.retained_count() == 2
            f.reopen()
            f.hook = None
            assert await host.run(task.id) == 0
            assert f.provider.body_reads == 0 and f.calls
        with pytest.raises(RuntimeError, match="entered again"):
            await host.__aenter__()
        with pytest.raises(RuntimeError, match="not accepting"):
            await host.resume()

    asyncio.run(exercise())


def test_queue_cancellation_cannot_revoke_hosted_native_application(
    application: worker.Fixture,
) -> None:
    from agent_index.indexing.runner import TaskRunner

    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    legacy = f.tasks.enqueue("legacy")
    entered, release = threading.Event(), threading.Event()
    events: list[str] = []

    class Events:
        def publish(self, event: str, payload: dict[str, Any]) -> None:
            events.append(event)

    competing = TaskStore(f.config.data_dir / "tasks.db")
    runner = TaskRunner(competing, Events())

    def block_embedding() -> None:
        # A callback cannot revoke its own reentrant source-writer ownership.
        assert not competing.cancel(task.id)
        entered.set()
        assert release.wait(8), "Test did not release its native worker"

    f.hook = block_embedding

    async def exercise() -> None:
        async with PointWorkerHost(
            config=f.config, point_executor=lambda selected: run(f, selected),
        ) as host:
            active = asyncio.create_task(host.run(task.id))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                pinned = f.tasks.get_task(task.id)
                assert pinned is not None and pinned.point_claim_id is not None
                assert not await asyncio.to_thread(runner.cancel_task, task.id)
                assert f.tasks.get_task(task.id) == pinned and not events
                assert await asyncio.to_thread(runner.cancel_task, legacy.id)
                assert events == ["task_cancelled"]
                assert not active.done() and not await host.drain(timeout=0)
                release.set()
                assert await active == 0
                completed = f.tasks.get_task(task.id)
                assert completed is not None and completed.status == TaskStatus.COMPLETE
                assert not competing.cancel(task.id)
            finally:
                release.set()
                await active

    asyncio.run(exercise())


def test_settled_failed_point_claim_can_be_cancelled_and_retried(
    application: worker.Fixture,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)

    def interrupt() -> None:
        raise RuntimeError("fixture interrupted hosted embedding")

    async def exercise() -> None:
        async with PointWorkerHost(
            config=f.config, point_executor=lambda selected: run(f, selected),
        ) as host:
            f.hook = interrupt
            assert await host.run(task.id) == 1
            pinned = f.tasks.get_task(task.id)
            assert pinned is not None and pinned.point_claim_id is not None
            assert f.tasks.cancel(task.id)
            retried = f.tasks.retry(task.id)
            assert retried is not None and retried.point_claim_id == pinned.point_claim_id
            assert retried.change_hints == pinned.change_hints and f.retained_count() == 2
            f.reopen()
            f.hook = None
            assert await host.run(task.id) == 0
            assert not f.provider.body_reads and f.calls

    asyncio.run(exercise())


@pytest.mark.parametrize("overlap", ["same", "ancestor", "descendant", "symlink"])
def test_legacy_overlap_refuses_before_pin_or_provider(
    application: worker.Fixture, overlap: str, tmp_path: Path,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    legacy = {
        "same": f.config.data_dir, "ancestor": f.config.data_dir.parent,
        "descendant": f.config.data_dir / "legacy", "symlink": tmp_path / "alias",
    }[overlap]
    if overlap == "symlink":
        legacy.symlink_to(f.config.data_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="separate legacy"):
        run(f, task, legacy_data_dir=legacy)
    assert f.tasks.get_task(task.id) == task and not f.provider.requests


def test_missing_domain_is_not_initialized(application: worker.Fixture, tmp_path: Path) -> None:
    f = application
    config = replace(f.config, data_dir=tmp_path / "unprepared")
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None
    with pytest.raises(ValueError, match="existing configured"):
        run(f, task, config=config)
    assert not config.data_dir.exists() and not f.provider.requests


def test_aliased_child_state_cannot_escape_the_selected_directory(
    application: worker.Fixture, tmp_path: Path,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    foreign = tmp_path / "foreign-clusters.db"
    foreign.write_bytes(b"untouched")
    f.config.clusters_db.symlink_to(foreign)
    with pytest.raises(ValueError, match="inside its selected"):
        run(f, task)
    assert f.tasks.get_task(task.id) == task and foreign.read_bytes() == b"untouched"
    assert not f.provider.requests
