"""Opt-in HTTP/service supersession counts beside unrelated native history."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from time import perf_counter, process_time
from typing import Any

import httpx
import pytest

import test_azure_devops_point_runtime as runtime
import test_azure_devops_point_worker as worker
from test_azure_devops_point_service_recovery import _reopen_connector
from test_native_azure_devops_point_matrix import _samples, _seed
from test_native_delta_matrix import measure_round

from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.point_hints import CommentVersionTarget, WorkItemRevisionTarget
from agent_index.indexing.task_store import TaskRecord, TaskStatus
from agent_index.indexing.work_counts import measure_reindex
from agent_index.store.component_ledger import ComponentLedger

application = worker.application
subject = worker.subject
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("service"),
]


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
@pytest.mark.parametrize("deleted", [False, True], ids=["content", "tombstone"])
def test_http_supersession_work_is_independent_of_unrelated_native_history(
    application: worker.Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    deleted: bool, capsys: pytest.CaptureFixture[str],
) -> None:
    template = application
    if deleted:
        template.provider.comment = {"id": 5, "version": 2, "isDeleted": True}
    baseline: dict[str, Any] | None = None
    for size in (100, 10_000, 100_000):
        started = perf_counter()
        template.provider.selected = True
        f, samples = _seed(template, tmp_path / str(size), size)
        if f.connector._http.client.is_closed:
            _reopen_connector(f)
        original = f.run()
        assert original.status == TaskStatus.COMPLETE
        f.reopen()
        before = worker._projection(f)
        before_samples = _samples(f, samples, size)
        poll = f.config.state_file.read_bytes()
        setup_seconds = perf_counter() - started
        hints = replace(f.claim.hints, targets=(
            WorkItemRevisionTarget("7", 1, ("older-first",)),
            WorkItemRevisionTarget("7", 2, ("older-second",)),
            CommentVersionTarget("7", "5", 1, ("older-comment",)),
        ))

        with monkeypatch.context() as patch:
            app, clients = runtime._point_service_app(f, patch)

            async def exercise() -> TaskRecord:
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
                            (task := f.tasks.get_task(task_id))
                            and task.status == TaskStatus.COMPLETE
                        ))
                        completed = await asyncio.to_thread(f.tasks.get_task, task_id)
                        assert completed is not None and completed.change_hints == hints
                        assert completed.result_stats is not None
                        outcome = completed.result_stats["point_completion"]
                        assert outcome["version"] == 2 and outcome["mode"] == "higher-covered"
                        assert f.provider.body_reads == 0 and not f.calls
                        assert len(f.provider.requests) == 2
                        assert await asyncio.to_thread(f.retained_count) == 0
                        assert await asyncio.to_thread(f.tasks.get_task, original.id) == original
                        assert 2 not in (await client.get("/health")).json()[
                            "indexing_contract"
                        ]["execution_versions"]
                assert clients == ["open", "closed"] and f.connector._http.client.is_closed
                await asyncio.to_thread(f.reopen)
                f.provider.selected = False
                assert await asyncio.to_thread(
                    runtime.run, f, completed, expected_hints=hints, clients={},
                ) == completed
                assert not f.provider.requests and not f.calls
                assert PendingCrawls(f.paths).read_payload(hints.source, worker.SCOPE) is None
                return completed

            @measure_reindex
            def consume() -> dict[str, Any]:
                return {"task": asyncio.run(exercise())}

            for method in ("get_all_entries", "get_paths_by_family", "get_content_hashes"):
                patch.setattr(PathIndex, method, lambda *_a, **_k: pytest.fail(
                    "named HTTP/service execution enumerated corpus checkpoints",
                ))
            wall, cpu = perf_counter(), process_time()
            measurement = measure_round(consume, patch)
            measurement["wall_seconds"] = perf_counter() - wall
            measurement["process_cpu_seconds"] = process_time() - cpu

        counts = measurement["counts"]
        assert counts["content_rows_upserted"] == 0
        for model in counts["models"].values():
            assert model["chunks_embedded"] == model["vector_rows_upserted"] == 0
        assert counts["fts_ensure_attempts"] == counts["cluster_pass_attempts"] == 0
        native = measurement["native"]
        assert native["queries"] > 0
        assert native["rows_scanned"] <= 32 * native["queries"], (size, measurement)
        if baseline is None:
            baseline = measurement
        else:
            assert counts == baseline["counts"], (size, measurement)
            assert native == baseline["native"], (size, measurement)
        assert worker._projection(f) == before
        assert _samples(f, samples, size) == before_samples
        assert f.config.state_file.read_bytes() == poll
        assert f.tasks.get_task(original.id) == original
        assert ComponentLedger(f.config, f.store)._bound_table().count_rows() == size + 2
        selected_rows = 2 if deleted else 3
        assert f.store.content_store.db.open_table(f.config.content_table).count_rows() == (
            size + selected_rows
        )
        for model in f.config.model_profiles:
            assert f.store.get_vector_table(model)._get_or_create_table().count_rows() == (
                size + selected_rows
            )
        with capsys.disabled():
            print("POINT_SERVICE_NATIVE_WORK " + json.dumps({
                "size": size, "deleted": deleted, "setup_seconds": setup_seconds,
                "round": "http-superseded-and-completed-retry",
                "native_plan_replay": True, **measurement,
            }, sort_keys=True))
