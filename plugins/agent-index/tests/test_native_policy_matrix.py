"""Opt-in native policy invalidation is bounded to the returned item, not the corpus."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from test_native_delta_matrix import RoundFixture, SOURCE, assert_reference, measure_round

from agent_index.chunking import get_chunker
from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SourceDelta
from agent_index.store.source_layout import LAYOUT

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def test_clean_reference_rejects_missing_vectors_even_when_both_stores_omit_them():
    item = FileEntry("active.md", "Useful text.", "markdown", SOURCE)
    record = Chunk(item.content, item.path, "text", item.language, 1, 1, SOURCE)
    content = SimpleNamespace(
        get_chunk_ids_by_file=lambda *_a: [record.chunk_id],
        get_by_ids=lambda _ids: {record.chunk_id: record},
    )
    store = SimpleNamespace(
        content_store=content, model_ids=["a"],
        get_vector_table=lambda _model: SimpleNamespace(get_vectors=lambda *_a, **_k: {}),
    )
    with pytest.raises(AssertionError):
        assert_reference(store, store, [item], set())


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
def test_model_addition_and_chunk_policy_invalidation_at_three_corpus_sizes(
    tmp_path, monkeypatch, capsys, selected,
):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    active = FileEntry("active.md", "Useful original text.", "markdown", SOURCE)
    baselines: dict[str, dict[str, Any]] = {}
    measurements: dict[int, dict[str, Any]] = {}

    class RevisedChunker:
        def chunk(self, content, path, *, source):
            return [
                Chunk(content + " First policy segment.", path, "text", "markdown", 1, 1, source),
                Chunk("Second policy segment.", path, "text", "markdown", 2, 2, source),
            ]

    for size in (100, 10_000, 100_000):
        profile_a = ModelProfile("a", "fixture", dim=2, table_name="vectors_a")
        profile_b = ModelProfile("b", "fixture", dim=2, table_name="vectors_b")
        fixture = RoundFixture(IndexConfig(
            data_dir=tmp_path / str(size), model_profiles={"a": profile_a},
            cluster_enabled=False,
        ), selected, SourceDelta(upserts=[active], commit="initial"))
        monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
        monkeypatch.setattr("agent_index.chunking.get_chunker", get_chunker)
        fixture.run()
        unchanged = fixture.seed(size, monkeypatch=monkeypatch)
        before_ids = fixture.store().content_store.get_chunk_ids_by_file(SOURCE, active.path)
        assert len(before_ids) == 1
        fixture.config = replace(fixture.config, model_profiles={"a": profile_a, "b": profile_b})
        measurements[size] = {}
        for name in ("model-add", "model-revision", "chunk-policy", "policy-recheck", "replay"):
            if name == "model-revision":
                fixture.config = replace(fixture.config, model_profiles={
                    "a": profile_a, "b": replace(profile_b, revision="next"),
                })
            elif name == "chunk-policy":
                fixture.config = replace(fixture.config, chunker_revision="2")
                monkeypatch.setattr(
                    "agent_index.chunking.get_chunker", lambda _path: (RevisedChunker(), "markdown"),
                )
            elif name == "policy-recheck":
                fixture.config = replace(fixture.config, chunker_revision="3")
            fixture.delta = SourceDelta(upserts=[active], commit=name)
            measurement = measure_round(fixture.run, monkeypatch)
            measurements[size][name] = measurement
            counts = measurement["counts"]
            if size == 100:
                baselines[name] = measurement
            else:
                assert counts == baselines[name]["counts"], (size, name, measurement)
            assert counts["upserts_acquired"] == 1
            assert counts["checkpoint_lookup_keys"] == 2  # Preparation and writer recheck.
            assert counts["files_chunked"] == (0 if name == "replay" else 1)
            if name in {"model-add", "model-revision"}:
                assert counts["content_rows_upserted"] == 0
                assert counts["models"]["a"]["chunks_embedded"] == 0
                assert counts["models"]["a"]["vector_rows_upserted"] == 0
                assert counts["models"]["b"]["chunks_embedded"] == 1
                assert counts["models"]["b"]["vector_rows_upserted"] == 1
                assert counts["fts_ensure_attempts"] == 0
            elif name == "chunk-policy":
                assert counts["chunks_generated"] == 2
                assert counts["content_rows_upserted"] == 2
                assert counts["stale_chunk_ids_deleted"] == 1
                assert all(model["chunks_embedded"] == 2 for model in counts["models"].values())
            else:
                assert counts["content_rows_upserted"] == 0
                assert all(model["chunks_embedded"] == model["vector_rows_upserted"] == 0
                           for model in counts["models"].values())
                assert counts["fts_ensure_attempts"] == 0
            with capsys.disabled():
                print("NATIVE_POLICY_WORK " + json.dumps({
                    "size": size, "layout": "source-aware" if selected else "legacy",
                    "case": name, "native": measurement["native"], "io": measurement["io"],
                }, sort_keys=True))

            reference = RoundFixture(
                replace(fixture.config, data_dir=tmp_path / f"reference-{size}-{name}"),
                selected, SourceDelta(upserts=[active], commit="rebuilt"),
            )
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: reference)
            reference.run()
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
            store = fixture.store()
            assert_reference(store, reference.store(), [active], set())
            chunk_count = 1 if name in {"model-add", "model-revision"} else 2
            assert store.content_store.source_counts() == {SOURCE: size + chunk_count}
            sentinel_ids = [unchanged[0].chunk_id, unchanged[-1].chunk_id]
            assert set(store.content_store.get_by_ids(sentinel_ids)) == set(sentinel_ids)
            sentinel_vectors = store.get_vector_table("a").get_vectors(sentinel_ids, strict=True)
            assert set(sentinel_vectors) == set(sentinel_ids)
            for vector in sentinel_vectors.values():
                np.testing.assert_array_equal(vector, [1, 1])
            # Adding a model does not certify coverage of unreturned corpus rows.
            assert store.get_vector_table("b").get_vectors(sentinel_ids, strict=True) == {}
            if chunk_count == 2:
                assert store.content_store.get_by_ids(before_ids) == {}
                assert all(store.get_vector_table(model).get_vectors(before_ids, strict=True) == {}
                           for model in store.model_ids)

    for size, rounds in measurements.items():
        for name, measurement in rounds.items():
            assert measurement["native"]["rows_scanned"] < 64, (size, name, measurement)
            assert measurement["native"]["read_iops"] < 64, (size, name, measurement)
            assert measurement["io"]["rchar"] < (
                baselines[name]["io"]["rchar"] + 2 * 1024 * 1024
            ), (size, name, measurement)
            assert measurement["io"]["wchar"] < (
                baselines[name]["io"]["wchar"] + 512 * 1024
            ), (size, name, measurement)
