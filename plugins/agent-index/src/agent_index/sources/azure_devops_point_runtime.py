"""Explicit configured composition; ordinary workers remain legacy-only."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.component_read import ComponentRead, read_component
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import PointChangeHints
from agent_index.indexing.point_service import require_point_isolation
from agent_index.indexing.source_ownership import source_writer
from agent_index.indexing.task_store import TaskStatus, TaskStore
from agent_index.sources.azure_devops_point_worker import run_azure_devops_point_worker
from agent_index.sources.base import nonsecret_target
from agent_index.store.component_ledger import ComponentLedger
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import PointScope, SourceLayoutError, _configured_point_models

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.indexing.task_store import TaskRecord
    from agent_index.sources.azure_devops import AzureDevOpsConnector


def _require_connector_binding(
    connector: AzureDevOpsConnector, point_scope: PointScope,
) -> None:
    if (
        point_scope[1] != connector.source_name + ":workitem-components"
        or point_scope[3].endpoint != nonsecret_target(
            str(connector._http.client.base_url).rstrip("/"),
        )
        or point_scope[3].query_scope != json.dumps(connector._work_item_queries, sort_keys=True)
    ):
        raise ValueError("Point runtime connector differs from its configured binding")


def read_configured_azure_devops_component(
    path: str, *, config: IndexConfig, legacy_data_dir: Path, point_scope: PointScope,
    scope: str, max_rows: int,
) -> ComponentRead:
    """Open existing owners for one qualification, without acquisition or preparation."""
    if type(scope) is not str or not scope.strip() or type(max_rows) is not int or max_rows < 1:
        raise ValueError("Point reader requires its application scope and finite row budget")
    require_point_isolation(config, legacy_data_dir)
    paths = PathIndex(config.data_dir / "path_index.db", read_only=True)
    store = MultiModelStore(
        config.lance_dir, content_table=config.content_table, point_scope=point_scope,
    )
    with store.content_store.db:
        # This configures in-memory routing; retained native model bindings are never created.
        for profile in config.model_profiles.values():
            store.register_model(profile)
        ledger = ComponentLedger(config, store)
        domain = ledger._source_domain
        with domain.operation(store.content_store.db, point_scope=point_scope):
            if domain.receipt().get("point_models") != _configured_point_models(config):
                raise SourceLayoutError("Point reader model selection differs from its retained binding")
            return read_component(
                ledger, paths, path, point_scope=point_scope,
                application_scope=scope, max_rows=max_rows,
            )


def run_configured_azure_devops_points(
    connector: AzureDevOpsConnector, task_id: str, *, expected_hints: PointChangeHints,
    config: IndexConfig, legacy_data_dir: Path, point_scope: PointScope, scope: str,
    clients: dict[str, EngineClient], max_rows: int,
    progress_cb: ProgressCallback | None = None,
    before_model_embed: Callable[[str], None] | None = None,
) -> TaskRecord:
    """Select existing isolated owners, pin one exact snapshot and execute it.

    All configuration and clients are explicit; the caller owns their lifetime.
    No bootstrap, engine discovery, ordinary dequeue, capability declaration,
    polling recovery or detached worker is performed. Failed execution retains
    the parked pin and its recovery material; a completed retry stays local.
    """
    if (
        type(expected_hints) is not PointChangeHints or expected_hints.scope != point_scope
        or type(scope) is not str or not scope.strip()
        or type(max_rows) is not int or max_rows < 1
    ):
        raise ValueError("Point runtime requires its complete configured scope and row budget")
    require_point_isolation(config, legacy_data_dir)
    _require_connector_binding(connector, point_scope)
    cancel_check = progress_cb.check_cancelled if progress_cb is not None else None
    with source_writer(config.data_dir, cancel_check=cancel_check):
        store = MultiModelStore(
            config.lance_dir, content_table=config.content_table, point_scope=point_scope,
        )
        for profile in config.model_profiles.values():
            store.register_model(profile)
        ledger = ComponentLedger(config, store)
        domain = store._source_domain
        if domain is None:
            raise SourceLayoutError("Point runtime requires a prepared point domain")
        with domain.operation(store.content_store.db, point_scope=point_scope):
            if domain.receipt().get("point_models") != _configured_point_models(config):
                raise SourceLayoutError("Point runtime model selection differs from its retained binding")
            ledger._bound_table()
        tasks = TaskStore(config.data_dir / "tasks.db")
        paths = PathIndex(config.data_dir / "path_index.db")
        task = tasks.get_task(task_id)
        if (
            task is not None and task.status == TaskStatus.COMPLETE
            and task.change_hints == expected_hints and task.point_claim_id is not None
        ):
            claim = PendingPointClaim(task.id, task.point_claim_id, expected_hints)
        else:
            if set(clients) != set(config.model_profiles):
                raise ValueError("Point runtime requires all configured model clients")
            claim = PendingPointClaim.from_task(
                tasks.pin_point_claim(task_id, expected_hints=expected_hints),
            )
        return run_azure_devops_point_worker(
            connector, claim, scope=scope, paths=paths, tasks=tasks, ledger=ledger,
            clients=clients, max_rows=max_rows, progress_cb=progress_cb,
            before_model_embed=before_model_embed,
        )
