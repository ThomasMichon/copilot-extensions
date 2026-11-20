"""Owned configured executor for an explicitly selected point service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from contextvars import copy_context
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.indexing.embedding_runtime import _model_readiness
from agent_index.indexing.point_hints import PointChangeHints
from agent_index.indexing.point_service import (
    PointRuntime, _ConfiguredPointRuntime, require_point_isolation,
)
from agent_index.indexing.runner import _settle
from agent_index.sources.azure_devops_point_runtime import (
    _require_connector_binding, read_configured_azure_devops_component,
    run_configured_azure_devops_points,
)
from agent_index.store.source_layout import _point_scope_json

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig, ModelProfile
    from agent_index.indexing.component_read import ComponentRead
    from agent_index.indexing.embedding_runtime import EmbeddingRuntime
    from agent_index.indexing.point_service import PointExecutor
    from agent_index.indexing.task_store import TaskRecord
    from agent_index.sources.azure_devops import AzureDevOpsConnector
    from agent_index.store.source_layout import PointScope


@asynccontextmanager
async def configured_azure_devops_point_executor(
    *, config: IndexConfig, legacy_data_dir: Path, point_scope: PointScope,
    scope: str, max_rows: int, create_connector: Callable[[], AzureDevOpsConnector],
    embedding_runtime: EmbeddingRuntime,
) -> AsyncIterator[PointExecutor]:
    """Own fresh resources until the service has joined its executing workers.

    Factories must return fresh owned clients. A connector with a borrowed HTTP
    client is refused without closing that client; explicit caller-owned runtime
    calls remain supported. Opening performs no provider requests, native-store
    mutation or engine readiness. Readiness is deferred to missing embeddings.
    """
    cleanup = ExitStack()

    def open_executor() -> PointExecutor:
        if _point_scope_json(point_scope) is None:
            raise ValueError("Point executor requires an exact configured point scope")
        if type(scope) is not str or not scope.strip() or type(max_rows) is not int or max_rows < 1:
            raise ValueError("Point executor requires a scope and finite row budget")
        require_point_isolation(config, legacy_data_dir)
        connector = create_connector()
        if not connector._http.owns_client:
            raise ValueError("Point executor requires a fresh owned connector HTTP client")
        cleanup.callback(connector._http.client.close)
        _require_connector_binding(connector, point_scope)
        clients = {}
        for profile in config.model_profiles.values():
            client = embedding_runtime.create_client(profile)
            cleanup.callback(client.close)
            clients[profile.model_id] = client
        started_engines: dict[str, ModelProfile] = {}

        def stop_started() -> None:
            with ExitStack() as stopping:
                for profile in started_engines.values():
                    stopping.callback(embedding_runtime.stop_engine, profile)

        cleanup.callback(stop_started)

        def execute(task: TaskRecord) -> TaskRecord:
            hints = task.change_hints
            if type(hints) is not PointChangeHints or hints.scope != point_scope:
                raise ValueError("Point executor requires its exact configured task scope")
            prepare, started = _model_readiness(config.model_profiles, clients, embedding_runtime)
            try:
                return run_configured_azure_devops_points(
                    connector, task.id, expected_hints=hints, config=config,
                    legacy_data_dir=legacy_data_dir, point_scope=point_scope, scope=scope,
                    clients=clients, max_rows=max_rows, before_model_embed=prepare,
                )
            finally:
                for profile in started:
                    started_engines[profile.model_id] = profile

        return execute

    loop = asyncio.get_running_loop()
    # Executor Futures survive loop-wide Task cancellation until their threads settle.
    try:
        executor = await _settle(loop.run_in_executor(None, copy_context().run, open_executor))
        yield executor
    finally:
        await _settle(loop.run_in_executor(None, copy_context().run, cleanup.close))


@asynccontextmanager
async def configured_azure_devops_point_runtime(
    *, config: IndexConfig, legacy_data_dir: Path, point_scope: PointScope,
    scope: str, max_rows: int, create_connector: Callable[[], AzureDevOpsConnector],
    embedding_runtime: EmbeddingRuntime,
) -> AsyncIterator[PointRuntime]:
    """Compose the owned executor and per-operation reader over the same binding.

    The service joins admitted execution and reads before this context exits.
    Reader calls cannot override the selected domain, application scope or budget.
    """
    async with configured_azure_devops_point_executor(
        config=config, legacy_data_dir=legacy_data_dir, point_scope=point_scope,
        scope=scope, max_rows=max_rows, create_connector=create_connector,
        embedding_runtime=embedding_runtime,
    ) as execute:
        def read(path: str) -> ComponentRead:
            return read_configured_azure_devops_component(
                path, config=config, legacy_data_dir=legacy_data_dir,
                point_scope=point_scope, scope=scope, max_rows=max_rows,
            )

        yield _ConfiguredPointRuntime(config, point_scope, execute, read)
