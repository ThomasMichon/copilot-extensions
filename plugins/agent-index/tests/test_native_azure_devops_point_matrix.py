"""Opt-in point-consumer counts against unrelated native content, vectors and paths."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from time import perf_counter, process_time
from typing import Any, cast

import numpy as np
import pytest

import test_azure_devops_point_runtime as runtime
import test_azure_devops_point_worker as worker
from test_native_delta_matrix import measure_round

from agent_index.chunking.base import Chunk
from agent_index.indexing.component_read import read_component
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import CommentVersionTarget, WorkItemRevisionTarget
from agent_index.indexing.point_queue_consumer import PointQueueConsumer
from agent_index.indexing.task_store import TaskRecord, TaskStatus, TaskStore
from agent_index.indexing.work_counts import measure_reindex
from agent_index.store.component_ledger import ComponentLedger, initialize_component_ledger
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
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
    paths.mark_indexed_batch([
        (hints.canonical_family, chunk.file_path, "unchanged-fixture", 1) for chunk in chunks
    ])
    return worker.Fixture(
        config, store, paths, tasks, claim, template.provider, template.connector,
    ), [chunks[0], chunks[size // 2], chunks[-1]]


def _samples(f: worker.Fixture, chunks: list[Chunk]) -> Any:
    scope = f.claim.hints.scope
    return (
        [capture_file_projection(
            f.config, f.store, chunk.source, chunk.file_path, max_rows=1, point_scope=scope,
        ) for chunk in chunks],
        [f.paths.get_entry(chunk.source, chunk.file_path) for chunk in chunks],
    )


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
@pytest.mark.parametrize("deleted", [False, True], ids=["content", "tombstone"])
def test_point_consumer_work_is_independent_of_unrelated_native_corpus(
    application: worker.Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    deleted: bool, capsys: pytest.CaptureFixture[str],
) -> None:
    template = application
    if deleted:
        template.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    selected_rows = 2 if deleted else 3
    baselines: dict[str, Any] = {}
    for size in (100, 10_000, 100_000):
        started = perf_counter()
        f, samples = _seed(template, tmp_path / str(size), size)
        setup_seconds = perf_counter() - started
        before_samples = _samples(f, samples)
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
                for method in ("get_all_entries", "get_paths_by_family", "get_content_hashes"):
                    patch.setattr(PathIndex, method, lambda *_a, **_k: pytest.fail(
                        "named consumer enumerated corpus checkpoints",
                    ))
                wall, cpu = perf_counter(), process_time()
                measurement = measure_round(consume, patch)
                measurement["wall_seconds"] = perf_counter() - wall
                measurement["process_cpu_seconds"] = process_time() - cpu
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
            with capsys.disabled():
                print("POINT_NATIVE_WORK " + json.dumps({
                    "size": size, "deleted": deleted, "round": name,
                    "setup_seconds": setup_seconds, **measurement,
                }, sort_keys=True))
        after_samples = _samples(f, samples)
        assert before_samples == after_samples
        assert f.store.content_store.db.open_table(f.config.content_table).count_rows() == (
            size + selected_rows
        )
        for model in f.config.model_profiles:
            assert f.store.get_vector_table(model)._get_or_create_table().count_rows() == (
                size + selected_rows
            )
