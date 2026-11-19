"""Native point-witness controls; no receiver, acquisition or live store."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.content_mutations import content_record
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired
from agent_index.store.source_layout import (
    LAYOUT, SourceDomain, SourceLayoutError, initialize_source_store,
)

SOURCE = "sample:chosen'point"
PATH = "items/one's.md"


@pytest.fixture
def cold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[IndexConfig, MultiModelStore]:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "owned", content_table="canonical",
        model_profiles={
            "first": ModelProfile("first", "fixture-a", dim=2, table_name="vectors_first"),
            "second": ModelProfile("second", "fixture-b", dim=3, table_name="vectors_second"),
        },
    )
    return config, cast("Callable[[IndexConfig], MultiModelStore]", initialize_source_store)(config)


def native(owner: Any) -> Any:
    return owner._get_or_create_table()


def domain(store: MultiModelStore) -> SourceDomain:
    result = store._source_domain
    assert result is not None
    return result


def item(*, source: str = SOURCE, path: str = PATH, text: str = "raw\r\ntext ") -> Chunk:
    return Chunk(text, path, "text", "markdown", 1, 2, source, {"nested": {"value": 7}})


def write(store: MultiModelStore, chunks: list[Chunk]) -> None:
    for model in store.model_ids:
        dim = store.get_vector_table(model).dim
        store.upsert(model, chunks, np.ones((len(chunks), dim), dtype=np.float32))
    prepare_ingestion_indices(store, max_rows=len(chunks))


def test_raw_snapshot_is_scoped_complete_immutable_and_read_only(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    target, sibling = item(), item(source="sample:neighbor", text="unselected")
    write(store, [target, sibling])
    before = domain(store).receipt()
    result = capture_file_projection(config, store, SOURCE, PATH, max_rows=2)
    assert result.domain == before["domain"] and result.content_table == "canonical"
    rows = json.loads(result.content_rows)
    assert len(rows) == 1
    assert rows[0] == native(store.content_store).search().where(
        f"chunk_id = '{target.chunk_id}'",
    ).limit(2).to_list()[0]
    assert rows[0]["content"] == target.content
    assert json.loads(rows[0]["metadata"]) == target.metadata
    assert result.vector_chunk_ids == (target.chunk_id,)
    assert [(model.model_id, model.table_name, model.dimensions) for model in result.models] == [
        ("first", "vectors_first", 2), ("second", "vectors_second", 3),
    ]
    for model in result.models:
        vectors = json.loads(model.rows)
        assert len(vectors) == 1 and vectors[0]["source"] == SOURCE
        assert vectors[0]["language"] == target.language
        assert len(vectors[0]["vector"]) == model.dimensions
    assert capture_file_projection(config, store, SOURCE, PATH, max_rows=2) == result
    assert domain(store).receipt() == before


def test_same_path_candidates_are_capped_before_source_filtering(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    write(store, [item(), item(source="sample:neighbor")])
    before = domain(store).receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="path candidate budget"):
        capture_file_projection(config, store, SOURCE, PATH, max_rows=1)
    assert domain(store).receipt() == before


def test_empty_point_and_unrelated_commits_do_not_invent_point_changes(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    prepare_ingestion_indices(store, max_rows=0)
    empty = capture_file_projection(config, store, SOURCE, PATH, max_rows=1)
    assert json.loads(empty.content_rows) == []
    assert empty.vector_chunk_ids == ()
    assert all(json.loads(model.rows) == [] for model in empty.models)
    write(store, [item()])
    before = capture_file_projection(config, store, SOURCE, PATH, max_rows=1)
    write(store, [item(source="sample:unrelated", path="elsewhere.md")])
    assert capture_file_projection(config, store, SOURCE, PATH, max_rows=1) == before


@pytest.mark.parametrize("missing", ["file_path_idx", "chunk_id_idx", "vector"])
def test_all_indices_are_checked_before_any_point_query(
    cold: tuple[IndexConfig, MultiModelStore], monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    config, store = cold
    write(store, [item()])
    owner = store.get_vector_table("second") if missing == "vector" else store.content_store
    table = native(owner)
    with domain(store).write(store.content_store.db):
        domain(store).effect("fixture-drop-index")
        table.drop_index("chunk_id_idx" if missing == "vector" else missing)
    before = domain(store).receipt()

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unprepared capture queried data")

    monkeypatch.setattr(type(table), "search", forbidden)
    with pytest.raises(ScalarIndexPreparationRequired, match="prepared"):
        capture_file_projection(config, store, SOURCE, PATH, max_rows=2)
    assert domain(store).receipt() == before


def test_unindexed_vector_tail_refuses_without_preparation_or_write(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    write(store, [item()])
    store.upsert("second", [item(path="other.md")], np.ones((1, 3), dtype=np.float32))
    before = domain(store).receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="physical rows"):
        capture_file_projection(config, store, SOURCE, PATH, max_rows=2)
    assert domain(store).receipt() == before


@pytest.mark.parametrize("kind", ["content", "vector"])
def test_duplicate_native_rows_are_not_a_successful_witness(
    cold: tuple[IndexConfig, MultiModelStore], kind: str,
) -> None:
    config, store = cold
    write(store, [item()])
    owner = store.content_store if kind == "content" else store.get_vector_table("first")
    table = native(owner)
    row = table.search().limit(1).to_list()[0]
    with domain(store).write(store.content_store.db):
        domain(store).effect("fixture-duplicate")
        table.add([row])
    prepare_ingestion_indices(store, max_rows=1)
    with pytest.raises((RuntimeError, SourceLayoutError), match="duplicate"):
        capture_file_projection(config, store, SOURCE, PATH, max_rows=2)


def test_retained_ids_cannot_capture_another_canonical_point(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    other = item(path="unrelated.md")
    write(store, [item(), other])
    with pytest.raises(SourceLayoutError, match="different canonical input"):
        capture_file_projection(
            config, store, SOURCE, PATH, max_rows=2, retained_chunk_ids=(other.chunk_id,),
        )


def test_native_point_plans_use_indices_with_a_large_unrelated_base(
    cold: tuple[IndexConfig, MultiModelStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = cold
    chunks = [item(), *(item(path=f"base/{n}.md", text=str(n)) for n in range(10_000))]
    with domain(store).write(store.content_store.db):
        domain(store).effect("fixture-base")
        native(store.content_store).add([
            {**content_record(chunk), "indexed_at": 1.0} for chunk in chunks
        ])
        for model in store.model_ids:
            owner = store.get_vector_table(model)
            native(owner).add([
                {"chunk_id": chunk.chunk_id, "source": chunk.source, "language": chunk.language,
                 "vector": [1.0] * owner.dim, "indexed_at": 1.0}
                for chunk in chunks
            ])
    prepare_ingestion_indices(store, max_rows=len(chunks))
    query_type = type(native(store.content_store).search())
    original = query_type.to_list
    plans: list[str] = []

    def observe(query: Any, *args: Any, **kwargs: Any) -> Any:
        plans.append(query.analyze_plan())
        return original(query, *args, **kwargs)

    monkeypatch.setattr(query_type, "to_list", observe)
    result = capture_file_projection(config, store, SOURCE, PATH, max_rows=1)
    assert len(json.loads(result.content_rows)) == 1
    assert len(plans) == 4  # File candidates, canonical IDs, two registered models.
    for plan in plans:
        assert "ScalarIndexQuery" in plan, plan
        scanned = [int(value) for value in re.findall(r"\brows_scanned=(\d+)", plan)]
        assert scanned and sum(scanned) == 1, plan


def test_raw_metadata_changes_and_known_vector_remnants_are_not_normalized_away(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    target = item()
    write(store, [target])
    original = capture_file_projection(config, store, SOURCE, PATH, max_rows=2)
    content = native(store.content_store)
    with domain(store).write(store.content_store.db):
        domain(store).effect("fixture-raw-metadata")
        content.update(where=f"chunk_id = '{target.chunk_id}'", values={"metadata": "{invalid"})
    prepare_ingestion_indices(store, max_rows=1)
    changed = capture_file_projection(config, store, SOURCE, PATH, max_rows=2)
    assert changed != original
    assert json.loads(changed.content_rows)[0]["metadata"] == "{invalid"
    with domain(store).write(store.content_store.db):
        domain(store).effect("fixture-canonical-delete")
        content.delete(f"chunk_id = '{target.chunk_id}'")
    remnant = capture_file_projection(
        config, store, SOURCE, PATH, max_rows=2, retained_chunk_ids=(target.chunk_id,),
    )
    assert json.loads(remnant.content_rows) == []
    assert all(json.loads(model.rows)[0]["chunk_id"] == target.chunk_id for model in remnant.models)
    store.delete_by_file(SOURCE, PATH)
    # With no canonical row, ordinary file deletion cannot discover those vectors.
    assert capture_file_projection(
        config, store, SOURCE, PATH, max_rows=2, retained_chunk_ids=(target.chunk_id,),
    ) == remnant


@pytest.mark.parametrize("field,value", [
    ("source", "sample:other"), ("language", "python"), ("vector", [float("inf"), 1.0]),
])
def test_vector_provenance_and_values_are_checked_on_raw_points(
    cold: tuple[IndexConfig, MultiModelStore], field: str, value: Any,
) -> None:
    config, store = cold
    write(store, [item()])
    table = native(store.get_vector_table("first"))
    with domain(store).write(store.content_store.db):
        domain(store).effect("fixture-invalid-vector")
        if field == "vector":
            row = table.search().limit(1).to_list()[0]
            row[field] = value
            table.delete(f"chunk_id = '{item().chunk_id}'")
            table.add([row])
        else:
            table.update(where=f"chunk_id = '{item().chunk_id}'", values={field: value})
    prepare_ingestion_indices(store, max_rows=1)
    with pytest.raises(SourceLayoutError, match="vector or provenance"):
        capture_file_projection(config, store, SOURCE, PATH, max_rows=2)


def test_mapping_and_retained_point_union_must_fit_the_selected_contract(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    write(store, [item()])
    with pytest.raises(ScalarIndexPreparationRequired, match="point union budget"):
        capture_file_projection(
            config, store, SOURCE, PATH, max_rows=1, retained_chunk_ids=("absent",),
        )
    changed = replace(config.model_profiles["first"], dim=4)
    alternate = replace(config, model_profiles={**config.model_profiles, "first": changed})
    with pytest.raises(SourceLayoutError, match="model mapping"):
        capture_file_projection(alternate, store, SOURCE, PATH, max_rows=2)


def test_backend_failure_is_never_an_empty_projection(
    cold: tuple[IndexConfig, MultiModelStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = cold
    write(store, [item()])
    table = native(store.content_store)

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("fixture backend read failure")

    monkeypatch.setattr(type(table), "search", broken)
    with pytest.raises(OSError, match="backend read failure"):
        capture_file_projection(config, store, SOURCE, PATH, max_rows=2)
