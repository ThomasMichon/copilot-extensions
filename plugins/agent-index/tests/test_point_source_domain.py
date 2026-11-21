"""Owned-directory proofs for point selection and default-reader refusal."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_manifest import ComponentContent, ComponentIdentity, ComponentVersion
from agent_index.indexing.point_hints import PointBinding, PointChangeHints, WorkItemRevisionTarget
from agent_index.store.cluster_store import ClusterStore
from agent_index.store.clustering import Cluster, ClusterMember
from agent_index.store.component_ledger import (
    BINDING, TABLE, ComponentEvidence, ComponentLedger, initialize_component_ledger,
)
from agent_index.store.content_store import ContentStore
from agent_index.store.content_mutations import content_record
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import (
    MAX_SEGMENTS, ScalarIndexPreparationRequired, inspect_index, prepare_index,
)
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


def test_point_vector_preflight_requires_exact_owned_input_without_enabling_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    item = Chunk("retained text", "one.md", "text", "markdown", 1, 1, scope[1], {"title": "current"})
    values = np.array([[1.0, 0.0]], dtype=np.float32)
    empty = np.zeros((0, 2), dtype=np.float32)
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-canonical-seed")
        store.content_store._get_or_create_table().add([{**content_record(item), "indexed_at": 0.0}])
    prepare_ingestion_indices(store, max_rows=1, point_scope=scope)
    before = snapshot(config.data_dir)
    for chunks, vectors in (([item], values), ([], empty)):
        with pytest.raises(SourceLayoutError, match="owning source writer"):
            store.preflight_point_vectors("small", chunks, vectors, point_scope=scope)
        assert snapshot(config.data_dir) == before

    with domain._write(store.content_store.db, point_scope=scope):
        before = snapshot(config.data_dir)
        for chunks, vectors in (([item], values), ([], empty)):
            for selected in (None, replace(claim(), account_id="other").scope):
                with pytest.raises(SourceLayoutError):
                    store.preflight_point_vectors("small", chunks, vectors, point_scope=selected)
                assert snapshot(config.data_dir) == before
            for mode in ("bounded_input", "bounded_provenance"):
                with pytest.raises(SourceLayoutError):
                    store.upsert_vectors("small", chunks, vectors, **{mode: True})
                assert snapshot(config.data_dir) == before
        for changed, message in (
            (replace(item, source="other:points"), "selected family"),
            (replace(item, metadata={"title": "outdated"}), "current canonical embedding input"),
            (replace(item, content="outdated text"), "canonical"),
        ):
            with pytest.raises(SourceLayoutError, match=message):
                store.preflight_point_vectors("small", [changed], values, point_scope=scope)
            assert snapshot(config.data_dir) == before
        assert store.preflight_point_vectors("small", [], empty, point_scope=scope) == {}
        assert snapshot(config.data_dir) == before
        assert store.preflight_point_vectors("small", [item], values, point_scope=scope) == {
            item.chunk_id: content_record(item),
        }
        assert snapshot(config.data_dir) == before

    reopened = initialize_source_store(config, point_scope=scope)
    retained = reopened.get_vector_table("small").get_point_vectors(
        [item.chunk_id], point_scope=scope,
    )
    assert retained == {}
    assert domain.receipt()["state"] == "clean"
    before = snapshot(config.data_dir)
    for ordinary in (
        lambda: reopened.upsert_vectors("small", [item], values),
        lambda: reopened.upsert_vectors("small", [item], values, bounded_input=True),
        lambda: reopened.upsert_content([item]),
        lambda: reopened.get_vector_table("small").get_vectors([item.chunk_id]),
    ):
        with pytest.raises(SourceLayoutError):
            ordinary()
        assert snapshot(config.data_dir) == before


@pytest.mark.parametrize("foreign_writer_connection", [False, True])
def test_point_vectors_refuse_mismatched_store_and_active_writer_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_writer_connection: bool,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    scope = claim().scope
    config = IndexConfig(data_dir=tmp_path / "first", model_profiles={"small": profile})
    first = initialize_source_store(config, point_scope=scope)
    second_config = replace(config, data_dir=tmp_path / "second")
    second = initialize_source_store(second_config, point_scope=scope)
    domain = first._source_domain
    assert domain is not None
    owner = VectorTable(
        (first if foreign_writer_connection else second).content_store.db,
        table_name=profile.table_name, dim=2, source_domain=domain,
        content_table=config.content_table,
    )
    active = (second if foreign_writer_connection else first).content_store.db
    item = Chunk("text", "one.md", "text", "markdown", 1, 1, scope[1])
    with domain._write(active, point_scope=scope):
        before_first, before_second = snapshot(config.data_dir), snapshot(second_config.data_dir)
        for chunks, vectors in (
            ([item], np.ones((1, 2), dtype=np.float32)),
            ([], np.zeros((0, 2), dtype=np.float32)),
        ):
            with pytest.raises(SourceLayoutError, match="connection differs"):
                owner.preflight_point_upsert(chunks, vectors, point_scope=scope)
            assert snapshot(config.data_dir) == before_first
            assert snapshot(second_config.data_dir) == before_second


@pytest.mark.parametrize("binding", [
    "collision", "missing-vector", "missing-content", "wrong-vector-schema", "wrong-content-schema",
])
def test_point_vectors_refuse_unprepared_or_colliding_table_bindings_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding: str,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    sibling = ModelProfile("sibling", "fixture", dim=2, table_name="vectors_sibling")
    config = IndexConfig(
        data_dir=tmp_path / "point", model_profiles={"small": profile, "sibling": sibling},
    )
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    item = Chunk("retain canonical", "one.md", "text", "markdown", 1, 1, scope[1])
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-canonical-seed")
        store.content_store._get_or_create_table().add([{**content_record(item), "indexed_at": 0.0}])
    prepare_ingestion_indices(store, max_rows=1, point_scope=scope)
    if binding == "missing-vector":
        owner = VectorTable(
            store.content_store.db, table_name="unprepared_vectors", dim=2,
            source_domain=domain, content_table=config.content_table,
        )
    else:
        owner = VectorTable(
            store.content_store.db,
            table_name=config.content_table if binding == "collision" else profile.table_name,
            dim=3 if binding == "wrong-vector-schema" else 2, source_domain=domain,
            content_table=(
                "unprepared_content" if binding == "missing-content"
                else sibling.table_name if binding == "wrong-content-schema"
                else config.content_table
            ),
        )
    with domain._write(store.content_store.db, point_scope=scope):
        before = snapshot(config.data_dir)
        for chunks, vectors in (
            ([item], np.ones((1, owner.dim), dtype=np.float32)),
            ([], np.zeros((0, owner.dim), dtype=np.float32)),
        ):
            with pytest.raises(SourceLayoutError, match="(?:distinct prepared|schema is incompatible)"):
                owner.preflight_point_upsert(chunks, vectors, point_scope=scope)
            assert snapshot(config.data_dir) == before


@pytest.mark.parametrize("unprepared", ["missing-index", "tail"])
def test_point_vector_preflight_requires_prepared_index_and_one_canonical_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unprepared: str,
) -> None:
    from agent_index.store import source_projection

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    items = [
        Chunk(str(i), f"{i}.md", "text", "markdown", 1, 1, scope[1]) for i in range(20)
    ]
    table = store.get_vector_table("small")._get_or_create_table()
    rows = [
        {"chunk_id": item.chunk_id, "vector": [1.0, 0.0], "indexed_at": 0.0,
         "source": item.source, "language": item.language} for item in items
    ]
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-canonical-and-vector-seed")
        store.content_store._get_or_create_table().add([
            {**content_record(item), "indexed_at": 0.0} for item in items
        ])
        table.add(rows)
    prepare_ingestion_indices(store, max_rows=len(items), point_scope=scope)
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-vector-index-gap")
        if unprepared == "missing-index":
            table.drop_index("chunk_id_idx")
        else:
            table.add(rows[:1])
    with domain._write(store.content_store.db, point_scope=scope):
        before = snapshot(config.data_dir)
        with pytest.raises(ScalarIndexPreparationRequired):
            store.preflight_point_vectors(
                "small", items[:1], np.ones((1, 2), dtype=np.float32),
                point_scope=scope,
            )
        assert snapshot(config.data_dir) == before
    prepare_ingestion_indices(store, max_rows=len(items) + 1, point_scope=scope)
    calls: list[int] = []
    plans: list[str] = []
    projection = source_projection.projection_records
    search = type(table).search

    class ObservedQuery:
        def __init__(self, query: Any) -> None:
            self.query = query

        def where(self, predicate: str) -> ObservedQuery:
            assert predicate.startswith("chunk_id IN ("), predicate
            self.query = self.query.where(predicate)
            return self

        def select(self, columns: list[str]) -> ObservedQuery:
            assert set(columns) == set(content_record(items[0]))
            self.query = self.query.select(columns)
            return self

        def limit(self, rows: int) -> ObservedQuery:
            assert rows == 2
            self.query = self.query.limit(rows)
            return self

        def to_list(self) -> list[dict[str, Any]]:
            plan = self.query.analyze_plan()
            assert "ScalarIndexQuery" in plan, plan
            scanned = re.findall(r"\brows_scanned=(\d+)", plan)
            assert scanned and sum(map(int, scanned)) <= 1, plan
            plans.append(plan)
            return cast(list[dict[str, Any]], self.query.to_list())

    def observed_search(self: Any, *args: Any, **kwargs: Any) -> Any:
        query = search(self, *args, **kwargs)
        if self.name == config.content_table:
            return ObservedQuery(query)
        return query

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return projection(*args, **kwargs)

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("point preflight must not perform native mutations")

    monkeypatch.setattr(source_projection, "projection_records", counted)
    monkeypatch.setattr(type(table), "search", observed_search)
    for method in ("delete", "add", "update", "create_scalar_index", "create_index"):
        monkeypatch.setattr(type(table), method, forbidden)
    with domain._write(store.content_store.db, point_scope=scope):
        before = snapshot(config.data_dir)
        assert store.preflight_point_vectors(
            "small", items[:1], np.ones((1, 2), dtype=np.float32),
            point_scope=scope,
        ) == {items[0].chunk_id: content_record(items[0])}
        assert snapshot(config.data_dir) == before
    assert calls == [1] and len(plans) == 1


def test_point_vector_preflight_reuses_open_canonical_table_without_creation_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.store import content_store

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    item = Chunk("retained text", "one.md", "text", "markdown", 1, 1, scope[1])
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-canonical-seed")
        store.content_store._get_or_create_table().add([{**content_record(item), "indexed_at": 0.0}])
    prepare_ingestion_indices(store, max_rows=1, point_scope=scope)
    opens: list[int] = []
    open_table = type(store.content_store.db).open_table
    content_type = ContentStore

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        opens.append(1)
        raise OSError("fixture canonical table open unavailable")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("read-only preflight attempted canonical table creation")

    def existing_content(*args: Any, **kwargs: Any) -> ContentStore:
        content = content_type(*args, **kwargs)
        content._db = SimpleNamespace(
            uri=content._db.uri, open_table=unavailable, create_table=forbidden,
        )
        return content

    def failed_binding_open(self: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == config.content_table:
            return unavailable()
        return open_table(self, name, *args, **kwargs)

    with domain._write(store.content_store.db, point_scope=scope):
        before = snapshot(config.data_dir)
        with monkeypatch.context() as patch:
            patch.setattr(content_store, "ContentStore", existing_content)
            assert store.preflight_point_vectors(
                "small", [item], np.ones((1, 2), dtype=np.float32), point_scope=scope,
            ) == {item.chunk_id: content_record(item)}
            assert opens == []
            assert snapshot(config.data_dir) == before
            patch.setattr(type(store.content_store.db), "open_table", failed_binding_open)
            patch.setattr(type(store.content_store.db), "create_table", forbidden)
            with pytest.raises(OSError, match="canonical table open unavailable"):
                store.preflight_point_vectors(
                    "small", [item], np.ones((1, 2), dtype=np.float32), point_scope=scope,
                )
            assert opens == [1]
            assert snapshot(config.data_dir) == before


def test_point_vector_preflight_reserves_a_segment_only_for_nonempty_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.store import scalar_index

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    prepare_ingestion_indices(store, max_rows=0, point_scope=scope)
    inspect = scalar_index.inspect_index

    def at_limit(table: Any, *, column: str) -> tuple[Any, Any]:
        state, dataset = inspect(table, column=column)
        if table.name == profile.table_name:
            state = replace(state, segments=MAX_SEGMENTS)
        return state, dataset

    monkeypatch.setattr(scalar_index, "inspect_index", at_limit)
    with domain._write(store.content_store.db, point_scope=scope):
        before = snapshot(config.data_dir)
        with pytest.raises(ScalarIndexPreparationRequired, match="reserved index segment"):
            store.preflight_point_vectors(
                "small", [Chunk("text", "one.md", "text", "markdown", 1, 1, scope[1])],
                np.ones((1, 2), dtype=np.float32), point_scope=scope,
            )
        assert snapshot(config.data_dir) == before
        assert store.preflight_point_vectors(
            "small", [], np.zeros((0, 2), dtype=np.float32), point_scope=scope,
        ) == {}
        assert snapshot(config.data_dir) == before


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


def test_cold_point_schema_bootstrap_keeps_ordinary_access_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture-inference-only", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    store = initialize_source_store(config, point_scope=claim().scope)
    domain = store._source_domain
    assert domain is not None
    receipt = domain.receipt()
    assert receipt["protocol"] == 2 and receipt["state"] == "clean"
    assert set(receipt["versions"]) == {config.content_table, profile.table_name}
    with domain.operation(store.content_store.db, point_scope=claim().scope):
        assert all(
            store.content_store.db.open_table(name).count_rows() == 0
            for name in receipt["versions"]
        )
        for operation in (
            store.stats, lambda: store.upsert_content([]),
            lambda: store.get_vector_table("small").upsert([], np.zeros((0, 2), dtype=np.float32)),
        ):
            with pytest.raises(SourceLayoutError, match="not admitted"):
                operation()
        with pytest.raises(SourceLayoutError, match="not admitted"), domain.write(store.content_store.db):
            pytest.fail("schema bootstrap must not admit ordinary writers")
    before = snapshot(config.data_dir)
    reopened = initialize_source_store(config, point_scope=claim().scope)
    assert reopened._source_domain is domain
    assert domain.receipt() == receipt
    for changed in (
        replace(config, content_table="different"),
        replace(config, model_profiles={"small": replace(profile, table_name="different")}),
        replace(config, model_profiles={"small": replace(profile, dim=3)}),
    ):
        with pytest.raises(SourceLayoutError, match="bootstrap"):
            initialize_source_store(changed, point_scope=claim().scope)
        assert snapshot(config.data_dir) == before
    for selected_scope in (None, replace(claim(), account_id="other").scope):
        with pytest.raises(SourceLayoutError):
            initialize_source_store(config, point_scope=selected_scope)
        assert snapshot(config.data_dir) == before


def test_point_schema_bootstrap_never_rebinds_legacy_or_replays_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture-inference-only", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    for profiles in (
        {"wrong-key": profile},
        {"small": replace(profile, table_name=config.content_table)},
        {"small": profile, "other": ModelProfile("other", "fixture", table_name=profile.table_name, dim=2)},
    ):
        with pytest.raises(SourceLayoutError, match="distinct configured"):
            initialize_source_store(replace(config, model_profiles=profiles), point_scope=claim().scope)
        assert not config.data_dir.exists()
    legacy = initialize_source_store(config)
    legacy.upsert("small", [
        Chunk("retain legacy", "legacy.md", "text", "markdown", 1, 1, "sample:legacy"),
    ], np.array([[1, 0]], dtype=np.float32))
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="incompatible"):
        initialize_source_store(config, point_scope=claim().scope)
    assert snapshot(config.data_dir) == before
    interrupted = replace(config, data_dir=tmp_path / "interrupted")

    def fail(_self: VectorTable) -> Any:
        raise RuntimeError("fixture schema interruption")

    with monkeypatch.context() as patch:
        patch.setattr(VectorTable, "_get_or_create_table", fail)
        with pytest.raises(RuntimeError, match="fixture schema interruption"):
            initialize_source_store(interrupted, point_scope=claim().scope)
    before = snapshot(interrupted.data_dir)
    with pytest.raises(SourceLayoutError, match="explicit recovery"):
        initialize_source_store(interrupted, point_scope=claim().scope)
    assert snapshot(interrupted.data_dir) == before


@pytest.mark.parametrize("same_table", [False, True])
def test_point_schema_bootstrap_rechecks_an_interleaved_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_table: bool,
) -> None:
    from agent_index.store import source_layout

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    competing = replace(config, model_profiles={"small": replace(
        profile, dim=3 if same_table else 2,
        table_name=profile.table_name if same_table else "other_vectors",
    )})
    initialize = source_layout.initialize_source_layout
    before: dict[str, bytes] = {}
    interleaved = False

    def interleave(path: str | Path, **kwargs: Any) -> dict[str, Any]:
        nonlocal before, interleaved
        receipt = initialize(path, **kwargs)
        if not interleaved:
            interleaved = True
            initialize_source_store(competing, point_scope=claim().scope)
            before = snapshot(config.data_dir)
        return receipt

    monkeypatch.setattr(source_layout, "initialize_source_layout", interleave)
    with pytest.raises(SourceLayoutError, match="bootstrap"):
        initialize_source_store(config, point_scope=claim().scope)
    assert interleaved and before
    assert snapshot(config.data_dir) == before


def test_point_schema_bootstrap_refuses_vector_table_as_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    first = ModelProfile("first", "fixture", dim=2, table_name="vectors_first")
    second = ModelProfile("second", "fixture", dim=2, table_name="vectors_second")
    config = IndexConfig(
        data_dir=tmp_path / "point", model_profiles={"first": first, "second": second},
    )
    initialize_source_store(config, point_scope=claim().scope)
    before = snapshot(config.data_dir)
    changed = replace(config, content_table=second.table_name, model_profiles={"first": first})
    with pytest.raises(SourceLayoutError, match="bootstrap model binding"):
        initialize_source_store(changed, point_scope=claim().scope)
    assert snapshot(config.data_dir) == before


def test_selected_point_ledger_and_ingestion_preparation_reopen_without_default_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    ledger = initialize_component_ledger(config, store, point_scope=scope)
    prepare_ingestion_indices(store, max_rows=0, point_scope=scope)
    receipt = domain.receipt()
    assert receipt["state"] == "clean"
    assert set(receipt["versions"]) == {config.content_table, profile.table_name, TABLE}
    for name, columns in (
        (config.content_table, ("chunk_id", "file_path")),
        (profile.table_name, ("chunk_id",)), (TABLE, ("point_key",)),
    ):
        table = store.content_store.db.open_table(name)
        assert table.count_rows() == 0
        for column in columns:
            state, _ = inspect_index(table, column=column)
            assert state.prepared and state.physical_unindexed_rows == 0
    assert receipt[BINDING]["version"] == store.content_store.db.open_table(TABLE).version
    before = snapshot(config.data_dir)

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("reopening/prepared indices counted the corpus")

    monkeypatch.setattr(type(table), "count_rows", forbidden)
    reopened = initialize_source_store(config, point_scope=scope)
    restored = initialize_component_ledger(config, reopened, point_scope=scope)
    prepare_ingestion_indices(reopened, max_rows=0, point_scope=scope)
    assert restored.get(scope[1], "unknown.md", point_scope=scope) is None
    with domain.operation(store.content_store.db, point_scope=scope):
        for ordinary in (
            lambda: ledger.get(scope[1], "unknown.md"),
            lambda: initialize_component_ledger(config, store),
            lambda: prepare_ingestion_indices(store, max_rows=0),
            lambda: prepare_index(store.content_store, column="chunk_id", max_rows=0),
            lambda: ledger.consolidate(max_rows=0), store.stats,
            lambda: store.upsert_content([]),
        ):
            with pytest.raises(SourceLayoutError, match="not admitted"):
                ordinary()
    assert domain.receipt() == receipt
    assert snapshot(config.data_dir) == before


def test_point_preparation_requires_exact_selection_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "point",
        model_profiles={"small": ModelProfile("small", "fixture", dim=2)},
    )
    store = initialize_source_store(config, point_scope=claim().scope)
    before = snapshot(config.data_dir)
    for scope in (None, replace(claim(), account_id="other").scope):
        for prepare in (
            lambda: initialize_component_ledger(config, store, point_scope=scope),
            lambda: prepare_ingestion_indices(store, max_rows=0, point_scope=scope),
            lambda: prepare_index(
                store.content_store, column="chunk_id", max_rows=0, point_scope=scope,
            ),
        ):
            with pytest.raises(SourceLayoutError):
                prepare()
            assert snapshot(config.data_dir) == before
    legacy = initialize_source_store(replace(config, data_dir=tmp_path / "legacy"))
    legacy_domain = legacy._source_domain
    assert legacy_domain is not None
    before = snapshot(legacy_domain.path)
    for prepare in (
        lambda: initialize_component_ledger(
            replace(config, data_dir=tmp_path / "legacy"), legacy, point_scope=claim().scope,
        ),
        lambda: prepare_ingestion_indices(legacy, max_rows=0, point_scope=claim().scope),
    ):
        with pytest.raises(SourceLayoutError, match="scope"):
            prepare()
        assert snapshot(legacy_domain.path) == before


def test_point_ledger_retains_ordering_under_an_already_owned_exact_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "point",
        model_profiles={"small": ModelProfile("small", "fixture", dim=2)},
    )
    hints = claim()
    scope = hints.scope
    store = initialize_source_store(config, point_scope=scope)
    ledger = initialize_component_ledger(config, store, point_scope=scope)
    prepare_ingestion_indices(store, max_rows=0, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    first = ComponentEvidence(
        ComponentContent(ComponentVersion(
            scope[1], "opaque-body", 7, "fixture/v1", ComponentIdentity("work_item_revision", "42"),
        ), "deleted-v7", True),
        "account-endpoint-query", "fixture-policy",
        capture_file_projection(config, store, scope[1], "opaque-body", max_rows=1, point_scope=scope),
    )
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="owning source writer"):
        ledger.put(first, expected=None, max_rows=1, point_scope=scope)
    assert snapshot(config.data_dir) == before
    with domain._write(store.content_store.db, point_scope=scope):
        for selected_scope in (None, replace(hints, account_id="other").scope):
            before = snapshot(config.data_dir)
            with pytest.raises(SourceLayoutError):
                ledger.put(first, expected=None, max_rows=1, point_scope=selected_scope)
            assert snapshot(config.data_dir) == before
        foreign = replace(
            first, content=replace(first.content, component=replace(
                first.content.component, source="other:points",
            )), projection=replace(first.projection, source="other:points"),
        )
        before = snapshot(config.data_dir)
        with pytest.raises(SourceLayoutError, match="outside its selected family"):
            ledger.put(foreign, expected=None, max_rows=1, point_scope=scope)
        assert snapshot(config.data_dir) == before
        assert ledger.put(first, expected=None, max_rows=1, point_scope=scope)
    second = replace(first, content=replace(
        first.content, component=replace(first.content.component, version=8), fingerprint="deleted-v8",
    ))
    with domain._write(store.content_store.db, point_scope=scope):
        assert ledger.put(second, expected=first, max_rows=1, point_scope=scope)
        assert not ledger.put(first, expected=second, max_rows=1, point_scope=scope)
        assert not ledger.put(second, expected=second, max_rows=1, point_scope=scope)
    assert domain.receipt()["state"] == "clean"
    before = snapshot(config.data_dir)
    reopened = initialize_source_store(config, point_scope=scope)
    restored = initialize_component_ledger(config, reopened, point_scope=scope)
    assert restored.get(scope[1], "opaque-body", point_scope=scope) == second
    assert snapshot(config.data_dir) == before
    with domain.operation(store.content_store.db, point_scope=scope):
        for ordinary in (
            lambda: ledger.get(scope[1], "opaque-body"),
            lambda: ledger.consolidate(max_rows=1),
        ):
            with pytest.raises(SourceLayoutError, match="not admitted"):
                ordinary()
        with pytest.raises(SourceLayoutError, match="not admitted"):
            with domain.write(store.content_store.db):
                pytest.fail("ordinary point writes remain disabled")
    assert snapshot(config.data_dir) == before


@pytest.mark.parametrize("stage", ["index", "binding"])
def test_interrupted_point_ledger_write_requires_existing_explicit_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from agent_index.store import component_ledger

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "point",
        model_profiles={"small": ModelProfile("small", "fixture", dim=2)},
    )
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    ledger = initialize_component_ledger(config, store, point_scope=scope)
    prepare_ingestion_indices(store, max_rows=0, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    record = ComponentEvidence(
        ComponentContent(ComponentVersion(
            scope[1], "opaque-body", 7, "fixture/v1", ComponentIdentity("work_item_revision", "42"),
        ), "deleted-v7", True),
        "account-endpoint-query", "fixture-policy",
        capture_file_projection(config, store, scope[1], "opaque-body", max_rows=1, point_scope=scope),
    )

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("fixture ledger publication interruption")

    with monkeypatch.context() as patch:
        if stage == "index":
            patch.setattr(component_ledger, "prepare_index", fail)
        else:
            patch.setattr(ComponentLedger, "_bind", fail)
        with pytest.raises(RuntimeError, match="ledger publication interruption"):
            with domain._write(store.content_store.db, point_scope=scope):
                ledger.put(record, expected=None, max_rows=1, point_scope=scope)
    receipt = domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"]
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="incomplete or changed"):
        ledger.get(scope[1], "opaque-body", point_scope=scope)
    with pytest.raises(SourceLayoutError, match="explicit recovery"):
        with domain._write(store.content_store.db, point_scope=scope):
            pytest.fail("dirty point evidence must not retry itself")
    assert snapshot(config.data_dir) == before


def test_point_index_segment_reservation_preflights_every_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.store import ingestion_index

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    store = initialize_source_store(config, point_scope=claim().scope)
    before = snapshot(config.data_dir)
    inspect_native = inspect_index

    def at_limit(table: Any, *, column: str) -> Any:
        state, dataset = inspect_native(table, column=column)
        if table.name == profile.table_name:
            state = replace(state, segments=MAX_SEGMENTS)
        return state, dataset

    monkeypatch.setattr(ingestion_index, "inspect_index", at_limit)
    with pytest.raises(ScalarIndexPreparationRequired, match="segment budget"):
        prepare_ingestion_indices(
            store, max_rows=0, reserve_segment=True, point_scope=claim().scope,
        )
    assert snapshot(config.data_dir) == before


@pytest.mark.parametrize("populated", ["content", "vector"])
def test_point_ledger_refuses_existing_data_and_indices_keep_physical_tail_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, populated: str,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("small", "fixture", dim=2)
    config = IndexConfig(data_dir=tmp_path / "point", model_profiles={"small": profile})
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    table = store.content_store.db.open_table(
        config.content_table if populated == "content" else profile.table_name,
    )
    row: dict[str, Any] = (
        {
            "chunk_id": "fixture", "source": scope[1], "file_path": "one.md",
            "chunk_type": "text", "language": "markdown", "content": "retain",
            "content_hash": "fixture", "line_start": 1, "line_end": 1,
            "metadata": "{}", "indexed_at": 0.0,
        } if populated == "content" else {
            "chunk_id": "fixture", "vector": [1.0, 0.0], "indexed_at": 0.0,
        }
    )
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-point-seed")
        table.add([row])
    rows_before = table.to_arrow().to_pylist()
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="empty configured"):
        initialize_component_ledger(config, store, point_scope=scope)
    with pytest.raises(ScalarIndexPreparationRequired, match="budget"):
        prepare_ingestion_indices(store, max_rows=0, point_scope=scope)
    assert snapshot(config.data_dir) == before
    prepare_ingestion_indices(store, max_rows=1, point_scope=scope)
    assert table.to_arrow().to_pylist() == rows_before
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="empty configured"):
        initialize_component_ledger(config, store, point_scope=scope)
    assert snapshot(config.data_dir) == before


@pytest.mark.parametrize("stage", ["ledger", "index"])
def test_interrupted_point_preparation_retains_existing_dirty_recovery_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "point",
        model_profiles={"small": ModelProfile("small", "fixture", dim=2)},
    )
    scope = claim().scope
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    with monkeypatch.context() as patch:
        if stage == "ledger":
            def fail_binding(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("fixture preparation interruption")

            patch.setattr(ComponentLedger, "_bind", fail_binding)
            with pytest.raises(RuntimeError, match="preparation interruption"):
                initialize_component_ledger(config, store, point_scope=scope)
            assert TABLE in store.content_store.db.list_tables().tables
            assert BINDING not in domain.receipt()
        else:
            maintenance = domain.physical_maintenance

            @contextmanager
            def interrupt(table_name: str, operation: str) -> Iterator[None]:
                with maintenance(table_name, operation):
                    yield
                    raise RuntimeError("fixture preparation interruption")

            patch.setattr(domain, "physical_maintenance", interrupt)
            with pytest.raises(RuntimeError, match="preparation interruption"):
                prepare_ingestion_indices(store, max_rows=0, point_scope=scope)
            state, _ = inspect_index(
                store.content_store.db.open_table(config.content_table), column="chunk_id",
            )
            assert state.prepared
    receipt = domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"]
    before = snapshot(config.data_dir)
    for prepare in (
        lambda: initialize_component_ledger(config, store, point_scope=scope),
        lambda: prepare_ingestion_indices(store, max_rows=0, point_scope=scope),
    ):
        with pytest.raises(SourceLayoutError, match="incomplete or changed"):
            prepare()
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
    point_receipt["point_models"] = {
        "protocol": 1, "content_table": config.content_table,
        "models": {
            key: {"table_name": profile.table_name, "dim": profile.dim}
            for key, profile in config.model_profiles.items()
        },
    }
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
