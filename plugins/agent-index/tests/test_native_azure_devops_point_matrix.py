"""Opt-in point-consumer counts against unrelated native content, vectors and paths."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from importlib import import_module
import json
from pathlib import Path
import re
import sys
from time import perf_counter, process_time
from typing import Any, cast

import numpy as np
import pytest

import test_azure_devops_point_runtime as runtime
import test_azure_devops_point_worker as worker
from test_native_delta_matrix import io_counts, measure_round

from agent_index.chunking.base import Chunk
from agent_index.indexing.component_application import _policy
from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion
from agent_index.indexing.component_read import read_component
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import CommentVersionTarget, WorkItemRevisionTarget
from agent_index.indexing.point_queue_consumer import PointQueueConsumer
from agent_index.indexing.task_store import TaskRecord, TaskStatus, TaskStore
from agent_index.indexing.work_counts import measure_reindex
from agent_index.store.component_ledger import (
    IDENTITY, TABLE, ComponentEvidence, ComponentLedger, _encode, _key, initialize_component_ledger,
)
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.scalar_index import prepare_index
from agent_index.store.source_layout import initialize_source_store

application = worker.application
subject = worker.subject
pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def _seed(template: worker.Fixture, directory: Path, size: int) -> tuple[worker.Fixture, list[Chunk]]:
    config = replace(template.config, data_dir=directory)
    tasks = TaskStore(config.data_dir / "tasks.db")
    hints = template.claim.hints
    task = tasks.enqueue(hints.source, change_hints=hints)
    claim = PendingPointClaim.from_task(tasks.pin_point_claim(task.id, expected_hints=hints))
    store = initialize_source_store(config, point_scope=hints.scope)
    prepare_ingestion_indices(store, max_rows=0, point_scope=hints.scope)
    initialize_component_ledger(config, store, point_scope=hints.scope)
    paths = PathIndex(config.data_dir / "path_index.db")
    config.state_file.write_text('{"poll":"unchanged"}')
    chunks = [
        Chunk("Unchanged native content.", f"unrelated/{i}.md", "text", "markdown",
              1, 1, hints.canonical_family)
        for i in range(size)
    ]
    domain = store._source_domain
    assert domain is not None
    with domain._write(store.content_store.db, point_scope=hints.scope):
        assert store.upsert_point_content(chunks, point_scope=hints.scope) == size
        prepare_ingestion_indices(store, max_rows=size, point_scope=hints.scope)
        for model in config.model_profiles:
            assert store.upsert_point_vectors(
                model, chunks, np.ones((size, 2), dtype=np.float32), point_scope=hints.scope,
            ) == size
        prepare_ingestion_indices(store, max_rows=size, point_scope=hints.scope)
    ledger = ComponentLedger(config, store)
    absent = capture_file_projection(
        config, store, hints.canonical_family, "unrelated-ledger/template.md",
        max_rows=1, point_scope=hints.scope,
    )
    records = []
    policy = _policy(config)
    for index in range(size):
        path = f"unrelated-ledger/{index}.md"
        evidence = ComponentEvidence(
            ComponentContent(
                ComponentVersion(hints.canonical_family, path, 1, "fixture/v1"), "deleted", True,
            ),
            worker.SCOPE, policy, replace(absent, file_path=path),
        )
        records.append({
            "point_key": _key(hints.canonical_family, path), "record": _encode(evidence),
        })
    # Bulk fixture history is not an ordinary application or a production batch API.
    with domain._write(store.content_store.db, point_scope=hints.scope):
        table = ledger._bound_table()
        identity = table.schema.metadata[IDENTITY].decode("ascii")
        domain.effect("component-evidence:" + TABLE)
        table.add(records)
        prepare_index(ledger, column="point_key", max_rows=size, point_scope=hints.scope)
        ledger._bind(ledger._get_or_create_table(), identity)
    paths.mark_indexed_batch([
        (hints.canonical_family, chunk.file_path, "unchanged-fixture", 1) for chunk in chunks
    ])
    return worker.Fixture(
        config, store, paths, tasks, claim, template.provider, template.connector,
    ), [chunks[0], chunks[size // 2], chunks[-1]]


def _samples(f: worker.Fixture, chunks: list[Chunk], size: int) -> Any:
    scope = f.claim.hints.scope
    table = ComponentLedger(f.config, f.store)._bound_table()
    ledger_rows = [
        table.search().where(
            f"point_key = '{_key(scope[1], f'unrelated-ledger/{index}.md')}'",
        ).limit(2).to_list()
        for index in (0, size // 2, size - 1)
    ]
    assert all(len(rows) == 1 for rows in ledger_rows)
    return (
        [capture_file_projection(
            f.config, f.store, chunk.source, chunk.file_path, max_rows=1, point_scope=scope,
        ) for chunk in chunks],
        [f.paths.get_entry(chunk.source, chunk.file_path) for chunk in chunks],
        ledger_rows,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
@pytest.mark.parametrize("deleted", [False, True], ids=["content", "tombstone"])
def test_point_consumer_work_is_independent_of_unrelated_native_corpus(
    application: worker.Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    deleted: bool, capsys: pytest.CaptureFixture[str],
) -> None:
    query_builder = import_module("lancedb.query").LanceEmptyQueryBuilder
    template = application
    if deleted:
        template.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    selected_rows = 2 if deleted else 3
    baselines: dict[str, Any] = {}
    for size in (100, 10_000, 100_000):
        started = perf_counter()
        f, samples = _seed(template, tmp_path / str(size), size)
        setup_seconds = perf_counter() - started
        before_samples = _samples(f, samples, size)
        poll = f.config.state_file.read_bytes()
        completed: TaskRecord | None = None
        for name in ("apply", "duplicate", "superseded", "completed-retry"):
            f.reopen()
            hints = f.claim.hints
            if name == "superseded":
                hints = replace(hints, targets=(
                    WorkItemRevisionTarget("7", 1, ("older-first",)),
                    WorkItemRevisionTarget("7", 2, ("older-second",)),
                    CommentVersionTarget("7", "5", 1, ("older-comment",)),
                ))
            if name in ("duplicate", "superseded"):
                task = f.tasks.enqueue(hints.source, change_hints=hints)
            elif name == "apply":
                initial = f.tasks.get_task(f.claim.task_id)
                assert initial is not None
                task = initial
            else:
                assert completed is not None
                task = completed
            before = worker._projection(f)
            results: list[dict[str, Any]] = []
            diagnostic_io = {key: 0 for key in io_counts()}
            diagnostic_plans: dict[str, str] = {}
            analyze_plan = query_builder.analyze_plan

            def observed_plan(query: Any, *args: Any, **kwargs: Any) -> Any:
                before_io = io_counts()
                try:
                    plan = analyze_plan(query, *args, **kwargs)
                    diagnostic_plans.setdefault(
                        f"{query._table.name}:{str(query._where).split(' ', 1)[0]}",
                        re.sub(r"uri=[^,\n]+", "uri=<fixture>", plan),
                    )
                    return plan
                finally:
                    after_io = io_counts()
                    for key in diagnostic_io:
                        diagnostic_io[key] += after_io[key] - before_io[key]

            @measure_reindex
            def execute(selected: TaskRecord) -> dict[str, object]:
                result = runtime.run(f, selected, expected_hints=selected.change_hints)
                assert result.status == TaskStatus.COMPLETE
                ledger = ComponentLedger(f.config, f.store)
                for path in ("workitems/7/body.md", "workitems/7/comments/5.md"):
                    observed = read_component(
                        ledger, f.paths, path, point_scope=f.claim.hints.scope,
                        application_scope=worker.SCOPE, max_rows=32,
                    )
                    assert observed.coverage == "named-target-only"
                    assert observed.deleted == (deleted and "/comments/" in path)
                return {"task": result}

            def consume() -> dict[str, Any]:
                async def run() -> None:
                    async with PointQueueConsumer(
                        config=f.config, point_scope=f.claim.hints.scope,
                        point_executor=lambda selected: results.append(execute(selected)),
                    ) as consumer:
                        assert consumer.paused
                        await consumer.resume()
                        assert await consumer.run_pending(max_tasks=1) == 1
                if name == "completed-retry":
                    results.append(execute(task))
                else:
                    asyncio.run(run())
                assert len(results) == 1
                return results[0]

            with monkeypatch.context() as patch:
                patch.setattr(query_builder, "analyze_plan", observed_plan)
                for method in ("get_all_entries", "get_paths_by_family", "get_content_hashes"):
                    patch.setattr(PathIndex, method, lambda *_a, **_k: pytest.fail(
                        "named consumer enumerated corpus checkpoints",
                    ))
                wall, cpu = perf_counter(), process_time()
                measurement = measure_round(consume, patch)
                measurement["wall_seconds"] = perf_counter() - wall
                measurement["process_cpu_seconds"] = process_time() - cpu
                measurement["diagnostic_io"] = diagnostic_io
                if name == "completed-retry":
                    measurement["diagnostic_plans"] = diagnostic_plans
            completed = cast(TaskRecord, results[0]["task"])
            assert completed.id == task.id and completed.change_hints == task.change_hints
            if name == "completed-retry":
                assert completed == task
            counts = measurement["counts"]
            assert f.provider.body_reads == (2 if name in ("apply", "duplicate") else 0)
            assert len(f.provider.requests) == {
                "apply": 18, "duplicate": 18, "superseded": 2, "completed-retry": 0,
            }[name]
            assert counts["content_rows_upserted"] == (selected_rows if name == "apply" else 0)
            for model in counts["models"].values():
                assert model["chunks_embedded"] == model["vector_rows_upserted"] == (
                    selected_rows if name == "apply" else 0
                )
            if name != "apply":
                assert not f.calls and worker._projection(f) == before
            assert counts["fts_ensure_attempts"] == counts["cluster_pass_attempts"] == 0
            assert f.retained_count() == 0
            assert PendingCrawls(f.paths).read_payload(hints.source, worker.SCOPE) is None
            assert f.config.state_file.read_bytes() == poll
            if name in baselines:
                assert counts == baselines[name]["counts"], (size, name, measurement)
                assert measurement["native"] == baselines[name]["native"], (size, name, measurement)
            else:
                baselines[name] = measurement
            native = measurement["native"]
            assert native["queries"] > 0
            assert native["rows_scanned"] <= 32 * native["queries"], (size, name, measurement)
            if name == "completed-retry":
                f.reopen()
                owner_io: dict[str, Any] = {}

                def observed_owner(
                    label: str, operation: Callable[..., Any], *args: Any, **kwargs: Any,
                ) -> Any:
                    prior = io_counts()
                    try:
                        return operation(*args, **kwargs)
                    finally:
                        after = io_counts()
                        entry = owner_io.setdefault(label, {
                            "calls": 0, "io": {key: 0 for key in prior}, "call_rchar": [],
                        })
                        entry["calls"] += 1
                        entry["call_rchar"].append(after["rchar"] - prior["rchar"])
                        for key in prior:
                            entry["io"][key] += after[key] - prior[key]

                to_arrow = query_builder.to_arrow

                def observed_query(query: Any, *args: Any, **kwargs: Any) -> Any:
                    return observed_owner(
                        f"query:{query._table.name}:{str(query._where).split(' ', 1)[0]}",
                        to_arrow, query, *args, **kwargs,
                    )

                with monkeypatch.context() as patch:
                    patch.setattr(query_builder, "to_arrow", observed_query)
                    patch.setattr(query_builder, "analyze_plan", lambda *_a, **_k: pytest.fail(
                        "ordinary retry replayed a diagnostic query",
                    ))
                    for method in ("get_all_entries", "get_paths_by_family", "get_content_hashes"):
                        patch.setattr(PathIndex, method, lambda *_a, **_k: pytest.fail(
                            "ordinary retry enumerated corpus checkpoints",
                        ))
                    for module in (
                        "scalar_index", "source_bucket_index", "ingestion_index",
                        "component_ledger", "chunk_id_index", "file_projection",
                        "point_content", "point_reconciliation",
                    ):
                        owner = import_module(f"agent_index.store.{module}")
                        patch.setattr(
                            owner, "inspect_index",
                            partial(observed_owner, f"inspect:{module}", owner.inspect_index),
                        )
                    before_ordinary = io_counts()
                    wall, cpu = perf_counter(), process_time()
                    ordinary = execute(task)
                    elapsed, cpu_elapsed = perf_counter() - wall, process_time() - cpu
                    after_ordinary = io_counts()
                ordinary_counts = cast(dict[str, Any], ordinary["work_counts"])
                ordinary_counts.pop("source_mutations")
                assert ordinary["task"] == task and ordinary_counts == counts
                assert sum(
                    entry["calls"] for label, entry in owner_io.items()
                    if label.startswith("query:")
                ) == native["queries"]
                assert not f.provider.requests and not f.calls
                assert worker._projection(f) == before
                measurement["ordinary_completed_retry"] = {
                    "counts": ordinary_counts, "wall_seconds": elapsed,
                    "process_cpu_seconds": cpu_elapsed,
                    "io": {key: after_ordinary[key] - before_ordinary[key] for key in before_ordinary},
                    "native_plan_replay": False,
                    "inclusive_owner_io": owner_io,
                }
            with capsys.disabled():
                print("POINT_NATIVE_WORK " + json.dumps({
                    "size": size, "deleted": deleted, "round": name,
                    "setup_seconds": setup_seconds, **measurement,
                }, sort_keys=True))
        after_samples = _samples(f, samples, size)
        assert before_samples == after_samples
        assert ComponentLedger(f.config, f.store)._bound_table().count_rows() == size + 2
        assert f.store.content_store.db.open_table(f.config.content_table).count_rows() == (
            size + selected_rows
        )
        for model in f.config.model_profiles:
            assert f.store.get_vector_table(model)._get_or_create_table().count_rows() == (
                size + selected_rows
            )
