"""Opt-in native model addition reuses proven pairs through bounded progress retry."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from typing import Any

import numpy as np
import pytest
from test_embedding_backfill import context as _context
from test_embedding_pair_reuse import Client, chunk, profile
from test_native_delta_matrix import measure_round

from agent_index.indexing import embedding_backfill
from agent_index.indexing.embedding_backfill import backfill_embedding_slice
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.work_counts import measure_reindex
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.source_layout import initialize_source_store

context = _context
pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
def test_new_model_bounded_slices_retry_only_proven_pairs_at_three_corpus_sizes(
    context, tmp_path, monkeypatch, capsys,
):
    baselines: dict[str, dict[str, Any]] = {}
    all_measurements: dict[int, dict[str, Any]] = {}
    for size in (100, 10_000, 100_000):
        config = replace(
            context.config, data_dir=tmp_path / str(size),
            model_profiles=dict(context.config.model_profiles),
        )
        store = initialize_source_store(config)
        store.register_model(profile())
        paths = PathIndex(config.data_dir / "path_index.db")
        # Current-model coverage is proven by the real backfill path, not assumed
        # from a pre-populated vector table.
        store.upsert_content(context.items)
        prepare_chunk_id_index(store.content_store, max_rows=5)
        state_bytes = context.config.state_file.read_bytes()
        config.state_file.write_bytes(state_bytes)
        original = Client()

        def after(table, _selected):
            prepare_chunk_id_index(table, max_rows=20)

        def run(clients, *, restart=False):
            return backfill_embedding_slice(
                context.spec, context.connector, config=config, store=store, paths=paths,
                clients=clients, row_budget=3, after_store=after, restart=restart,
            )

        assert not run({"a": original}).complete
        assert run({"a": original}).complete
        current_calls = len(original.calls)
        store.upsert_content([
            chunk(f"unchanged unrelated {i}", path=f"unrelated-{i}.md", source="other:repo")
            for i in range(size - 5)
        ])
        prepare_chunk_id_index(store.content_store, max_rows=size)
        snapshot = store.content_store.capture_snapshot()
        target_ids = [item.chunk_id for item in context.items]
        original_vectors = store.get_vector_table("a").get_vectors(target_ids, strict=True)
        config.model_profiles["b"] = profile("b")
        with store._source_domain.write(store.content_store.db):
            store.register_model(config.model_profiles["b"])._get_or_create_table()
        new = Client()
        clients = {"a": original, "b": new}
        require = embedding_backfill._require_current

        def interrupt_after_store(*args):
            if new.calls and not store._source_domain.writing:
                raise RuntimeError("progress publication interrupted")
            return require(*args)

        with monkeypatch.context() as patch:
            patch.setattr(embedding_backfill, "_require_current", interrupt_after_store)
            with pytest.raises(RuntimeError, match="publication interrupted"):
                run(clients, restart=True)
        assert len(new.calls) == 1 and len(new.calls[0]) == 3
        assert len(original.calls) == current_calls
        assert store._source_domain.receipt()["state"] == "clean"
        store = initialize_source_store(config)
        for configured in config.model_profiles.values():
            store.register_model(configured)
        paths = PathIndex(config.data_dir / "path_index.db")
        measurements: dict[str, Any] = {}
        for name in ("covered-replay", "next-slice"):
            progress = []

            @measure_reindex
            def measured():
                result = asdict(run(clients))
                progress.append(result)
                return {"progress": result}

            measurements[name] = measure_round(measured, monkeypatch)
            counts = measurements[name]["counts"]
            if size == 100:
                baselines[name] = measurements[name]
            else:
                assert counts == baselines[name]["counts"], (size, name, measurements[name])
            assert counts["models"]["a"]["chunks_embedded"] == 0
            assert counts["models"]["a"]["vector_rows_upserted"] == 0
            expected = 0 if name == "covered-replay" else 2
            assert progress == [{
                "offset": 3 if name == "covered-replay" else 6,
                "total_rows": size, "rows_read": 3,
                "scoped_chunks": 3 if name == "covered-replay" else 2,
                "vector_rows_upserted": expected, "complete": False,
            }]
            assert counts["models"]["b"]["chunks_embedded"] == expected
            assert counts["models"]["b"]["vector_rows_upserted"] == expected
            assert counts["content_rows_upserted"] == counts["checkpoint_rows_written"] == 0
            with capsys.disabled():
                print("NATIVE_BACKFILL_WORK " + json.dumps({
                    "size": size, "case": name, "native": measurements[name]["native"],
                    "io": measurements[name]["io"],
                }, sort_keys=True))
        assert [len(call) for call in new.calls] == [3, 2]
        assert len(original.calls) == current_calls
        retained = store.get_vector_table("a").get_vectors(target_ids, strict=True)
        assert retained.keys() == original_vectors.keys()
        for cid in target_ids:
            np.testing.assert_array_equal(retained[cid], original_vectors[cid])
        assert set(store.get_vector_table("b").get_vectors(target_ids, strict=True)) == set(target_ids)
        unrelated = chunk("unchanged unrelated 0", path="unrelated-0.md", source="other:repo")
        assert store.get_vector_table("b").get_vectors([unrelated.chunk_id], strict=True) == {}
        assert store.content_store.capture_snapshot() == snapshot
        assert config.state_file.read_bytes() == state_bytes
        assert paths.count() == 0
        all_measurements[size] = measurements

    for size, rounds in all_measurements.items():
        for name, measurement in rounds.items():
            assert measurement["native"]["rows_scanned"] < 64, (size, name, measurement)
            assert measurement["native"]["read_iops"] < 64, (size, name, measurement)
            assert measurement["io"]["rchar"] < (
                baselines[name]["io"]["rchar"] + 2 * 1024 * 1024
            ), (size, name, measurement)
            assert measurement["io"]["wchar"] < (
                baselines[name]["io"]["wchar"] + 512 * 1024
            ), (size, name, measurement)
