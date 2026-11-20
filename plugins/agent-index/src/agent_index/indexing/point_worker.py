"""Explicit in-process application of a fully retained point claim."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.indexing.component_application import PreparedComponentApplication
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_completion import complete_point_claim
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PreparedPoints
from agent_index.indexing.source_ownership import source_writer
from agent_index.indexing.task_store import TaskStatus

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.indexing.task_store import TaskRecord, TaskStore
    from agent_index.store.component_ledger import ComponentLedger


def _validate_owners(
    claim: PendingPointClaim, scope: str, paths: PathIndex, tasks: TaskStore,
    ledger: ComponentLedger, max_rows: int,
) -> None:
    config, domain = ledger._config, ledger._source_domain
    if (
        type(claim) is not PendingPointClaim
        or type(scope) is not str or not scope.strip()
        or Path(tasks._db_path).resolve() != (config.data_dir / "tasks.db").resolve()
        or Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve()
        or not all(Path(path).is_file() for path in (tasks._db_path, paths._db_path))
        or type(max_rows) is not int or max_rows < 1
        or domain.writing or getattr(domain._local, "operation", False)
    ):
        raise ValueError("Retained point worker requires independent configured claim owners")


def run_retained_point_worker(
    claim: PendingPointClaim, *, scope: str, paths: PathIndex, tasks: TaskStore,
    ledger: ComponentLedger, clients: dict[str, EngineClient], max_rows: int,
    progress_cb: ProgressCallback | None = None,
    qualify_claim: Callable[[], None] | None = None,
    before_model_embed: Callable[[str], None] | None = None,
) -> TaskRecord:
    """Apply retained targets, then qualify and acknowledge only their whole claim.

    The caller owns client lifetime and acquisition/eligibility qualification.
    Its optional qualifier runs before preparation, publication and completion,
    outside native operations; completed retries do not invoke it.
    This synchronous in-process worker never acquires targets, opens clients,
    registers a detached worker, repairs uncertain state or advances polling.
    Exceptions retain the parked claim and its subordinate recovery material.
    """
    _validate_owners(claim, scope, paths, tasks, ledger, max_rows)
    config, store = ledger._config, ledger._store

    def check_claim() -> TaskRecord:
        if progress_cb:
            progress_cb.check_cancelled()
        with closing(tasks._connect()) as conn:
            return tasks._require_point_claim(conn, claim)

    cancel_check = progress_cb.check_cancelled if progress_cb is not None else None
    with source_writer(config.data_dir, cancel_check=cancel_check):
        task = check_claim()
        if task.status != TaskStatus.COMPLETE:
            pending = PendingCrawls(paths)
            payload = pending.read_payload(claim.hints.source, scope)
            if payload is None:
                raise PendingCrawlConflict("Retained point worker requires existing acquisition")
            points = PreparedPoints(PreparedJournal(
                paths, claim.hints.source, scope, payload,
                point_claim=claim, require_existing=True,
            ))
            manifest = points.compose()
            if qualify_claim:
                qualify_claim()
            check_claim()
            application = PreparedComponentApplication(
                manifest, scope=scope, family=claim.hints.canonical_family,
                max_rows=max_rows, config=config, paths=paths, store=store,
                clients=clients, point_claim=claim, progress_cb=progress_cb,
                before_model_embed=before_model_embed,
            )
            if qualify_claim:
                qualify_claim()
            check_claim()
            application.publish()
            if qualify_claim:
                qualify_claim()
        check_claim()
        return complete_point_claim(
            claim, scope=scope, paths=paths, tasks=tasks, ledger=ledger, max_rows=max_rows,
        )
