"""Cold owned-directory proofs; no point execution or component reads are enabled."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

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
