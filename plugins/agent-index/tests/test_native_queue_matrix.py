"""Opt-in native queue/worker/engine composition; no service or network is started."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from test_native_delta_matrix import (
    FixtureClient, RoundFixture, SOURCE, assert_reference, measure_round, no_lifecycle,
)

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine, worker
from agent_index.indexing.change_hints import ChangeHints, MetadataRevision, RefRange
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.state import IndexState
from agent_index.indexing.task_store import TaskStore
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.store.source_layout import LAYOUT

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def delivery(item: str, sequence: int) -> ChangeHints:
    return ChangeHints(
        source=SOURCE, canonical_family=SOURCE, account_id="account",
        item_ids=(item,), event_ids=(f"delivery-{sequence}",),
        ref_ranges=(RefRange("base", f"target-{sequence % 2}"),),
        metadata_revisions=(MetadataRevision(item, str(sequence % 2)),),
    )


def enqueue_burst(queue: TaskStore, items: tuple[str, ...]) -> tuple[str, ChangeHints]:
    values = [delivery(items[sequence % len(items)], sequence) for sequence in range(100)]
    expected = ChangeHints(
        source=SOURCE, canonical_family=SOURCE, account_id="account", item_ids=items,
        event_ids=tuple(f"delivery-{sequence}" for sequence in range(100)),
        ref_ranges=(RefRange("base", "target-0"), RefRange("base", "target-1")),
        metadata_revisions=tuple(value.metadata_revisions[0] for value in values),
    )
    task = queue.enqueue(SOURCE, change_hints=values[0])
    for value in values[1:]:
        assert queue.enqueue(SOURCE, change_hints=value).id == task.id
    assert queue.get_task(task.id).change_hints == expected
    return task.id, expected


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
@pytest.mark.parametrize("model_count", [1, 2])
def test_duplicate_and_active_round_bursts_at_three_corpus_sizes(
    tmp_path, monkeypatch, capsys, selected, model_count,
):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda _: None)
    monkeypatch.setattr("agent_index.indexing.priority.lower_current_process_priority", lambda _: None)
    monkeypatch.setattr("agent_index.config.machine_id", lambda: "test-host")
    profiles = {
        model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
        for model in ("a", "b")[:model_count]
    }
    initial = FileEntry("active.md", "Useful original text.", "markdown", SOURCE)
    removed = replace(initial, path="removed.md")
    changed = replace(initial, content="Useful replacement text.")
    second = replace(initial, path="second.md", content="Second useful item.")
    third = replace(initial, path="third.md", content="Third useful item.")
    fourth = replace(initial, path="fourth.md", content="Fourth useful item.")
    baselines: dict[str, dict[str, Any]] = {}
    measurements: dict[int, dict[str, Any]] = {}

    for size in (100, 10_000, 100_000):
        fixture = RoundFixture(IndexConfig(
            data_dir=tmp_path / str(size), model_profiles=profiles, cluster_enabled=False,
        ), selected, SourceDelta(upserts=[initial, removed], commit="poll"))
        monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
        fixture.run()
        unchanged = fixture.seed(size, monkeypatch=monkeypatch)
        removed_ids = fixture.store().content_store.get_chunk_ids_by_file(SOURCE, removed.path)
        assert len(removed_ids) == 1
        for model in profiles:
            assert fixture.store().get_vector_table(model).get_vectors(
                removed_ids, strict=True,
            ).keys() == set(removed_ids)
        queue_path = fixture.config.data_dir / "tasks.db"
        queue = TaskStore(queue_path)
        records = {item.path: item for item in (changed, second, third, fourth)}
        acquired = []
        callbacks = []

        def discover_hints(value, prior, *, cancel_check):
            cancel_check()
            acquired.append((value, prior))
            return SourceDelta(
                upserts=[records[item] for item in value.item_ids if item != removed.path],
                deletions=[DeletedFile(SOURCE, removed.path)] if removed.path in value.item_ids else [],
                commit=prior,
            )

        discover_hints.change_hints_versions = (1,)
        provider = SimpleNamespace(
            source_name=SOURCE, hint_scope=(SOURCE, "account"),
            recovery_identity=fixture.recovery_identity, discover_hints=discover_hints,
        )
        monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: provider)

        class BurstClient(FixtureClient):
            def embed_texts(self, texts):
                if callbacks:
                    callbacks.pop(0)()
                return super().embed_texts(texts)

        def run(**kwargs):
            return engine.run_reindex(
                **kwargs, config=fixture.config,
                source_specs=[engine.SourceSpec(SOURCE, type="sample")],
                embedding_runtime=EmbeddingRuntime(
                    lambda _: BurstClient(), no_lifecycle, no_lifecycle, manage_engines=False,
                ),
            )

        run.change_hints_versions = (1,)
        task_id, expected = enqueue_burst(queue, (changed.path, second.path, removed.path))
        # A fresh queue owner must recover the exact pre-claim union.
        queue = TaskStore(queue_path)
        assert queue.get_pending_count() == 1
        assert queue.get_task(task_id).change_hints == expected
        measurements[size] = {}
        trailing = []
        for name in ("duplicate", "replay", "active", "trailing", "trailing-replay"):
            if name == "active":
                records[changed.path] = replace(changed, content="A newer active item.")
                active_hints = delivery(changed.path, 200)
                task_id = queue.enqueue(SOURCE, change_hints=active_hints).id
                expected = active_hints

                def burst():
                    immutable = queue.get_task(task_id)
                    assert immutable.status == "processing"
                    trailing.append(enqueue_burst(queue, (third.path, fourth.path)))
                    assert trailing[0][0] != task_id
                    assert queue.get_task(task_id) == immutable
                    assert queue.get_pending_count() == 1

                callbacks.append(burst)
            elif name == "trailing":
                assert len(trailing) == 1 and not callbacks
                task_id, expected = trailing[0]
                queue = TaskStore(queue_path)
                assert queue.get_task(task_id).change_hints == expected
            elif name in {"replay", "trailing-replay"}:
                task_id = queue.enqueue(SOURCE, change_hints=expected).id

            claimed = queue.dequeue_next(accept_change_hints=True)
            assert claimed.id == task_id and claimed.change_hints == expected
            if name == "duplicate":
                queue = TaskStore(queue_path)
                assert queue.get_task(task_id).change_hints == expected
                assert queue.get_task(task_id).status == "processing"
            before_acquired = len(acquired)

            def execute():
                assert worker.run_worker(task_id, config=fixture.config, indexer=run) == 0
                completed = queue.get_task(task_id)
                assert completed.status == "complete", completed.error
                assert completed.change_hints == expected
                return completed.result_stats

            measurement = measure_round(execute, monkeypatch)
            measurements[size][name] = measurement
            assert acquired[before_acquired:] == [(expected, "poll")]
            assert len(expected.item_ids) == len(set(expected.item_ids))
            counts = measurement["counts"]
            acquired_count = 1 if name == "active" else 2
            assert counts["upserts_acquired"] == acquired_count
            assert counts["files_chunked"] == (0 if "replay" in name else acquired_count)
            assert counts["content_rows_upserted"] == (0 if "replay" in name else acquired_count)
            if "replay" in name:
                assert all(value["chunks_embedded"] == value["vector_rows_upserted"] == 0
                           for value in counts["models"].values())
                assert counts["fts_ensure_attempts"] == 0
            else:
                assert set(counts["models"]) == set(profiles)
                assert all(value["chunks_embedded"] == value["vector_rows_upserted"] == acquired_count
                           for value in counts["models"].values())
            assert IndexState.load(fixture.config.state_file).sources[SOURCE].last_commit == "poll"
            assert queue.get_pending_count() == (1 if name == "active" else 0)
            if size == 100:
                baselines[name] = measurement
            else:
                assert counts == baselines[name]["counts"], (size, name, measurement)
            with capsys.disabled():
                print("NATIVE_QUEUE_WORK " + json.dumps({
                    "size": size, "layout": "source-aware" if selected else "legacy",
                    "models": model_count, "case": name,
                    "native": measurement["native"], "io": measurement["io"],
                }, sort_keys=True))

            entries = [records[changed.path], second]
            if name in {"trailing", "trailing-replay"}:
                entries.extend([third, fourth])
            reference = RoundFixture(replace(
                fixture.config, data_dir=tmp_path / f"reference-{size}-{name}",
            ), selected, SourceDelta(upserts=entries, commit="rebuilt"))
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: reference)
            reference.run()
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: provider)
            store = fixture.store()
            assert_reference(store, reference.store(), entries, {removed.path})
            assert store.content_store.source_counts() == {SOURCE: size + len(entries)}
            sentinel_ids = {unchanged[0].chunk_id, unchanged[-1].chunk_id}
            assert set(store.content_store.get_by_ids(list(sentinel_ids))) == sentinel_ids
            for model in profiles:
                table = store.get_vector_table(model)
                assert table.get_vectors(removed_ids, strict=True) == {}
                vectors = table.get_vectors(list(sentinel_ids), strict=True)
                assert vectors.keys() == sentinel_ids
                for vector in vectors.values():
                    np.testing.assert_array_equal(vector, [1, 1])

    for size, rounds in measurements.items():
        for name, measurement in rounds.items():
            assert measurement["native"]["rows_scanned"] < 64, (size, name, measurement)
            assert measurement["native"]["read_iops"] < 64, (size, name, measurement)
            assert measurement["io"]["rchar"] < (
                baselines[name]["io"]["rchar"]
                + baselines[name]["counts"]["identities_scanned"] * 2 * 1024 * 1024
            ), (size, name, measurement)
            assert measurement["io"]["wchar"] < (
                baselines[name]["io"]["wchar"] + 512 * 1024
            ), (size, name, measurement)
