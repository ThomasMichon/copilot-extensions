"""Actual round work stays distinct from logical result totals."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import httpx
import numpy as np
import pytest

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.work_counts import current_counts, measure_reindex
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.sources.good_citizen_http import GoodCitizenSession
from agent_index.store.source_layout import LAYOUT


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("model_count", [1, 2])
def test_native_round_counts_noop_metadata_edit_and_tombstone(
    tmp_path, monkeypatch, selected: bool, model_count: int,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    profiles = {
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("a", "b")[:model_count]
    }
    config = IndexConfig(data_dir=tmp_path, model_profiles=profiles, cluster_enabled=False)
    entries = [FileEntry("one.md", "Useful text.", "markdown", "sample:repo")]
    delta = SourceDelta(upserts=entries, commit="first")
    fail_embedding = False

    class Connector:
        source_name = "sample:repo"
        recovery_identity = ("fixture", "account")

        def discover_delta(self, *_args, **_kwargs):
            return delta

    class Client:
        def embed_texts(self, texts):
            if fail_embedding:
                raise RuntimeError("fixture embed failure")
            return np.array([[len(text), 1] for text in texts], dtype=np.float32)

        def close(self):
            pass

    monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: Connector())
    def no_lifecycle(*_args):
        raise AssertionError("unmanaged runtime attempted engine lifecycle")

    runtime = EmbeddingRuntime(
        lambda _profile: Client(), no_lifecycle, no_lifecycle, manage_engines=False,
    )

    def run():
        result = engine.run_reindex(
            source="sample:repo", config=config,
            source_specs=[engine.SourceSpec("sample:repo", type="sample")],
            embedding_runtime=runtime,
        )
        assert "sources_failed" not in result
        json.dumps(result)
        assert current_counts() is None
        return result, result["work_counts"]

    first, counts = run()
    assert first["chunks_total"] == counts["chunks_generated"] == 1
    assert counts["identities_scanned"] == counts["files_chunked"] == 1
    assert counts["checkpoint_lookup_keys"] == counts["checkpoint_rows_written"] == 1
    assert counts["content_rows_upserted"] == 1
    assert counts["http"] is None
    for model in profiles:
        assert counts["models"][model] == {
            "candidate_chunks": 1, "reused_chunks": 0, "embed_calls": 1,
            "chunks_submitted": 1, "chunks_embedded": 1, "vector_rows_upserted": 1,
        }

    # A replayed identity is acquired but a compatible checkpoint avoids chunking.
    repeated, counts = run()
    assert repeated["chunks_total"] == 0
    assert counts["upserts_acquired"] == counts["files_reused"] == 1
    assert counts["checkpoint_rows_found"] == 1
    assert counts["files_chunked"] == counts["checkpoint_rows_written"] == 0
    assert counts["content_rows_upserted"] == 0 and counts["models"] == {}

    delta = SourceDelta(commit="first")
    _, counts = run()
    assert counts["manifests_acquired"] == counts["manifests_applied"] == 1
    assert counts["identities_scanned"] == counts["files_chunked"] == 0
    assert counts["checkpoint_lookup_keys"] == counts["content_rows_upserted"] == 0
    assert counts["models"] == {}

    assert counts["cluster_pass_attempts"] == counts["fts_ensure_attempts"] == 0

    delta = SourceDelta(
        upserts=[replace(entries[0], metadata={"state": "closed"})], commit="metadata",
    )
    metadata, counts = run()
    assert metadata["chunks_total"] == counts["chunks_generated"] == 1
    assert counts["content_rows_upserted"] == 1
    for model in profiles:
        assert counts["models"][model]["reused_chunks"] == 1
        assert counts["models"][model]["embed_calls"] == 0
        assert counts["models"][model]["vector_rows_upserted"] == 0

    delta = SourceDelta(
        upserts=[replace(entries[0], content="Changed text.")], commit="changed",
    )
    _, counts = run()
    assert counts["stale_chunk_ids_deleted"] == 1
    assert counts["content_rows_upserted"] == 1
    delta = SourceDelta(
        deletions=[DeletedFile("sample:repo", "one.md")], commit="deleted",
    )
    _, counts = run()
    assert counts["tombstones_acquired"] == counts["file_chunk_ids_deleted"] == 1
    assert counts["files_chunked"] == counts["content_rows_upserted"] == 0
    assert counts["models"] == {}

    delta = SourceDelta(upserts=entries, commit="failed")
    fail_embedding = True
    failed = engine.run_reindex(
        source="sample:repo", config=config,
        source_specs=[engine.SourceSpec("sample:repo", type="sample")],
        embedding_runtime=runtime,
    )
    assert failed["sources_failed"][0]["error"] == "fixture embed failure"
    counts = failed["work_counts"]
    assert counts["content_rows_upserted"] == 1
    assert counts["manifests_applied"] == counts["checkpoint_rows_written"] == 0
    assert counts["models"]["a"]["embed_calls"] == 1
    assert counts["models"]["a"]["chunks_embedded"] == 0
    assert counts["models"]["a"]["vector_rows_upserted"] == 0
    fail_embedding = False
    _, counts = run()
    assert counts["content_rows_upserted"] == 0
    assert counts["manifests_acquired"] == 1 and counts["manifests_applied"] == 2
    assert counts["files_chunked"] == counts["files_reused"] == 1
    assert counts["models"]["a"]["vector_rows_upserted"] == 1


def test_shared_http_counts_retries_payloads_not_modified_and_json(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _delay: None)
    responses = [
        httpx.ReadTimeout("fixture timeout"),
        httpx.Response(503, content=b"retry"),
        httpx.Response(200, content=b'{"ok":true}', headers={"ETag": '"v1"'}),
        httpx.Response(304),
        httpx.Response(200, content=b"bad json"),
    ]

    def handler(request):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    session = GoodCitizenSession(
        base_url="https://example.test", transport=httpx.MockTransport(handler), min_interval_s=0,
    )

    @measure_reindex
    def run():
        assert session.get_json("/items").data == {"ok": True}
        assert session.get_json("/items").not_modified
        with pytest.raises(ValueError):
            session.get_json("/invalid")
        return {}

    try:
        assert run()["work_counts"]["http"] == {
            "attempts": 5, "responses": 4, "retries": 2,
            "response_bytes": 5 + 11 + 8, "not_modified": 1, "json_results": 1,
        }
    finally:
        session.client.close()
    assert current_counts() is None


def test_nested_concurrent_and_failed_scopes_do_not_leak() -> None:
    barrier = Barrier(2)

    @measure_reindex
    def inner():
        assert current_counts().files_chunked == 0
        current_counts().files_chunked += 3
        return {}

    @measure_reindex
    def run(value):
        current_counts().files_chunked += value
        barrier.wait(timeout=5)
        assert inner()["work_counts"]["files_chunked"] == 3
        assert current_counts().files_chunked == value
        return {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, (1, 2)))
    assert [r["work_counts"]["files_chunked"] for r in results] == [1, 2]

    @measure_reindex
    def fail():
        raise RuntimeError("cancelled or failed")

    with pytest.raises(RuntimeError, match="cancelled or failed"):
        fail()
    assert current_counts() is None


@pytest.mark.parametrize("fail", [False, True])
def test_cluster_counts_distinguish_cached_completed_and_failed(tmp_path, monkeypatch, fail):
    from agent_index.indexing import derived_maintenance
    from agent_index.store.cluster_store import ClusterStore

    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(data_dir=tmp_path, cluster_enabled=True)
    monkeypatch.setattr(derived_maintenance, "cluster_signature", lambda *_a: "signature")
    cached = None
    monkeypatch.setattr(ClusterStore, "covered_stats", lambda *_a: cached)

    def cluster(*_args, **_kwargs):
        if fail:
            raise RuntimeError("fixture cluster failure")
        return {"clusters": 1, "slices": 1, "items": 1, "elapsed_ms": 1}

    monkeypatch.setattr("agent_index.indexing.cluster_pass.run_clustering_pass", cluster)

    @measure_reindex
    def run():
        derived_maintenance.refresh_clusters(object(), config)
        return {}

    counts = run()["work_counts"]
    assert counts["cluster_pass_attempts"] == 1
    assert counts["cluster_pass_completed"] == int(not fail)
    assert counts["cluster_pass_failures"] == int(fail)
    assert counts["cluster_cache_hits"] == 0
    cached = {"clusters": 1, "maintenance_skipped": 1}
    counts = run()["work_counts"]
    assert counts["cluster_cache_hits"] == 1
    assert counts["cluster_pass_attempts"] == counts["cluster_pass_failures"] == 0
