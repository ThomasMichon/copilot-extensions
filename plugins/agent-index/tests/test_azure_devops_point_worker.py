"""Actual named-version HTTP, retained recovery, native application and completion."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pytest

import test_azure_devops_points as named

from agent_index.engine.client import EngineClient
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_application import PreparedComponentApplication
from agent_index.indexing.component_read import read_component
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import CommentVersionTarget, WorkItemRevisionTarget
from agent_index.indexing.source_ownership import SourcePipelineBusy, source_writer
from agent_index.indexing.task_store import TaskRecord, TaskStatus, TaskStore
from agent_index.sources.azure_devops import AzureDevOpsConnector
from agent_index.sources.azure_devops_eligibility import WorkItemEligibility
from agent_index.sources.azure_devops_point_worker import run_azure_devops_point_worker
from agent_index.store.component_ledger import ComponentLedger, initialize_component_ledger
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store

subject = named.subject
SCOPE = "account/endpoint/query"


@dataclass
class Fixture:
    config: IndexConfig
    store: MultiModelStore
    paths: PathIndex
    tasks: TaskStore
    claim: PendingPointClaim
    provider: named.Provider
    connector: AzureDevOpsConnector
    calls: list[tuple[str, list[str]]] = field(default_factory=list)
    hook: Callable[[], None] | None = None

    def run(self, **kwargs: Any) -> TaskRecord:
        options: dict[str, Any] = {
            "connector": self.connector, "claim": self.claim, "scope": SCOPE,
            "paths": self.paths, "tasks": self.tasks,
            "ledger": ComponentLedger(self.config, self.store), "max_rows": 32,
            "clients": {model: cast(EngineClient, Client(self, model))
                        for model in self.config.model_profiles},
        }
        return run_azure_devops_point_worker(**(options | kwargs))

    def reopen(self) -> None:
        self.paths = PathIndex(self.paths._db_path)
        self.store = MultiModelStore(
            self.config.lance_dir, content_table=self.config.content_table,
            point_scope=self.claim.hints.scope,
        )
        for profile in self.config.model_profiles.values():
            self.store.register_model(profile)
        self.calls.clear()
        self.provider.requests.clear()
        self.provider.body_reads = 0

    def retained_count(self) -> int:
        with closing(self.paths._connect()) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM prepared_crawl_rows WHERE kind = 'named-point-acquisition/v1'",
            ).fetchone()[0])


class Client:
    def __init__(self, fixture: Fixture, model: str) -> None:
        self.fixture, self.model = fixture, model

    def embed_texts(self, texts: list[str]) -> np.ndarray[Any, Any]:
        f = self.fixture
        domain = f.store._source_domain
        assert domain is not None and not domain.writing
        assert not getattr(domain._local, "operation", False)
        f.calls.append((self.model, texts))
        if f.hook:
            f.hook()
        return np.asarray([[len(text), 1] for text in texts], dtype=np.float32)


@pytest.fixture
def application(
    subject: tuple[named.Provider, AzureDevOpsConnector, WorkItemEligibility],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> Fixture:
    provider, connector, expected = subject
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    hints = named._claim(connector, expected).hints
    config = IndexConfig(
        data_dir=tmp_path / "owned", content_table="canonical", stream_batch_size=1,
        model_profiles={
            name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
            for name in ("first", "second")
        },
    )
    tasks = TaskStore(config.data_dir / "tasks.db")
    task = tasks.enqueue(hints.source, change_hints=hints)
    claim = PendingPointClaim.from_task(tasks.pin_point_claim(task.id, expected_hints=hints))
    store_scope = hints.scope
    if getattr(request, "param", False):
        store_scope = replace(hints, binding=replace(hints.binding, query_context="other")).scope
    store = initialize_source_store(config, point_scope=store_scope)
    prepare_ingestion_indices(store, max_rows=0, point_scope=store_scope)
    initialize_component_ledger(config, store, point_scope=store_scope)
    paths = PathIndex(config.data_dir / "path_index.db")
    config.state_file.write_text('{"poll":"unchanged"}')
    return Fixture(config, store, paths, tasks, claim, provider, connector)


@pytest.mark.parametrize("deleted", [False, True])
def test_actual_caller_acquires_only_named_targets_applies_reads_and_retries_locally(
    application: Fixture, deleted: bool,
) -> None:
    f = application
    if deleted:
        f.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    trailing = f.tasks.enqueue(f.claim.hints.source, change_hints=replace(
        f.claim.hints, targets=(WorkItemRevisionTarget("7", 4, ("trailing",)),),
    ))
    poll = f.config.state_file.read_bytes()
    result = f.run()
    assert result.status == TaskStatus.COMPLETE and f.calls
    assert f.provider.body_reads == 2 and len(f.provider.requests) == 18
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None
    assert f.tasks.get_task(trailing.id) == trailing
    assert f.config.state_file.read_bytes() == poll
    f.reopen()
    ledger = ComponentLedger(f.config, f.store)
    for path in ("workitems/7/body.md", "workitems/7/comments/5.md"):
        observed = read_component(
            ledger, f.paths, path, point_scope=f.claim.hints.scope,
            application_scope=SCOPE, max_rows=32,
        )
        assert observed.coverage == "named-target-only"
        assert observed.deleted == (deleted and "/comments/" in path)
    f.provider.selected = False
    f.provider.user = "changed-principal"
    assert f.run(clients={}) == result
    assert not f.provider.requests and not f.calls
    assert f.config.state_file.read_bytes() == poll


def test_partial_acquisition_reopens_without_refetching_retained_bodies(
    application: Fixture,
) -> None:
    f = application
    def interrupt() -> None:
        if f.provider.body_reads == 2:
            raise RuntimeError("fixture interrupted acquisition")
    f.provider.on_body = interrupt
    with pytest.raises(RuntimeError, match="interrupted acquisition"):
        f.run()
    assert f.retained_count() == 1
    assert not f.paths.get_all_entries() and not f.calls
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    parent = PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE)
    f.provider.on_body = lambda: None
    f.reopen()
    assert f.run().status == TaskStatus.COMPLETE
    assert f.provider.body_reads == 1 and len(f.provider.requests) == 13
    assert parent is not None and f.config.state_file.read_text() == '{"poll":"unchanged"}'


@pytest.mark.parametrize("drift", ["principal", "membership", "query"])
def test_retained_bytes_require_fresh_eligibility_before_any_native_work(
    application: Fixture, drift: str,
) -> None:
    f = application
    def interrupt() -> None:
        raise RuntimeError("fixture interrupted embedding")
    f.hook = interrupt
    with pytest.raises(RuntimeError, match="interrupted embedding"):
        f.run()
    assert f.retained_count() == 2
    domain = f.store._source_domain
    assert domain is not None
    before = domain.receipt(), f.config.state_file.read_bytes()
    f.reopen()
    f.hook = None
    if drift == "principal":
        f.provider.user = "other"
    elif drift == "membership":
        f.provider.selected = False
    else:
        f.connector._work_item_queries = [{"wiql": named._QUERY + " AND [System.Id] > 0"}]
    with pytest.raises(ValueError, match="binding changed"):
        f.run()
    assert f.provider.body_reads == 0 and not f.calls
    assert f.retained_count() == 2 and not f.paths.get_all_entries()
    assert (domain.receipt(), f.config.state_file.read_bytes()) == before
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2


def test_eligibility_loss_during_embedding_cannot_publish_or_acknowledge(
    application: Fixture,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None
    receipt = domain.receipt()
    def exit_query() -> None:
        f.provider.selected = False
    f.hook = exit_query
    with pytest.raises(ValueError, match="binding changed"):
        f.run()
    assert f.calls and f.retained_count() == 2
    assert domain.receipt() == receipt and not f.paths.get_all_entries()
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    f.reopen()
    f.provider.selected = True
    f.hook = None
    assert f.run().status == TaskStatus.COMPLETE
    assert f.provider.body_reads == 0 and not f.calls


def test_eligibility_loss_after_publication_retains_claim_and_covered_retry(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    original = PreparedComponentApplication.publish
    def publish(self: PreparedComponentApplication) -> tuple[int, int]:
        result = original(self)
        f.provider.selected = False
        return result
    with monkeypatch.context() as patch:
        patch.setattr(PreparedComponentApplication, "publish", publish)
        with pytest.raises(ValueError, match="binding changed"):
            f.run()
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    assert f.paths.get_all_entries() and f.retained_count() == 2
    f.reopen()
    f.provider.selected = True
    domain = f.store._source_domain
    assert domain is not None
    before = domain.receipt()
    assert f.run().status == TaskStatus.COMPLETE
    assert domain.receipt() == before and not f.calls and not f.provider.body_reads


def test_noncurrent_body_cannot_be_retained_or_acknowledged(application: Fixture) -> None:
    f = application
    def cancel() -> None:
        assert not f.tasks.cancel(f.claim.task_id)
        f.tasks.update_status(f.claim.task_id, TaskStatus.CANCELLED)
    f.provider.on_body = cancel
    with pytest.raises(ValueError, match="no longer eligible"):
        f.run()
    assert not f.paths.get_all_entries() and not f.calls
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None and task.status == TaskStatus.CANCELLED
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None


def test_wrong_owner_and_writer_contention_refuse_before_provider_io(application: Fixture) -> None:
    f = application
    with pytest.raises(ValueError, match="independent configured"):
        f.run(paths=PathIndex(f.config.data_dir / "foreign.db"))
    with source_writer(f.config.data_dir), ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(SourcePipelineBusy):
            pool.submit(f.run).result(timeout=10)
    assert not f.provider.requests and not f.calls
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None


@pytest.mark.parametrize("invalid", ["item", "version", "comment", "retry"])
def test_invalid_target_or_retry_budget_refuses_before_provider_io(
    application: Fixture, invalid: str,
) -> None:
    f = application
    target = named._COMMENT
    if invalid == "retry":
        f.connector._http._max_retries = 5
    elif invalid == "item":
        target = replace(target, item_id="07")
    elif invalid == "comment":
        target = replace(target, comment_id="0")
    else:
        target = replace(target, version=2_147_483_648)
    claim = replace(f.claim, hints=replace(f.claim.hints, targets=(target,)))
    with pytest.raises(ValueError):
        f.run(claim=claim)
    assert not f.provider.requests and not f.calls
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None


def _older_claim(f: Fixture) -> PendingPointClaim:
    hints = replace(f.claim.hints, targets=(
        WorkItemRevisionTarget("7", 1, ("older-first",)),
        WorkItemRevisionTarget("7", 2, ("older-second",)),
        CommentVersionTarget("7", "5", 1, ("older-comment",)),
    ))
    task = f.tasks.enqueue(hints.source, change_hints=hints)
    return PendingPointClaim.from_task(f.tasks.pin_point_claim(task.id, expected_hints=hints))


def _projection(f: Fixture) -> tuple[Any, ...]:
    domain = f.store._source_domain
    assert domain is not None
    receipt = domain.receipt()
    versions = {
        name: f.store.content_store.db.open_table(name).version for name in receipt["versions"]
    }
    return receipt, versions, f.paths.get_all_entries(), f.config.state_file.read_bytes()


def test_mixed_older_and_equal_targets_still_require_whole_claim_acquisition(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    f.run()
    hints = replace(f.claim.hints, targets=(
        replace(named._ITEM, revision=2, event_ids=("older",)), named._COMMENT,
    ))
    task = f.tasks.enqueue(hints.source, change_hints=hints)
    f.claim = PendingPointClaim.from_task(f.tasks.pin_point_claim(task.id, expected_hints=hints))
    f.reopen()
    before = _projection(f), f.tasks.get_task(task.id)

    def acquire(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("fixture requires acquisition")

    monkeypatch.setattr("agent_index.sources.azure_devops_point_worker.acquire_named_point", acquire)
    with pytest.raises(RuntimeError, match="requires acquisition"):
        f.run()
    assert (_projection(f), f.tasks.get_task(task.id)) == before
    assert not f.calls and f.provider.body_reads == 0
    assert PendingCrawls(f.paths).read_payload(hints.source, SCOPE) is None


@pytest.mark.parametrize("deleted", [False, True])
def test_fresh_whole_older_claim_completes_without_bodies_preparation_or_native_writes(
    application: Fixture, deleted: bool,
) -> None:
    f = application
    if deleted:
        f.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    f.run()
    f.claim = _older_claim(f)
    trailing = f.tasks.enqueue(f.claim.hints.source, change_hints=replace(
        f.claim.hints, targets=(WorkItemRevisionTarget("7", 4, ("trailing",)),),
    ))
    f.reopen()
    before = _projection(f)
    result = f.run(clients={})
    assert result.status == TaskStatus.COMPLETE and result.change_hints == f.claim.hints
    assert result.result_stats is not None
    outcome = result.result_stats["point_completion"]
    assert outcome["version"] == 2 and outcome["mode"] == "higher-covered"
    assert "pending" not in outcome and "generation" not in outcome
    assert len(outcome["components"]) == 2
    assert {item["component"]["path"]: item["component"]["version"]
            for item in outcome["components"]} == {
        "workitems/7/body.md": 3, "workitems/7/comments/5.md": 2,
    }
    assert f.provider.body_reads == 0 and len(f.provider.requests) == 2 and not f.calls
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None
    assert f.retained_count() == 0 and _projection(f) == before
    assert f.tasks.get_task(trailing.id) == trailing
    f.reopen()
    f.provider.selected = False
    assert f.run(clients={}) == result
    assert not f.provider.requests and not f.calls and _projection(f) == before
    f.paths.mark_indexed(
        f.claim.hints.canonical_family, "workitems/7/body.md", content_hash="old-backup", chunk_count=1,
    )
    f.reopen()
    restored = _projection(f)
    with pytest.raises(SourceLayoutError, match="checkpoint"):
        f.run(clients={})
    assert f.tasks.get_task(result.id) == result and _projection(f) == restored
    assert not f.provider.requests and not f.calls


@pytest.mark.parametrize("drift", ["principal", "membership", "query", "checkpoint", "coverage", "policy"])
def test_unqualified_higher_versions_cannot_skip_acquisition_or_acknowledge(
    application: Fixture, drift: str,
) -> None:
    f = application
    f.run()
    f.claim = _older_claim(f)
    f.reopen()
    if drift == "principal":
        f.provider.user = "other"
    elif drift == "membership":
        f.provider.selected = False
    elif drift == "query":
        f.connector._work_item_queries = [{"wiql": named._QUERY + " AND [System.Id] > 0"}]
    elif drift == "checkpoint":
        f.paths.mark_indexed(
            f.claim.hints.canonical_family, "workitems/7/body.md", content_hash="stale", chunk_count=9,
        )
    elif drift == "coverage":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM embedding_coverage")
    else:
        f.config.model_profiles["first"] = replace(f.config.model_profiles["first"], revision="next")
    before = _projection(f), f.tasks.get_task(f.claim.task_id)
    with pytest.raises((ValueError, SourceLayoutError)):
        f.run()
    assert (_projection(f), f.tasks.get_task(f.claim.task_id)) == before
    assert not f.calls and f.provider.body_reads == 0
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None


@pytest.mark.parametrize("conflict", [False, True])
def test_equal_versions_still_acquire_and_compare_canonical_content(
    application: Fixture, conflict: bool,
) -> None:
    f = application
    f.run()
    hints = replace(f.claim.hints, targets=(
        replace(named._ITEM, event_ids=("equal-body",)),
        replace(named._COMMENT, event_ids=("equal-comment",)),
    ))
    task = f.tasks.enqueue(hints.source, change_hints=hints)
    f.claim = PendingPointClaim.from_task(f.tasks.pin_point_claim(task.id, expected_hints=hints))
    f.reopen()
    before = _projection(f)
    if conflict:
        f.provider.item["fields"]["System.Title"] = "Conflicting equal revision"
        with pytest.raises(ValueError, match="conflicting"):
            f.run()
        parked = f.tasks.get_task(task.id)
        assert parked is not None and parked.status == TaskStatus.QUEUED_HINTS_V2
        assert PendingCrawls(f.paths).read_payload(hints.source, SCOPE) is not None
    else:
        result = f.run()
        assert result.status == TaskStatus.COMPLETE and result.result_stats is not None
        assert result.result_stats["point_completion"]["version"] == 1
    assert f.provider.body_reads == 2 and not f.calls
    assert _projection(f) == before


@pytest.mark.parametrize("application", [True], indirect=True)
def test_incompatible_native_point_selection_refuses_before_acquisition(application: Fixture) -> None:
    f = application
    with pytest.raises(SourceLayoutError):
        f.run()
    assert not f.provider.requests and not f.calls
    assert PendingCrawls(f.paths).read_payload(f.claim.hints.source, SCOPE) is None
