"""Explicit manual/scheduled large-corpus timing and memory proof, not fast PR CI."""

import json
import time

import numpy as np
import pytest
from test_source_aware_store import cold as cold

from agent_index.search.engine import SearchEngine
from agent_index.store.source_layout import initialize_source_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
              pytest.mark.effect("process")]


@pytest.mark.timeout(180)
def test_native_140000_source_layout_queries_and_latency(cold, monkeypatch):
    """Benchmark-only bulk rows are separate from the real queued-ingestion proof."""
    import hashlib
    import tracemalloc

    import pyarrow as pa

    config, store = cold
    vt = store.get_vector_table("small")
    content = store.content_store._get_or_create_table()
    vectors = vt._get_or_create_table()
    total = 140_000
    winners = {100_001, 120_000, 139_990, 139_999}

    def batches(schema, vector):
        for start in range(0, total + 32, 4096):
            rows = []
            for i in range(start, min(total + 32, start + 4096)):
                source = f"git:family-{i % 18}" if i < total else "git:family-0lookalike"
                if i < total and i % 37 == 0:
                    source += ":commits"
                language = "python" if i % 2 else "markdown"
                row = {"chunk_id": f"{i:064x}", "source": source, "language": language,
                       "indexed_at": 1.0}
                if vector:
                    row["vector"] = [1.0, 0.0] if i in winners or i >= total else [0.0, 1.0]
                else:
                    text = f"needle {i:06d}"
                    row.update(
                        file_path=f"docs/{i}.md", chunk_type="text", content=text,
                        content_hash=hashlib.sha256(text.encode()).hexdigest(),
                        line_start=1, line_end=1, metadata="",
                    )
                rows.append(row)
            yield pa.RecordBatch.from_pylist(rows, schema=schema)

    with store._source_domain.write(store.content_store.db):
        content.add(pa.RecordBatchReader.from_batches(
            content.schema, batches(content.schema, False),
        ))
        vectors.add(pa.RecordBatchReader.from_batches(
            vectors.schema, batches(vectors.schema, True),
        ))
    assert store.ensure_fts_index()
    content = store.content_store._get_or_create_table()
    initial = store._source_domain.receipt()
    embeddings = []

    class Inference:
        def embed_query(self, query):
            embeddings.append(query)
            return np.array([1, 0], dtype=np.float32)

    engine = SearchEngine({"small": Inference()}, store)
    families = tuple(f"git:family-{i}" for i in range(18))
    counts = []
    for table in (content, vectors):
        original = table.search

        def observed(*args, _original=original, **kwargs):
            counts.append(True)
            return _original(*args, **kwargs)

        monkeypatch.setattr(table, "search", observed)
    for mode in ("vector", "fts", "hybrid"):
        for name, granted, selectors in (
            ("empty", (), {}),
            ("explicit", ("git:family-0",), {}),
            ("full", families, {}),
            ("lookalike", ("git:family-0missing",), {}),
            ("language", families, {"language": "python"}),
            ("repo", families, {"repo": "family-0"}),
        ):
            assert not tracemalloc.is_tracing()
            before, queries = len(embeddings), len(counts)
            start = time.perf_counter()
            hits = engine.search_authorized("needle", families=granted, mode=mode,
                                            limit=4, **selectors)
            duration = time.perf_counter() - start
            print("COLD_QUERY " + json.dumps({
                "mode": mode, "grant": name, "seconds": round(duration, 6),
                "embeddings": len(embeddings) - before, "native_queries": len(counts) - queries,
            }, sort_keys=True))
            assert duration < 3.5
            assert len(counts) - queries <= 4
            assert len(embeddings) - before == (0 if not granted or mode == "fts" else 1)
            assert all(r.source != "git:family-0lookalike" for r in hits)
            if name in ("empty", "lookalike"):
                assert hits == []
            else:
                assert hits
            if name == "language":
                assert all(r.language == "python" for r in hits)
            if name in ("explicit", "repo"):
                assert all(r.source == "git:family-0" or
                           r.source.startswith("git:family-0:") for r in hits)
            if mode == "vector" and name == "full":
                assert {int(r.chunk_id, 16) for r in hits} == winners
    assert store._source_domain.receipt() == initial
    tracemalloc.start()
    try:
        engine.search_authorized("needle", families=families, mode="hybrid", limit=4)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 12 * 1024 * 1024
    assert initialize_source_store(config)._source_domain.receipt() == initial
