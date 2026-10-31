"""Opt-in composed rounds: logical counts are not native storage-work evidence."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.path_index import PathIndex
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]
SOURCE = "sample:repo"


def io_counts() -> dict[str, int]:
    """Linux process reads include cached I/O, unlike read_bytes alone."""
    return {
        key: int(value)
        for line in Path("/proc/self/io").read_text().splitlines()
        for key, value in [line.split(":")]
    }


@dataclass
class NativeWork:
    plans: list[str] = field(default_factory=list)
    queries: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "queries": self.queries,
            "rows_scanned": sum(
                int(value) for plan in self.plans
                for value in re.findall(r"\brows_scanned=(\d+)", plan)
            ),
            "read_iops": sum(
                int(value) for plan in self.plans
                for value in re.findall(r"\biops=(\d+)", plan)
            ),
        }


@dataclass
class FixtureClient:
    offset: int = 0

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return np.array([[len(text) + self.offset, 1] for text in texts], dtype=np.float32)

    def close(self) -> None:
        pass


def no_lifecycle(*_args: object) -> None:
    raise AssertionError("an injected runtime attempted engine lifecycle")


@dataclass
class RoundFixture:
    config: IndexConfig
    selected: bool
    delta: SourceDelta = field(default_factory=SourceDelta)
    acquired: list[str | None] = field(default_factory=list)
    source_name: str = SOURCE
    recovery_identity: tuple[str, ...] = ("fixture", "account")

    def discover_delta(self, last_commit: str | None, **_kwargs: object) -> SourceDelta:
        self.acquired.append(last_commit)
        return self.delta

    def run(self) -> dict[str, Any]:
        result = engine.run_reindex(
            source=SOURCE, config=self.config,
            source_specs=[engine.SourceSpec(SOURCE, type="sample")],
            embedding_runtime=EmbeddingRuntime(
                lambda profile: FixtureClient(100 if profile.revision == "next" else 0),
                no_lifecycle, no_lifecycle,
                manage_engines=False,
            ),
        )
        assert "sources_failed" not in result, result
        json.dumps(result)
        return result

    def store(self) -> MultiModelStore:
        store = (
            initialize_source_store(self.config) if self.selected else
            MultiModelStore(self.config.lance_dir)
        )
        for profile in self.config.model_profiles.values():
            store.register_model(profile)
        return store

    def seed(
        self, size: int, *, via_engine: bool = False,
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[Chunk]:
        """Setup is deliberately unmeasured, using the real mutation APIs."""
        if via_engine:
            chunks = [
                Chunk("Unchanged fixture content.", "bulk.md", "text", "markdown", i, i, SOURCE)
                for i in range(size)
            ]

            class CorpusChunker:
                def chunk(self, *_args: Any, **_kwargs: Any) -> list[Chunk]:
                    return chunks

            config = self.config
            self.config = replace(config, stream_batch_size=size)
            self.delta = SourceDelta(upserts=[
                FileEntry("bulk.md", "Synthetic corpus.", "markdown", SOURCE),
            ], commit="seeded")
            with monkeypatch.context() as patch:
                patch.setattr("agent_index.chunking.get_chunker", lambda _path: (CorpusChunker(), "markdown"))
                self.run()
            self.config = config
            return chunks
        chunks = [
            Chunk("Unchanged fixture content.", f"old-{i}.md", "text", "markdown", 1, 1, SOURCE)
            for i in range(size)
        ]
        store = self.store()
        store.upsert_content(chunks)
        for model in self.config.model_profiles:
            store.upsert_vectors(model, chunks, np.ones((size, 2), dtype=np.float32))
        paths = PathIndex(self.config.data_dir / "path_index.db")
        paths.mark_indexed_batch([
            (SOURCE, chunk.file_path, file_fingerprint(
                FileEntry(chunk.file_path, chunk.content, chunk.language, SOURCE),
                self.config.model_profiles,
            ), 1)
            for chunk in chunks
        ])
        from lancedb.index import BTree

        from agent_index.store.scalar_index import prepare_index

        owners = [store.content_store, *(
            store.get_vector_table(model) for model in self.config.model_profiles
        )]
        for owner in owners:
            columns = ("chunk_id", "file_path") if owner is store.content_store else ("chunk_id",)
            for column in columns:
                if self.selected:
                    prepare_index(owner, column=column, max_rows=size + 1)
                else:
                    owner._get_or_create_table().create_index(
                        column, config=BTree(), name=f"{column}_idx",
                    )
        if self.selected:
            store.ensure_fts_index()
        return chunks


def assert_reference(
    actual: MultiModelStore, reference: MultiModelStore, entries: list[FileEntry],
    deleted_paths: set[str],
) -> None:
    """Compare the changed projection to a clean engine rebuild, outside measurement."""
    for path in {item.path for item in entries} | deleted_paths:
        actual_ids = actual.content_store.get_chunk_ids_by_file(SOURCE, path)
        expected_ids = reference.content_store.get_chunk_ids_by_file(SOURCE, path)
        assert set(actual_ids) == set(expected_ids)
        actual_records = actual.content_store.get_by_ids(actual_ids)
        expected_records = reference.content_store.get_by_ids(expected_ids)
        for cid in expected_ids:
            left, right = actual_records[cid], expected_records[cid]
            assert (left.content, left.metadata, left.file_path) == (
                right.content, right.metadata, right.file_path,
            )
        for model in actual.model_ids:
            left_vectors = actual.get_vector_table(model).get_vectors(actual_ids, strict=True)
            right_vectors = reference.get_vector_table(model).get_vectors(expected_ids, strict=True)
            assert left_vectors.keys() == right_vectors.keys() == set(expected_ids)
            for cid in right_vectors:
                np.testing.assert_array_equal(left_vectors[cid], right_vectors[cid])


def measure_round(
    run: Callable[[], dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    from lancedb.query import LanceEmptyQueryBuilder

    work = NativeWork()
    batches = LanceEmptyQueryBuilder.to_batches

    def measured_batches(query: Any, *args: Any, **kwargs: Any) -> Any:
        work.queries += 1
        # Diagnostic replay conservatively contributes to measured process I/O.
        work.plans.append(query.analyze_plan())
        return batches(query, *args, **kwargs)

    before_io = io_counts()
    with monkeypatch.context() as patch:
        patch.setattr(LanceEmptyQueryBuilder, "to_batches", measured_batches)
        result = run()
    after_io = io_counts()
    counts = result["work_counts"]
    counts.pop("source_mutations")  # Generation/epoch values are intentionally unique.
    return {
        "counts": counts, "native": work.summary(),
        "io": {key: after_io[key] - before_io[key] for key in before_io},
    }


@pytest.mark.skipif(sys.platform != "linux", reason="process I/O counter qualification is Linux-only")
@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
@pytest.mark.parametrize("model_count", [1, 2])
@pytest.mark.parametrize("seed_via_engine", [False, True], ids=["prepared", "ordinary"])
def test_prepared_native_finite_delta_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool, model_count: int,
    capsys: pytest.CaptureFixture[str], seed_via_engine: bool,
) -> None:
    if seed_via_engine and not selected:
        pytest.skip("legacy ingestion does not opt into source-owned scalar upkeep")
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    profiles = {
        model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
        for model in ("a", "b")[:model_count]
    }
    active = FileEntry("active.md", "Useful original text.", "markdown", SOURCE)
    changed = replace(active, content="Useful replacement text.")
    metadata = replace(changed, metadata={"state": "closed"})
    renamed = replace(metadata, path="moved.md")
    baselines: dict[str, dict[str, Any]] = {}
    all_measurements: dict[int, dict[str, Any]] = {}
    for size in (100, 10_000, 100_000):
        fixture = RoundFixture(IndexConfig(
            data_dir=tmp_path / str(size), model_profiles=profiles, cluster_enabled=False,
        ), selected)
        monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
        fixture.delta = SourceDelta(upserts=[active], commit="initial")
        fixture.run()
        unchanged = fixture.seed(size, via_engine=seed_via_engine, monkeypatch=monkeypatch)
        cases = [
            ("noop", SourceDelta(commit="initial"), [active], set()),
            ("edit", SourceDelta(upserts=[changed], commit="edit"), [changed], set()),
            ("replay", SourceDelta(upserts=[changed], commit="edit"), [changed], set()),
            ("metadata", SourceDelta(upserts=[metadata], commit="metadata"), [metadata], set()),
            ("rename", SourceDelta(
                upserts=[renamed], deletions=[DeletedFile(SOURCE, active.path)], commit="rename",
            ), [renamed], {active.path}),
            ("model-gap", SourceDelta(upserts=[renamed], commit="model-gap"),
             [renamed], {active.path}),
            ("delete", SourceDelta(
                deletions=[DeletedFile(SOURCE, renamed.path)], commit="delete",
            ), [], {active.path, renamed.path}),
        ]
        measurements: dict[str, Any] = {}
        for name, delta, expected, deleted in cases:
            if name == "model-gap":
                model = next(reversed(profiles))
                fixture.config = replace(fixture.config, model_profiles={
                    key: replace(profile, revision="next") if key == model else profile
                    for key, profile in profiles.items()
                })
            fixture.delta = delta
            measurement = measure_round(fixture.run, monkeypatch)
            counts = measurement["counts"]
            measurements[name] = measurement
            with capsys.disabled():
                print("NATIVE_DELTA_WORK " + json.dumps({
                    "size": size, "layout": "source-aware" if selected else "legacy",
                    "models": model_count, "setup": "ordinary" if seed_via_engine else "prepared",
                    "case": name,
                    "native": measurement["native"], "io": measurement["io"],
                }, sort_keys=True))
            if size == 100:
                baselines[name] = measurement
            else:
                assert counts == baselines[name]["counts"], (size, name, measurement)

            reference = RoundFixture(replace(
                fixture.config, data_dir=tmp_path / f"reference-{size}-{name}",
            ), selected, SourceDelta(upserts=expected, commit="rebuilt"))
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: reference)
            reference.run()
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
            store = fixture.store()
            assert_reference(store, reference.store(), expected, deleted)
            assert store.content_store.source_counts() == {SOURCE: size + len(expected)}
            sentinel_ids = [unchanged[0].chunk_id, unchanged[-1].chunk_id]
            assert set(store.content_store.get_by_ids(sentinel_ids)) == set(sentinel_ids)
            for model in profiles:
                vectors = store.get_vector_table(model).get_vectors(sentinel_ids, strict=True)
                for cid in sentinel_ids:
                    np.testing.assert_array_equal(vectors[cid], [
                        len(unchanged[0].content) if seed_via_engine else 1, 1,
                    ])
        all_measurements[size] = measurements

    # Check after collecting every size/case so one native amplification cannot
    # hide the rest of the matrix. Limits allow metadata/fragment overhead.
    for size, measurements in all_measurements.items():
        for name, measurement in measurements.items():
            assert measurement["native"]["rows_scanned"] < 64, (size, name, measurement)
            assert measurement["native"]["read_iops"] < 64, (size, name, measurement)
            assert measurement["io"]["rchar"] < (
                baselines[name]["io"]["rchar"] + 2 * 1024 * 1024
            ), (size, name, measurement)
            assert measurement["io"]["wchar"] < (
                baselines[name]["io"]["wchar"] + 512 * 1024
            ), (size, name, measurement)
