"""Selected native capacity and cleanup preserve neighboring rows and ordering evidence."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion
from agent_index.indexing.point_hints import PointBinding
from agent_index.store import point_reconciliation as cleanup
from agent_index.store.component_ledger import BINDING, TABLE, ComponentEvidence, initialize_component_ledger
from agent_index.store.content_mutations import content_record
from agent_index.store.file_projection import capture_file_projection, require_file_projection_capacity
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import MAX_SEGMENTS, ScalarIndexPreparationRequired, inspect_index
from agent_index.store.source_layout import (
    LAYOUT, PointScope, SourceDomain, SourceLayoutError, initialize_source_store,
)

SCOPE: PointScope = (
    "sample:components", "sample:points'family", "account",
    PointBinding("https://example.test/project", "union", "context"),
)
PATH = "one's.md"


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def domain(store: MultiModelStore) -> SourceDomain:
    owner = store._source_domain
    assert owner is not None
    return owner


@pytest.fixture
def selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[IndexConfig, MultiModelStore, list[Chunk]]:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path / "store", content_table="canonical", model_profiles={
        name: ModelProfile(name, "fixture", dim=dim, table_name=f"vectors_{name}")
        for name, dim in (("first", 2), ("second", 3))
    })
    store = initialize_source_store(config, point_scope=SCOPE)
    ledger = initialize_component_ledger(config, store, point_scope=SCOPE)
    chunks = [
        Chunk("keep", PATH, "text", "markdown", 1, 1, SCOPE[1]),
        Chunk("stale", PATH, "text", "markdown", 2, 2, SCOPE[1]),
        Chunk("other-family", PATH, "text", "markdown", 1, 1, "sample:other"),
        Chunk("other-path", "sibling.md", "text", "markdown", 1, 1, SCOPE[1]),
        Chunk("remnant", PATH, "text", "markdown", 3, 3, SCOPE[1]),
    ]
    with domain(store)._write(store.content_store.db, point_scope=SCOPE):
        domain(store).effect("fixture-seed")
        store.content_store._get_or_create_table().add([
            {**content_record(chunk), "indexed_at": 0.0} for chunk in chunks[:-1]
        ])
        for model in store.model_ids:
            vector = store.get_vector_table(model)
            vector._get_or_create_table().add([
                {"chunk_id": chunk.chunk_id, "vector": [1.0] * vector.dim, "indexed_at": 0.0,
                 "source": chunk.source, "language": chunk.language} for chunk in chunks
            ])
    prepare_ingestion_indices(store, max_rows=len(chunks), point_scope=SCOPE)
    evidence = ComponentEvidence(
        ComponentContent(ComponentVersion(SCOPE[1], PATH, 1, "render"), "fp", False),
        "account-endpoint-query", "selected-policy",
        capture_file_projection(config, store, SCOPE[1], PATH, max_rows=4, point_scope=SCOPE),
    )
    with domain(store)._write(store.content_store.db, point_scope=SCOPE):
        assert ledger.put(evidence, expected=None, max_rows=4, point_scope=SCOPE)
    return config, store, chunks


def reconcile(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], **kwargs: Any,
) -> int:
    config, store, chunks = selected
    return cleanup.reconcile_point_file(
        config, store, SCOPE[1], PATH, (chunks[0].chunk_id,),
        max_rows=kwargs.pop("max_rows", 4),
        retained_chunk_ids=kwargs.pop("retained_chunk_ids", (chunks[-1].chunk_id,)),
        point_scope=SCOPE, **kwargs,
    )


def test_point_capacity_uses_qualified_indexed_paths_without_changing_caches_or_rows(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, _ = selected
    owner = domain(store)
    store = MultiModelStore(owner.path, content_table="canonical", point_scope=SCOPE)
    db = store.content_store.db
    table = db.open_table("canonical")
    store.content_store._table = db.open_table("vectors_second")
    query_type = type(table.search())
    original = query_type.to_list
    plans: list[str] = []

    def observe(query: Any, *args: Any, **kwargs: Any) -> Any:
        plans.append(query.analyze_plan())
        return original(query, *args, **kwargs)

    with owner._write(db, point_scope=SCOPE):
        before = snapshot(owner.path)
        with monkeypatch.context() as patch:
            patch.setattr(query_type, "to_list", observe)
            require_file_projection_capacity(
                store, {(SCOPE[1], PATH): 2, (SCOPE[1], "new.md"): 3,
                        (SCOPE[1], "sibling.md"): 0}, max_rows=3, point_scope=SCOPE,
            )
        cached: Any = store.content_store._table
        assert cached.name == "vectors_second"
        assert snapshot(owner.path) == before
    assert len(plans) == 3
    for plan in plans:
        scanned = re.findall(r"\brows_scanned=(\d+)", plan)
        assert "ScalarIndexQuery" in plan and scanned and sum(map(int, scanned)) <= 3, plan


@pytest.mark.parametrize("replacements,max_rows", [
    ({(SCOPE[1], PATH): 3}, 3),  # The unselected family still occupies one row.
    ({(SCOPE[1], "new.md"): 4}, 3),
    ({(SCOPE[1], PATH): 0}, 2),  # Current candidates are bounded even for deletion.
])
def test_point_capacity_refuses_projected_or_current_path_overflow_without_effects(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]],
    replacements: dict[tuple[str, str], int], max_rows: int,
) -> None:
    _, store, _ = selected
    owner = domain(store)
    with owner._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(owner.path)
        with pytest.raises(ScalarIndexPreparationRequired, match="candidate budget"):
            require_file_projection_capacity(
                store, replacements, max_rows=max_rows, point_scope=SCOPE,
            )
        assert snapshot(owner.path) == before


@pytest.mark.parametrize("obstruction", [
    "no-writer", "omitted-scope", "scope", "family", "store-path", "connection", "ledger-binding",
    "missing-index", "tail", "segment",
])
def test_point_capacity_refuses_unqualified_owners_and_unprepared_path_index(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]],
    monkeypatch: pytest.MonkeyPatch, obstruction: str,
) -> None:
    from agent_index.store import file_projection

    _, store, chunks = selected
    owner = domain(store)
    db = store.content_store.db
    if obstruction == "no-writer":
        before = snapshot(owner.path)
        with pytest.raises(SourceLayoutError, match="owning source writer"):
            require_file_projection_capacity(store, {}, max_rows=3, point_scope=SCOPE)
        assert snapshot(owner.path) == before
        return
    with owner._write(db, point_scope=SCOPE):
        replacements = {(SCOPE[1], PATH): 2}
        scope: PointScope | None = SCOPE
        if obstruction == "omitted-scope":
            scope = None
        elif obstruction == "scope":
            scope = (SCOPE[0], SCOPE[1], "other-account", SCOPE[3])
        elif obstruction == "family":
            replacements = {("sample:other", PATH): 0}
        elif obstruction == "store-path":
            monkeypatch.setattr(store, "_db_path", owner.path / "foreign")
        elif obstruction == "connection":
            monkeypatch.setattr(owner._local, "db", object())
        elif obstruction == "ledger-binding":
            receipt = owner.receipt()
            receipt[BINDING]["version"] += 1
            owner.save(receipt)
        elif obstruction in ("missing-index", "tail"):
            owner.effect("fixture-path-index-drift")
            table = db.open_table("canonical")
            if obstruction == "missing-index":
                table.drop_index("file_path_idx")
            else:
                table.add([{**content_record(chunks[0]), "indexed_at": 1.0}])
        elif obstruction == "segment":
            def exhausted(table: Any, *, column: str) -> Any:
                state, dataset = inspect_index(table, column=column)
                return replace(state, segments=MAX_SEGMENTS + 1), dataset

            monkeypatch.setattr(file_projection, "inspect_index", exhausted)
        before = snapshot(owner.path)
        with pytest.raises((ScalarIndexPreparationRequired, SourceLayoutError)):
            require_file_projection_capacity(
                store, replacements, max_rows=3, point_scope=scope,
            )
        assert snapshot(owner.path) == before


def test_point_capacity_empty_deletions_and_noops_need_no_reserved_segment(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.store import file_projection

    _, store, _ = selected
    owner = domain(store)

    def at_capacity(table: Any, *, column: str) -> Any:
        state, dataset = inspect_index(table, column=column)
        return replace(state, segments=MAX_SEGMENTS), dataset

    monkeypatch.setattr(file_projection, "inspect_index", at_capacity)
    with owner._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(owner.path)
        for replacements in ({}, {(SCOPE[1], PATH): 0}, {(SCOPE[1], PATH): 2}):
            require_file_projection_capacity(store, replacements, max_rows=3, point_scope=SCOPE)
        assert snapshot(owner.path) == before


def test_cleanup_uses_exact_qualified_handles_and_bounded_indexed_raw_points(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, chunks = selected
    owner = domain(store)
    db = store.content_store.db
    ledger = snapshot(owner.path / f"{TABLE}.lance")
    table = db.open_table("canonical")
    query_type = type(table.search())
    original = query_type.to_list
    plans: list[str] = []

    def observe(query: Any, *args: Any, **kwargs: Any) -> Any:
        plans.append(query.analyze_plan())
        return original(query, *args, **kwargs)

    store.content_store._table = db.open_table("vectors_second")
    for model in store.model_ids:
        store.get_vector_table(model)._table = table
    with owner._write(db, point_scope=SCOPE):
        with monkeypatch.context() as patch:
            patch.setattr(query_type, "to_list", observe)
            assert reconcile(selected) == 2
        cached: Any = store.content_store._table
        assert cached.name == "vectors_second"
        for model in store.model_ids:
            cached = store.get_vector_table(model)._table
            assert cached.name == "canonical"
    assert len(plans) == 4
    for plan in plans:
        scanned = re.findall(r"\brows_scanned=(\d+)", plan)
        assert "ScalarIndexQuery" in plan and scanned and sum(map(int, scanned)) <= 3, plan
    expected = {chunk.chunk_id for chunk in (chunks[0], chunks[2], chunks[3])}
    for name in ("canonical", "vectors_first", "vectors_second"):
        assert {row["chunk_id"] for row in db.open_table(name).search().limit(8).to_list()} == expected
    assert snapshot(owner.path / f"{TABLE}.lance") == ledger
    assert owner.receipt()["state"] == "clean"


def test_empty_and_kept_points_are_byte_stable_and_do_not_count_absent_retained_ids(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store, chunks = selected
    owner = domain(store)

    def at_capacity(table: Any, *, column: str) -> Any:
        state, dataset = inspect_index(table, column=column)
        return replace(state, segments=MAX_SEGMENTS), dataset

    monkeypatch.setattr(cleanup, "inspect_index", at_capacity)
    with owner._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(owner.path)
        assert cleanup.reconcile_point_file(
            config, store, SCOPE[1], PATH, tuple(chunk.chunk_id for chunk in chunks[:2]),
            max_rows=4, retained_chunk_ids=("absent",), point_scope=SCOPE,
        ) == 0
        assert cleanup.reconcile_point_file(
            config, store, SCOPE[1], "empty.md", (), max_rows=1,
            retained_chunk_ids=("absent",), point_scope=SCOPE,
        ) == 0
        assert snapshot(owner.path) == before


@pytest.mark.parametrize("obstruction", [
    "path-budget", "union-budget", "missing-index", "tail", "segment",
    "other-family", "other-path", "ledger-binding",
])
def test_all_model_preflight_refuses_before_any_cleanup_effect(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]],
    monkeypatch: pytest.MonkeyPatch, obstruction: str,
) -> None:
    _, store, chunks = selected
    owner = domain(store)
    db = store.content_store.db
    with owner._write(db, point_scope=SCOPE):
        kwargs: dict[str, Any] = {}
        if obstruction == "path-budget":
            kwargs["max_rows"] = 2
        elif obstruction == "union-budget":
            kwargs.update(max_rows=3, retained_chunk_ids=(chunks[-1].chunk_id, "absent"))
        elif obstruction in ("other-family", "other-path"):
            kwargs["retained_chunk_ids"] = (chunks[2 if obstruction == "other-family" else 3].chunk_id,)
        elif obstruction == "ledger-binding":
            receipt = owner.receipt()
            receipt[BINDING]["version"] += 1
            owner.save(receipt)
        elif obstruction in ("missing-index", "tail"):
            owner.effect("fixture-index-drift")
            table = db.open_table("vectors_second")
            if obstruction == "missing-index":
                table.drop_index("chunk_id_idx")
            else:
                table.add([{
                    "chunk_id": "tail", "vector": [0.0] * 3, "indexed_at": 0.0,
                    "source": SCOPE[1], "language": "markdown",
                }])
        elif obstruction == "segment":
            def exhausted(table: Any, *, column: str) -> Any:
                state, dataset = inspect_index(table, column=column)
                return (
                    replace(state, segments=MAX_SEGMENTS) if table.name == "vectors_second" else state,
                    dataset,
                )

            monkeypatch.setattr(cleanup, "inspect_index", exhausted)
        before = snapshot(owner.path)
        with pytest.raises((ScalarIndexPreparationRequired, SourceLayoutError)):
            reconcile(selected, **kwargs)
        assert snapshot(owner.path) == before


def test_caught_native_failure_latches_uncertainty_and_never_deletes_canonical_first(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, chunks = selected
    owner = domain(store)
    db = store.content_store.db
    table_type = type(db.open_table("canonical"))
    delete = table_type.delete
    events: list[str] = []

    def fail_second(table: Any, predicate: str) -> None:
        events.append(table.name)
        if table.name == "vectors_second":
            raise RuntimeError("native second-model failure")
        delete(table, predicate)

    monkeypatch.setattr(table_type, "delete", fail_second)
    with pytest.raises(SourceLayoutError, match="failed nested effect"):
        with owner._write(db, point_scope=SCOPE):
            with pytest.raises(RuntimeError, match="second-model failure"):
                reconcile(selected)
    assert events == ["vectors_first", "vectors_second"]
    assert {row["chunk_id"] for row in db.open_table("canonical").search().limit(8).to_list()} == {
        chunk.chunk_id for chunk in chunks[:-1]
    }
    assert owner.receipt()["state"] == "dirty" and owner.receipt()["uncertain_effect"] is True


@pytest.mark.parametrize("obstruction", ["family", "scope", "model", "kept-duplicate", "budget-boolean"])
def test_invalid_selection_refuses_without_effects(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]], obstruction: str,
) -> None:
    config, store, chunks = selected
    owner = domain(store)
    source = "sample:other" if obstruction == "family" else SCOPE[1]
    scope = (*SCOPE[:2], "other-account", SCOPE[3]) if obstruction == "scope" else SCOPE
    keep = (chunks[0].chunk_id,) * (2 if obstruction == "kept-duplicate" else 1)
    if obstruction == "model":
        config.model_profiles["second"] = replace(config.model_profiles["second"], dim=4)
    with owner._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(owner.path)
        with pytest.raises((ValueError, SourceLayoutError)):
            cleanup.reconcile_point_file(
                config, store, source, PATH, keep, max_rows=True if obstruction == "budget-boolean" else 4,
                point_scope=scope,
            )
        assert snapshot(owner.path) == before


def test_cleanup_requires_existing_selected_writer_and_keeps_ordinary_frontdoor_refused(
    selected: tuple[IndexConfig, MultiModelStore, list[Chunk]],
) -> None:
    _, store, chunks = selected
    with pytest.raises(SourceLayoutError, match="owning source writer"):
        reconcile(selected)
    owner = domain(store)
    with owner._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(owner.path)
        with pytest.raises(SourceLayoutError):
            store.reconcile_file_chunks(SCOPE[1], PATH, {chunks[0].chunk_id})
        assert snapshot(owner.path) == before
