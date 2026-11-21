"""Explicit named-version acquisition through the existing retained point worker."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from typing import TYPE_CHECKING

from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_hints import CommentVersionTarget, PointTarget, WorkItemRevisionTarget
from agent_index.indexing.point_completion import complete_higher_point_claim
from agent_index.indexing.point_worker import _validate_owners, run_retained_point_worker
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PointAcquisition, PreparedPoints
from agent_index.indexing.source_ownership import source_writer
from agent_index.indexing.task_store import TaskStatus
from agent_index.sources.azure_devops_points import (
    _number, _point_component, _require_selection, acquire_named_point,
)

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.indexing.component_manifest import ComponentVersion
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.point_claim import PendingPointClaim
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.indexing.task_store import TaskRecord, TaskStore
    from agent_index.sources.azure_devops import AzureDevOpsConnector
    from agent_index.sources.azure_devops_eligibility import WorkItemEligibility
    from agent_index.store.component_ledger import ComponentLedger


def run_azure_devops_point_worker(
    connector: AzureDevOpsConnector, claim: PendingPointClaim, *, scope: str,
    paths: PathIndex, tasks: TaskStore, ledger: ComponentLedger,
    clients: dict[str, EngineClient], max_rows: int,
    progress_cb: ProgressCallback | None = None,
    before_model_embed: Callable[[str], None] | None = None,
) -> TaskRecord:
    """Acquire missing exact targets and apply only the current whole parked claim.

    The caller selects the compatible component store and owns all clients.
    For U distinct items and M missing targets, at most 132U + 67M logical
    provider requests use the existing retry cap (five attempts per request).
    This is not a transport-memory, aggregate-payload, lock-time or elapsed
    budget. No runner/dequeue capability, polling or automatic repair is added.
    """
    _validate_owners(claim, scope, paths, tasks, ledger, max_rows)
    http = connector._http
    if type(http._max_retries) is not int or not 0 <= http._max_retries <= 4:
        raise ValueError("Azure DevOps point worker exceeds the shared retry budget")
    for target in claim.hints.targets:
        _number(target.item_id)
        version = target.revision if isinstance(target, WorkItemRevisionTarget) else target.version
        if version > 2_147_483_647:
            raise ValueError("Azure DevOps point target version exceeds int32")
        if isinstance(target, CommentVersionTarget):
            _number(target.comment_id)

    def check() -> None:
        if progress_cb:
            progress_cb.check_cancelled()
        with closing(tasks._connect()) as conn:
            tasks._require_point_claim(conn, claim)

    def qualify() -> dict[str, WorkItemEligibility]:
        selections: dict[str, WorkItemEligibility] = {}
        for item in sorted({target.item_id for target in claim.hints.targets}):
            check()
            selected = connector.work_item_eligibility(int(item), cancel_check=check)
            if selected is None:
                raise ValueError("Azure DevOps point worker requires current query eligibility")
            _require_selection(connector, claim, int(item), selected)
            selections[item] = selected
        check()
        return selections

    def qualify_claim() -> None:
        qualify()

    def acquire(target: PointTarget) -> PointAcquisition:
        observed = acquire_named_point(
            connector, claim=claim, target=target, expected=selections[target.item_id],
            cancel_check=check,
        )
        check()
        return PointAcquisition(
            observed.target, observed.component, observed.entry, observed.deletion,
        )

    with source_writer(ledger._config.data_dir, cancel_check=check):
        with closing(tasks._connect()) as conn:
            task = tasks._require_point_claim(conn, claim)
        if task.status != TaskStatus.COMPLETE:
            with ledger._source_domain.operation(
                ledger._store.content_store.db, point_scope=claim.hints.scope,
            ):
                pass
            selections = qualify()
            pending = PendingCrawls(paths)
            payload = pending.read_payload(claim.hints.source, scope)
            first = None
            if payload is None:
                components: dict[tuple[str, str], ComponentVersion] = {}
                for target in claim.hints.targets:
                    component = _point_component(connector, target)
                    key = component.source, component.path
                    previous = components.get(key)
                    if previous is None or previous.version < component.version:
                        components[key] = component
                covered = complete_higher_point_claim(
                    claim, components=tuple(components.values()), scope=scope, paths=paths,
                    tasks=tasks, ledger=ledger, max_rows=max_rows,
                )
                if covered is not None:
                    return covered
                first = acquire(claim.hints.targets[0])
                pending.save(CrawlManifest(
                    claim.hints.source, accounting_family=claim.hints.canonical_family,
                    upserts=[first.entry] if first.entry is not None else [],
                    deletions=[first.deletion] if first.deletion is not None else [],
                    component_versions=[first.component],
                ), scope=scope, full=False, point_claim=claim)
                payload = pending.read_payload(claim.hints.source, scope)
            assert payload is not None
            points = PreparedPoints(PreparedJournal(
                paths, claim.hints.source, scope, payload, point_claim=claim,
            ))
            if first is not None:
                points.retain(first, claim=claim)
            for target in claim.hints.targets:
                check()
                if points.read(target) is not None:
                    continue
                points.retain(acquire(target), claim=claim)
        return run_retained_point_worker(
            claim, scope=scope, paths=paths, tasks=tasks, ledger=ledger, clients=clients,
            max_rows=max_rows, progress_cb=progress_cb, qualify_claim=qualify_claim,
            before_model_embed=before_model_embed,
        )
