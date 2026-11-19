"""Owned-directory proofs for point selection and default-reader refusal."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig
from agent_index.indexing.point_hints import PointBinding, PointChangeHints, WorkItemRevisionTarget
from agent_index.store.cluster_store import ClusterStore
from agent_index.store.clustering import Cluster, ClusterMember
from agent_index.store.content_store import ContentStore
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import (
    LAYOUT,
    MARKER,
    SourceDomain,
    SourceLayoutError,
    domain_for,
    initialize_source_layout,
    initialize_source_store,
)
from agent_index.store.vector_table import VectorTable


def claim() -> PointChangeHints:
    return PointChangeHints(
        source="sample:components", canonical_family="sample:points", account_id="account-a",
        binding=PointBinding("https://example.test/project", "union-a", "context-a"),
        targets=(WorkItemRevisionTarget("42", 7),),
    )


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()}


def test_cold_point_binding_reopens_without_enabling_default_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path / "point")
    hints = claim()
    receipt = initialize_source_layout(config.lance_dir, point_scope=hints.scope)
    before = snapshot(config.data_dir)
    assert receipt["protocol"] == 2
    assert initialize_source_layout(config.lance_dir, point_scope=hints.scope) == receipt
    selected = domain_for(config.lance_dir, point_scope=hints.scope)
    assert selected is not None and selected.receipt() == receipt
    for open_store in (
        lambda: domain_for(config.lance_dir),
        lambda: SourceDomain(config.lance_dir).receipt(),
        lambda: initialize_source_layout(config.lance_dir),
        lambda: initialize_source_store(config),
        lambda: ContentStore(config.lance_dir),
        lambda: MultiModelStore(config.lance_dir),
        lambda: ClusterStore(config.data_dir / "clusters.db"),
    ):
        with pytest.raises(SourceLayoutError):
            open_store()
    for changed in (
        replace(hints, source="other:components"),
        replace(hints, canonical_family="other:points"),
        replace(hints, account_id="account-b"),
        replace(hints, binding=replace(hints.binding, endpoint="https://other.test/project")),
        replace(hints, binding=replace(hints.binding, query_scope="union-b")),
        replace(hints, binding=replace(hints.binding, query_context="context-b")),
    ):
        for open_domain in (initialize_source_layout, domain_for):
            with pytest.raises(SourceLayoutError, match="scope"):
                open_domain(config.lance_dir, point_scope=changed.scope)
    for operation in (selected.read, selected.operation, selected.write):
        with pytest.raises(SourceLayoutError, match="not admitted"), operation(object()):
            pytest.fail("point execution is not admitted")
    assert snapshot(config.data_dir) == before
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT")
    with pytest.raises(SourceLayoutError):
        domain_for(config.lance_dir, point_scope=hints.scope)
    with pytest.raises(SourceLayoutError):
        ContentStore(config.lance_dir)
    assert snapshot(config.data_dir) == before


def test_relative_cluster_connection_cannot_retarget_to_marked_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    original, destination = tmp_path / "original", tmp_path / "destination"
    original.mkdir()
    destination.mkdir()
    monkeypatch.chdir(original)
    store = ClusterStore(Path("shared/clusters.db"))

    def cluster(name: str) -> Cluster:
        return Cluster(
            "sample:items", name, (ClusterMember("sample:items", f"{name}.md", 1.0, False),), False,
        )

    assert store.replace_all([cluster("original")]) == 1
    target = destination / "shared"
    other = ClusterStore(target / "clusters.db")
    assert other.replace_all([cluster("destination-a"), cluster("destination-b")]) == 2
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    initialize_source_layout(target / "lance", point_scope=claim().scope)
    before = snapshot(target)
    monkeypatch.chdir(destination)
    assert store.stats()["clusters"] == 1
    assert len(store.list_clusters()) == 1
    assert store.replace_all([]) == 0
    assert store.stats()["clusters"] == 0
    assert snapshot(target) == before


def test_relative_fts_maintenance_stays_on_original_content_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    original, destination = tmp_path / "original", tmp_path / "destination"
    original.mkdir()
    destination.mkdir()
    monkeypatch.chdir(original)
    content = ContentStore(Path("lance"))
    item = Chunk("searchable original fixture", "original.md", "text", "markdown", 1, 1, "git:sample")
    content.upsert([item])
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    initialize_source_layout(destination / "lance", point_scope=claim().scope)
    before = snapshot(destination)
    monkeypatch.chdir(destination)
    assert content.ensure_fts_index(max_retries=1)
    assert [chunk_id for chunk_id, _ in content.fts_search("searchable")] == [item.chunk_id]
    assert snapshot(destination) == before


def test_point_scope_cannot_rebind_legacy_or_unmarked_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    legacy = tmp_path / "legacy"
    original = initialize_source_layout(legacy)
    before = snapshot(legacy)
    for operation in (initialize_source_layout, domain_for):
        with pytest.raises(SourceLayoutError, match="incompatible"):
            operation(legacy, point_scope=claim().scope)
    reopened = domain_for(legacy)
    assert reopened is not None and reopened.receipt() == original
    assert snapshot(legacy) == before
    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    (unmarked / "keep").write_bytes(b"fixture-owned legacy bytes")
    before = snapshot(unmarked)
    with pytest.raises(SourceLayoutError, match="nonempty"):
        initialize_source_layout(unmarked, point_scope=claim().scope)
    assert snapshot(unmarked) == before
    oversized = replace(claim(), source="x" * 65536)
    absent = tmp_path / "oversized"
    with pytest.raises(SourceLayoutError, match="size limit"):
        initialize_source_layout(absent, point_scope=oversized.scope)
    assert not absent.exists()


def test_cached_default_reader_refuses_receipt_drift_and_cannot_overwrite_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path / "legacy", model_profiles={})
    store = initialize_source_store(config)
    domain = store._source_domain
    assert domain is not None
    legacy_receipt = domain.receipt()
    assert store.stats()["total_chunks"] == 0
    point_path = tmp_path / "point"
    point_receipt = initialize_source_layout(point_path, point_scope=claim().scope)
    marker = config.lance_dir / MARKER
    marker.write_text(json.dumps({**point_receipt, "versions": legacy_receipt["versions"]}))
    before = marker.read_bytes()
    selected = domain_for(config.lance_dir, point_scope=claim().scope)
    assert selected is not None and selected is not domain
    operations: tuple[Any, ...] = (
        store.stats, lambda: store.get_document("sample:components", "missing.md"),
        lambda: domain_for(config.lance_dir), lambda: domain.save(legacy_receipt),
    )
    for operation in operations:
        with pytest.raises(SourceLayoutError):
            operation()
    assert marker.read_bytes() == before
    marker.write_text(json.dumps(legacy_receipt))
    with pytest.raises(SourceLayoutError):
        selected.receipt()
    before = marker.read_bytes()
    with pytest.raises(SourceLayoutError):
        selected.save(point_receipt)
    assert marker.read_bytes() == before
    assert domain_for(config.lance_dir) is domain
    assert store.stats()["total_chunks"] == 0


@pytest.mark.parametrize("relative", [False, True])
def test_unmarked_native_handles_refuse_a_later_point_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    config = IndexConfig(data_dir=caller / "late-selection")
    opened_path = Path("late-selection/lance") if relative else config.lance_dir
    content = ContentStore(opened_path)
    vectors = VectorTable(content.db, table_name="vectors_fixture", dim=2)
    store = MultiModelStore(opened_path)
    cluster_path = Path("late-selection/clusters.db") if relative else config.data_dir / "clusters.db"
    clusters = ClusterStore(cluster_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    initialize_source_layout(config.lance_dir, point_scope=claim().scope)
    before = snapshot(config.data_dir)
    operations: tuple[Any, ...] = (
        content.stats, content.distinct_items, lambda: content.fts_search("needle"),
        store.stats, lambda: store.get_document("sample:components", "missing.md"),
        lambda: store.upsert_content([]),
        vectors.count, lambda: vectors.get_vector("unknown"),
        lambda: vectors.search(np.array([1, 0], dtype=np.float32)),
        clusters.stats, clusters.list_clusters,
    )
    for operation in operations:
        with pytest.raises(SourceLayoutError, match="newly selected"):
            operation()
    assert snapshot(config.data_dir) == before


@pytest.mark.parametrize("populated", [False, True])
def test_query_frontdoors_refuse_point_domains_without_empty_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    populated: bool,
) -> None:
    from fastapi.testclient import TestClient

    from agent_index import __main__ as cli
    from agent_index import mcp_app, store as exported_stores, transport
    from agent_index.client import AgentIndexClient
    from agent_index.engine.client import EngineClient, EngineUnavailableError
    from agent_index.search.engine import SearchEngine, create_search_engine
    from agent_index.server import build_app

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_INDEX_SEARCH_IN_PROCESS", "0")
    config = IndexConfig()
    store = initialize_source_store(config)
    queries: list[str] = []

    def offline(self: EngineClient, query: str) -> np.ndarray[Any, Any]:
        queries.append(query)
        raise EngineUnavailableError("fixture engine offline")

    monkeypatch.setattr(EngineClient, "embed_query", offline)
    chunks = [
        Chunk("searchable fixture", "42.md", "text", "markdown", 1, 1, "sample:components"),
        Chunk("searchable fixture", "43.md", "text", "markdown", 1, 1, "sample:other"),
    ]
    if populated:
        store.upsert_content(chunks)
        assert store.ensure_fts_index()
    clusters = ClusterStore(config.clusters_db)
    clusters.replace_all([
        Cluster("sample:components", "fixture", (
            ClusterMember("sample:components", "42.md", 1.0, False),
        ), False),
    ] if populated else [])
    app = build_app()
    http = TestClient(app)  # No lifespan: never start a runner or publish an endpoint.
    engine = app.state.get_search_engine()
    assert engine is app.state.get_search_engine()
    assert isinstance(engine, SearchEngine)
    assert engine.search_authorized("searchable", families=()) == []
    if populated:
        assert SearchEngine({}, store).search("searchable") == []
    for source, expected_path in (("sample:components", "42.md"), ("sample:other", "43.md")):
        if populated:
            hits = engine.search("searchable", source=source)
            assert [hit.file_path for hit in hits] == [expected_path]
        else:
            with pytest.raises(SourceLayoutError, match="FTS is stale"):
                engine.search("searchable", source=source)
    legacy_search = http.get("/search", params={"q": "searchable", "source": "sample:components"})
    assert legacy_search.json()["available"] is populated
    if not populated:
        assert "FTS is stale" in legacy_search.json()["error"]
    assert len(legacy_search.json()["hits"]) == int(populated)
    assert http.get("/similar", params={"id": "missing"}).json()["available"] is True
    assert http.get("/clusters").json()["count"] == int(populated)

    point_receipt = initialize_source_layout(tmp_path / "point", point_scope=claim().scope)
    legacy_domain = store._source_domain
    assert legacy_domain is not None
    # Simulate externally changed selection without applying or migrating point data.
    point_receipt["versions"] = legacy_domain.receipt()["versions"]
    (config.lance_dir / MARKER).write_text(json.dumps(point_receipt))
    selected = domain_for(config.lance_dir, point_scope=claim().scope)
    assert selected is not None
    scoped = MultiModelStore(config.lance_dir, point_scope=claim().scope)
    scoped_engine = SearchEngine({}, scoped)
    profile = next(iter(config.model_profiles.values()))
    vectors = scoped.register_model(profile)
    vector = np.zeros(profile.dim, dtype=np.float32)
    before = snapshot(config.data_dir)
    queries.clear()
    with selected.operation(scoped._content.db, point_scope=claim().scope):
        for operation in (
            lambda: scoped_engine.search("searchable"),
            lambda: scoped_engine.search_authorized("searchable", families=()),
            lambda: scoped_engine.search_authorized("searchable", families=("sample:components",)),
            lambda: scoped_engine.find_similar("missing"),
            lambda: scoped.fts_search("searchable"),
            lambda: scoped.get_document("sample:other", "43.md"),
            scoped.projection_snapshot, scoped.distinct_sources, scoped.source_counts,
            scoped.distinct_items, scoped.distinct_forge_repos,
            lambda: scoped.search(profile.model_id, vector),
            lambda: scoped.hybrid_search(profile.model_id, vector, "searchable"),
            lambda: scoped.search_all({}, "searchable"),
            lambda: scoped.compare_search({}, "searchable"),
            lambda: scoped.content_store.get_by_ids([]),
            lambda: scoped.content_store.get_by_ids([chunks[0].chunk_id]),
            lambda: scoped.content_store.get_chunk_ids_by_source_exact("sample:components"),
            lambda: scoped.content_store.get_chunk_ids_by_file("sample:components", "42.md"),
            lambda: scoped.content_store.get_document("sample:other", "43.md"),
            scoped.content_store.source_counts, scoped.content_store.distinct_sources,
            scoped.content_store.stats,
            vectors.count, lambda: vectors.get_vector("missing"),
            lambda: vectors.get_vectors([]),
            lambda: vectors.get_vectors([chunks[0].chunk_id]),
            lambda: vectors.search(vector),
        ):
            with pytest.raises(SourceLayoutError, match="point-domain reads and application are not admitted"):
                operation()
    for legacy_operation in (
        lambda: engine.search("searchable"),
        clusters.list_clusters,
        lambda: clusters.get_cluster("missing"),
        lambda: clusters.cluster_for_item("sample:components", "42.md"),
        lambda: clusters.cluster_for_item("sample:unknown", "unknown.md"),
        clusters.stats, clusters.slice_manifest,
        lambda: clusters.covered_stats("unknown"),
        lambda: exported_stores.ContentStore(config.lance_dir),
        lambda: exported_stores.MultiModelStore(config.lance_dir),
        create_search_engine,
    ):
        with pytest.raises(SourceLayoutError, match="incompatible source-layout receipt"):
            legacy_operation()
    for query_source in (None, "sample:components", "sample:other", "sample:unknown"):
        params = (
            {"q": "searchable"} if query_source is None
            else {"q": "searchable", "source": query_source}
        )
        payload = http.get("/search", params=params).json()
        assert payload["available"] is False
        assert payload["hits"] == [] and "SourceLayoutError" in payload["error"]
    for path, params in (("/similar", {"id": "missing"}), ("/clusters", {})):
        payload = http.get(path, params=params).json()
        assert payload["available"] is False and "SourceLayoutError" in payload["error"]
    status = http.get("/status").json()["index"]
    assert status["chunks"] is None and status["available"] is None
    assert "SourceLayoutError" in status["error"]

    async def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await asyncio.to_thread(http.get, path, params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

    monkeypatch.setattr(mcp_app, "_get", get)
    for result in (
        asyncio.run(mcp_app.agent_index_search("searchable")),
        asyncio.run(mcp_app.agent_index_find_similar("missing")),
        asyncio.run(mcp_app.agent_index_clusters()),
    ):
        assert "unavailable" in result and "SourceLayoutError" in result
    status_text = asyncio.run(mcp_app.agent_index_status())
    assert "Index available: unknown" in status_text and "Total chunks: unknown" in status_text

    def request(
        self: AgentIndexClient, method: str, path: str,
        json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = http.request(method, path, json=json, params={
            key: value for key, value in (params or {}).items() if value is not None
        })
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

    monkeypatch.setattr(AgentIndexClient, "_request", request)
    monkeypatch.setattr(transport, "maybe_delegate", lambda _sub, _raw: None)
    monkeypatch.setattr(cli, "client_url", lambda: "http://fixture.invalid")
    config_file = tmp_path / "config.yaml"
    config_file.write_text("{}")
    monkeypatch.setenv("AGENT_INDEX_CONFIG", str(config_file))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    for role in ("host", "client"):
        monkeypatch.setattr(transport, "plan_route", lambda: (role, {}))
        for args in (
            ["search", "searchable", "--json"], ["search", "searchable"],
            ["similar", "missing"], ["clusters"],
        ):
            assert cli.main(args) != 0
            emitted = capsys.readouterr()
            assert "SourceLayoutError" in emitted.out + emitted.err
            assert emitted.out.strip() != "[]"
    assert queries == []
    assert snapshot(config.data_dir) == before
