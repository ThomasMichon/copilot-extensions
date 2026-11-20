"""Selected pair composition uses native bindings before publishing coverage."""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from time import perf_counter, process_time
from typing import Any, cast

import numpy as np
import pytest
from test_point_reconciliation import SCOPE, domain, selected as _selected, snapshot

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig
from agent_index.indexing.embedding_batch import embed_and_store_pairs
from agent_index.indexing.embedding_coverage import EmbeddingCoverage
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.work_counts import measure_reindex
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired, inspect_index, prepare_index
from agent_index.store.source_layout import SourceLayoutError
from agent_index.store.vector_table import VectorTable

selected = _selected


class Client:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> np.ndarray[Any, Any]:
        self.calls.append(texts)
        return np.asarray([[len(text)] * self.dim for text in texts], dtype=np.float32)


def prepare(table: VectorTable, rows: int) -> None:
    prepare_index(table, column="chunk_id", max_rows=rows, point_scope=SCOPE)


def receipts(coverage: EmbeddingCoverage) -> list[tuple[Any, ...]]:
    with coverage.paths._connect(read_only=True) as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT * FROM embedding_coverage ORDER BY table_name, chunk_id",
        )]


def test_selected_pairs_route_reuse_and_reopen_without_trusting_facade_caches(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, original, chunks = selected
    owner = domain(original)
    models = {
        key: replace(profile, content_types=frozenset({"text" if key == "first" else "function"}))
        for key, profile in config.model_profiles.items()
    }
    items = [
        chunks[0], replace(chunks[1], chunk_type="function"),
        replace(chunks[-1], chunk_type="custom"),
    ]
    with owner._write(original.content_store.db, point_scope=SCOPE):
        original.upsert_point_content(items, point_scope=SCOPE)
        prepare_ingestion_indices(original, max_rows=3, point_scope=SCOPE)
    before_content = owner.path / "canonical.lance"
    content_bytes = snapshot(before_content)
    clients = {key: Client(profile.dim) for key, profile in models.items()}
    paths = tmp_path / "paths.db"
    ready: list[str] = []
    samples: list[dict[str, Any]] = []
    for run in range(2):
        store = MultiModelStore(owner.path, content_table="canonical", point_scope=SCOPE)
        db = store.content_store.db
        store.content_store._table = db.open_table("vectors_second")
        coverage = EmbeddingCoverage(PathIndex(paths))
        with owner._write(store.content_store.db, point_scope=SCOPE):
            before = snapshot(owner.path) if run else {}
            native = {name: db.open_table(name) for name in db.list_tables().tables}
            footprint = {
                name: {
                    "fragments": len(list(table.to_lance().get_fragments())),
                    "segments": inspect_index(
                        table, column="point_key" if name == "component_ordering_evidence" else "chunk_id",
                    )[0].segments,
                } for name, table in native.items()
            }
            opens: Counter[str] = Counter()
            open_table = type(db).open_table

            def observe(connection: Any, name: str, *args: Any, **kwargs: Any) -> Any:
                opens[name] += 1
                return open_table(connection, name, *args, **kwargs)

            @measure_reindex
            def write() -> dict[str, object]:
                return {"written": embed_and_store_pairs(
                    items, store, cast(Any, clients), models, coverage=coverage, bounded=True,
                    point_scope=SCOPE, after_store=prepare, before_model_embed=ready.append,
                    stream_batch_size=2,
                )}
            with monkeypatch.context() as patch:
                patch.setattr(type(db), "open_table", observe)
                started, cpu_started = perf_counter(), process_time()
                result = write()
                elapsed, cpu = perf_counter() - started, process_time() - cpu_started
            samples.append({
                "pair_receipts": "covered-reopen" if run else "cold",
                "wall_seconds": elapsed, "process_cpu_seconds": cpu,
                "opens_by_table": dict(opens), "native_before": footprint,
                "work_counts": result["work_counts"],
            })
            counts = cast(dict[str, Any], result["work_counts"])
            written = result["written"]
            if run:
                assert written == 0
                assert snapshot(owner.path) == before
                assert sum(model["embed_calls"] for model in counts["models"].values()) == 0
            else:
                assert written == 4
                assert sum(model["chunks_submitted"] for model in counts["models"].values()) == 4
                assert sum(model["vector_rows_upserted"] for model in counts["models"].values()) == 4
            assert counts["content_rows_upserted"] == 0
            cached: Any = store.content_store._table
            assert cached.name == "vectors_second"
            assert store.model_ids == []
        assert snapshot(before_content) == content_bytes
    assert clients["first"].calls == [[items[0].content], [items[2].content]]
    assert clients["second"].calls == [[items[1].content], [items[2].content]]
    assert ready == ["first", "second", "first", "second"]
    assert len(receipts(coverage)) == 4
    models["second"] = replace(models["second"], revision="next")
    with owner._write(store.content_store.db, point_scope=SCOPE):
        assert embed_and_store_pairs(
            items, store, cast(Any, clients), models, coverage=coverage, bounded=True,
            point_scope=SCOPE, after_store=prepare,
        ) == 2
    assert len(clients["first"].calls) == 2
    assert clients["second"].calls[-1] == [items[1].content, items[2].content]
    assert snapshot(before_content) == content_bytes
    print("selected_pair_measurements=" + json.dumps({
        "platform": sys.platform, "models": 2, "chunks": 3, "batch_size": 2,
        "cache_boundary": "receipt-cold first pair write versus covered fresh-facade reopen; OS cache uncontrolled",
        "samples": samples,
    }, sort_keys=True))


@pytest.mark.parametrize("obstruction", [
    "no-writer", "scope", "family", "profile-table", "profile-id", "profile-missing",
    "stale-input", "content-index", "vector-index", "connection",
])
def test_selected_pairs_refuse_before_inference_or_receipts(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, obstruction: str,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    models = dict(config.model_profiles)
    items = chunks[:1]
    clients = {key: Client(profile.dim) for key, profile in models.items()}
    coverage = EmbeddingCoverage(PathIndex(tmp_path / "paths.db"))
    scope = SCOPE
    if obstruction == "no-writer":
        before = snapshot(owner.path)
        with pytest.raises(SourceLayoutError, match="owning source writer"):
            embed_and_store_pairs(
                [], store, cast(Any, clients), models, coverage=coverage, bounded=True,
                point_scope=SCOPE, after_store=prepare,
            )
        assert snapshot(owner.path) == before
        assert receipts(coverage) == []
        return
    with owner._write(store.content_store.db, point_scope=SCOPE):
        if obstruction == "scope":
            scope = (SCOPE[0], SCOPE[1], "another-account", SCOPE[3])
        elif obstruction == "family":
            items = [replace(items[0], source="sample:other")]
        elif obstruction == "profile-table":
            models["first"] = replace(models["first"], table_name="vectors_second")
        elif obstruction == "profile-id":
            models["first"] = replace(models["first"], model_id="alias")
        elif obstruction == "profile-missing":
            models.pop("second")
            clients.pop("second")
        elif obstruction == "stale-input":
            items = [replace(items[0], content="stale")]
        elif obstruction in ("content-index", "vector-index"):
            owner.effect("fixture-index-drift")
            name = "canonical" if obstruction == "content-index" else "vectors_second"
            store.content_store.db.open_table(name).drop_index("chunk_id_idx")
        elif obstruction == "connection":
            monkeypatch.setattr(store, "_db_path", owner.path / "foreign")
        before = snapshot(owner.path)
        with pytest.raises((SourceLayoutError, ScalarIndexPreparationRequired)):
            embed_and_store_pairs(
                items, store, cast(Any, clients), models, coverage=coverage, bounded=True,
                point_scope=scope, after_store=prepare,
            )
        assert snapshot(owner.path) == before
        assert all(client.calls == [] for client in clients.values())
        assert receipts(coverage) == []


def test_selected_prepared_vectors_skip_inference_and_failed_publication_latches_writer(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    coverage = EmbeddingCoverage(PathIndex(tmp_path / "paths.db"))
    clients = {key: Client(profile.dim) for key, profile in config.model_profiles.items()}
    events: list[str] = []

    def prepared(model: str, items: list[Chunk]) -> np.ndarray[Any, Any]:
        events.append(model)
        return np.ones((len(items), config.model_profiles[model].dim), dtype=np.float32) * 7

    def interrupt(table: VectorTable, rows: int) -> None:
        prepare(table, rows)
        raise RuntimeError("interrupted before coverage")

    with pytest.raises(SourceLayoutError, match="failed nested effect"):
        with owner._write(store.content_store.db, point_scope=SCOPE):
            with pytest.raises(RuntimeError, match="interrupted before coverage"):
                embed_and_store_pairs(
                    chunks[:1], store, cast(Any, clients), config.model_profiles,
                    coverage=coverage, bounded=True, point_scope=SCOPE,
                    prepared_vectors=prepared, after_store=interrupt,
                    before_embed=lambda: pytest.fail("prepared vectors must skip readiness"),
                )
            assert receipts(coverage) == []
    assert events == ["first"]
    assert all(client.calls == [] for client in clients.values())
    assert owner.receipt()["state"] == "dirty"
    with pytest.raises(SourceLayoutError, match="incomplete or changed"):
        with owner.read(store.content_store.db, point_scope=SCOPE):
            pass


def test_selected_prepared_pairs_publish_then_reuse_without_inference_counts(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    coverage = EmbeddingCoverage(PathIndex(tmp_path / "paths.db"))
    clients = {key: Client(profile.dim) for key, profile in config.model_profiles.items()}
    cached: list[str] = []

    def prepared(model: str, items: list[Chunk]) -> np.ndarray[Any, Any]:
        cached.append(model)
        return np.full((len(items), config.model_profiles[model].dim), 7, dtype=np.float32)

    @measure_reindex
    def write() -> dict[str, object]:
        return {"written": embed_and_store_pairs(
            chunks[:1], store, cast(Any, clients), config.model_profiles,
            coverage=coverage, bounded=True, point_scope=SCOPE,
            prepared_vectors=prepared, after_store=prepare,
            before_model_embed=lambda model: pytest.fail("prepared pairs must skip readiness"),
        )}

    for expected in (2, 0):
        with owner._write(store.content_store.db, point_scope=SCOPE):
            result = write()
            assert result["written"] == expected
            counts = cast(dict[str, Any], result["work_counts"])
            assert counts["content_rows_upserted"] == 0
            assert sum(model["vector_rows_upserted"] for model in counts["models"].values()) == expected
            assert sum(model["embed_calls"] for model in counts["models"].values()) == 0
            assert sum(model["chunks_embedded"] for model in counts["models"].values()) == 0
    assert cached == ["first", "second"]
    assert all(client.calls == [] for client in clients.values())
    assert len(receipts(coverage)) == 2


@pytest.mark.parametrize("failure", ["later-batch", "later-model-selection"])
def test_selected_later_selection_failure_latches_earlier_effects_even_when_caught(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    coverage = EmbeddingCoverage(PathIndex(tmp_path / "paths.db"))
    clients = {key: Client(profile.dim) for key, profile in config.model_profiles.items()}
    items = [chunks[0], replace(chunks[1], content="not stored input")] if failure == "later-batch" else chunks[:1]
    missing = coverage.missing

    def select(table: VectorTable, *args: Any, **kwargs: Any) -> list[Chunk]:
        if table.table_name == "vectors_second":
            raise SourceLayoutError("later selection refused")
        return missing(table, *args, **kwargs)

    if failure == "later-model-selection":
        monkeypatch.setattr(coverage, "missing", select)
    with pytest.raises(SourceLayoutError, match="failed nested effect"):
        with owner._write(store.content_store.db, point_scope=SCOPE):
            with pytest.raises(SourceLayoutError, match="canonical embedding input|canonical provenance|later selection"):
                embed_and_store_pairs(
                    items, store, cast(Any, clients), config.model_profiles,
                    coverage=coverage, bounded=True, point_scope=SCOPE,
                    stream_batch_size=1, after_store=prepare,
                )
            assert len(receipts(coverage)) == (2 if failure == "later-batch" else 1)
    assert clients["first"].calls == [[chunks[0].content]]
    assert clients["second"].calls == ([[chunks[0].content]] if failure == "later-batch" else [])
    assert owner.receipt()["state"] == "dirty"
    with pytest.raises(SourceLayoutError, match="incomplete or changed"):
        with owner.read(store.content_store.db, point_scope=SCOPE):
            pass


@pytest.mark.parametrize("obstruction", ["unprepared", "mismatch", "profile", "family", "stale"])
def test_selected_coverage_requires_actual_prepared_current_vectors_before_sqlite(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
    obstruction: str,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    coverage = EmbeddingCoverage(PathIndex(tmp_path / "paths.db"))
    model = config.model_profiles["first"]
    table = store.get_vector_table("first")
    items = chunks[:1]
    values = np.ones((1, model.dim), dtype=np.float32)
    with owner._write(store.content_store.db, point_scope=SCOPE):
        if obstruction == "unprepared":
            store.upsert_point_vectors("first", items, values * 7, point_scope=SCOPE)
        elif obstruction == "mismatch":
            values *= 7
        elif obstruction == "profile":
            model = replace(model, table_name="vectors_second")
        elif obstruction == "family":
            items = [replace(items[0], source="sample:other")]
        else:
            items = [replace(items[0], metadata={"title": "stale"})]
        before = snapshot(owner.path)
        with pytest.raises((SourceLayoutError, ScalarIndexPreparationRequired, RuntimeError)):
            coverage.record(table, items, model, values, bounded=True, point_scope=SCOPE)
        assert snapshot(owner.path) == before
        assert receipts(coverage) == []


def test_selected_coverage_ignores_poisoned_handle_but_rejects_unowned_publication(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], tmp_path: Path,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    coverage = EmbeddingCoverage(PathIndex(tmp_path / "paths.db"))
    table = store.get_vector_table("first")
    model = config.model_profiles["first"]
    values = np.ones((1, model.dim), dtype=np.float32)
    with pytest.raises(SourceLayoutError, match="owning source writer"):
        coverage.record(table, [], model, np.ones((0, model.dim)), point_scope=SCOPE)
    table._table = store.content_store.db.open_table("vectors_second")
    with owner._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(owner.path)
        coverage.record(table, chunks[:1], model, values, point_scope=SCOPE)
        assert snapshot(owner.path) == before
        assert len(receipts(coverage)) == 1
        cached: Any = table._table
        assert cached.name == "vectors_second"
