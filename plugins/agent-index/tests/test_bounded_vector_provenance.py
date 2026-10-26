"""Prepared canonical provenance is verified before actual vector effects."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.chunk_id_index import (
    ChunkIdIndexPreparationRequired,
    prepare_chunk_id_index,
)
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store


def chunk(index: int) -> Chunk:
    return Chunk(f"text {index}", f"{index}.md", "text", "markdown", 1, 1, "sample:repo")


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2, table_name="vectors_a")},
    )
    store = initialize_source_store(config)
    items = [chunk(i) for i in range(5)]
    store.upsert("a", items, np.ones((5, 2), dtype=np.float32))
    return store, items


@pytest.mark.parametrize("mode", ["bounded_provenance", "bounded_input"])
def test_preparation_is_explicit_and_only_vectors_are_written(corpus, mode: str) -> None:
    store, items = corpus
    content = store.content_store
    vectors = np.array([[42, 1]], dtype=np.float32)
    before = store.projection_snapshot()
    with pytest.raises(ChunkIdIndexPreparationRequired, match="missing"):
        store.upsert_vectors("a", items[:1], vectors, **{mode: True})
    assert store.projection_snapshot() == before
    assert store._source_domain.receipt()["state"] == "clean"
    prepare_chunk_id_index(content, max_rows=5)
    version = content._get_or_create_table().version
    assert store.upsert_vectors("a", items[:1], vectors, **{mode: True}) == 1
    assert content._get_or_create_table().version == version
    np.testing.assert_array_equal(
        store.get_vector_table("a").get_vectors([items[0].chunk_id], strict=True)[items[0].chunk_id],
        [42, 1],
    )
    assert store._source_domain.receipt()["state"] == "clean"


def test_provenance_mismatch_and_read_errors_do_not_poison_preflight(
    corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_index.store.chunk_id_index as indices

    store, items = corpus
    prepare_chunk_id_index(store.content_store, max_rows=5)
    before = store.projection_snapshot()
    for bad in (replace(items[0], language="python"), replace(items[0], source="sample:other")):
        with pytest.raises(SourceLayoutError, match="provenance"):
            store.upsert_vectors(
                "a", [bad], np.ones((1, 2), dtype=np.float32), bounded_provenance=True,
            )
        assert store.projection_snapshot() == before

    def fail(*args, **kwargs):
        raise RuntimeError("canonical read failed")

    monkeypatch.setattr(indices, "indexed_chunk_rows", fail)
    with pytest.raises(RuntimeError, match="canonical read failed"):
        store.upsert_vectors(
            "a", items[:1], np.ones((1, 2), dtype=np.float32), bounded_provenance=True,
        )
    assert store.projection_snapshot() == before
    assert store._source_domain.receipt()["state"] == "clean"


def test_intervening_content_tail_refuses_cleanly_then_can_resume(
    corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, items = corpus
    content, domain = store.content_store, store._source_domain
    prepare_chunk_id_index(content, max_rows=5)
    write, appended = domain.write, False
    vector_version = store.get_vector_table("a")._get_or_create_table().version

    @contextmanager
    def intervening_write(db) -> Iterator[None]:
        nonlocal appended
        if not appended:
            appended = True
            store.upsert_content([chunk(9)])
        with write(db):
            yield

    monkeypatch.setattr(domain, "write", intervening_write)
    vectors = np.array([[42, 1]], dtype=np.float32)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="budget"):
        store.upsert_vectors("a", items[:1], vectors, bounded_provenance=True)
    assert domain.receipt()["state"] == "clean"
    assert store.get_vector_table("a")._get_or_create_table().version == vector_version
    assert chunk(9).chunk_id in content.get_by_ids([chunk(9).chunk_id])
    prepare_chunk_id_index(content, max_rows=1)
    assert store.upsert_vectors("a", items[:1], vectors, bounded_provenance=True) == 1
    assert domain.receipt()["state"] == "clean"


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("mode", ["bounded_provenance", "bounded_input"])
def test_post_effect_errors_are_never_reclassified_as_clean_refusals(
    corpus, monkeypatch: pytest.MonkeyPatch, typed: bool, mode: str,
) -> None:
    store, items = corpus
    prepare_chunk_id_index(store.content_store, max_rows=5)
    table = store.get_vector_table("a")._get_or_create_table()
    error = ChunkIdIndexPreparationRequired if typed else RuntimeError

    def fail(*args, **kwargs):
        raise error("after vector effect")

    monkeypatch.setattr(table, "add", fail)
    with pytest.raises(error, match="after vector effect"):
        store.upsert_vectors(
            "a", items[:1], np.ones((1, 2), dtype=np.float32), **{mode: True},
        )
    receipt = store._source_domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True


def test_no_legacy_adoption_is_implied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path)
    store.register_model(ModelProfile("a", "fixture", dim=2, table_name="vectors_a"))
    with pytest.raises(ChunkIdIndexPreparationRequired, match="participating"):
        store.upsert_vectors(
            "a", [chunk(0)], np.ones((1, 2), dtype=np.float32), bounded_provenance=True,
        )
    assert store.content_store.db.list_tables().tables == []


def test_empty_and_duplicate_inputs_have_no_vector_effect(corpus) -> None:
    store, items = corpus
    before = store.projection_snapshot()
    assert store.upsert_vectors(
        "a", [], np.empty((0, 2), dtype=np.float32), bounded_provenance=True,
    ) == 0
    with pytest.raises(ValueError, match="unique"):
        store.upsert_vectors(
            "a", [items[0], items[0]], np.ones((2, 2), dtype=np.float32),
            bounded_provenance=True,
        )
    assert store.projection_snapshot() == before


def test_refusal_does_not_clear_a_callers_outer_writer_transaction(corpus) -> None:
    store, items = corpus
    with pytest.raises(ChunkIdIndexPreparationRequired, match="missing"):
        with store._source_domain.write(store.content_store.db):
            store.upsert_vectors(
                "a", items[:1], np.ones((1, 2), dtype=np.float32), bounded_provenance=True,
            )
    assert store._source_domain.receipt()["state"] == "dirty"


@pytest.mark.parametrize("field,value", [
    ("content", "text 0  "),
    ("content_hash", "different"),
    ("file_path", "different.md"),
    ("chunk_type", "function"),
    ("line_start", 9),
    ("line_end", 9),
    ("metadata", '{"state": "closed"}'),
])
def test_current_input_compares_every_canonical_field_before_vector_effects(
    corpus, field: str, value: object,
) -> None:
    store, items = corpus
    content, domain = store.content_store, store._source_domain
    identity = items[0].chunk_id
    prepare_chunk_id_index(content, max_rows=5)
    with domain.write(content.db):
        domain.effect("fixture-canonical-change")
        content._get_or_create_table().update(
            where=f"chunk_id = '{identity}'", values={field: value},
        )
    prepare_chunk_id_index(content, max_rows=1)
    before = store.projection_snapshot()
    with pytest.raises(SourceLayoutError, match="canonical embedding input"):
        store.upsert_vectors(
            "a", items[:1], np.ones((1, 2), dtype=np.float32), bounded_input=True,
        )
    assert store.projection_snapshot() == before
    assert domain.receipt()["state"] == "clean"


def test_intervening_raw_input_change_is_rechecked_under_writer_exclusion(
    corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, items = corpus
    content, domain = store.content_store, store._source_domain
    prepare_chunk_id_index(content, max_rows=5)
    write, changed = domain.write, False
    vector_version = store.get_vector_table("a")._get_or_create_table().version
    updated = replace(items[0], content=items[0].content + "  ")
    assert updated.chunk_id == items[0].chunk_id

    @contextmanager
    def intervening_write(db) -> Iterator[None]:
        nonlocal changed
        if not changed:
            changed = True
            store.upsert_content([updated])
            prepare_chunk_id_index(content, max_rows=1)
        with write(db):
            yield

    monkeypatch.setattr(domain, "write", intervening_write)
    with pytest.raises(SourceLayoutError, match="canonical embedding input"):
        store.upsert_vectors(
            "a", items[:1], np.ones((1, 2), dtype=np.float32), bounded_input=True,
        )
    assert domain.receipt()["state"] == "clean"
    assert store.get_vector_table("a")._get_or_create_table().version == vector_version
    assert content.get_by_ids([updated.chunk_id], bounded=True)[updated.chunk_id].content == updated.content
    assert store.upsert_vectors(
        "a", [updated], np.array([[42, 1]], dtype=np.float32), bounded_input=True,
    ) == 1
    assert domain.receipt()["state"] == "clean"


def test_provenance_only_default_does_not_claim_raw_input_currentness(corpus) -> None:
    store, items = corpus
    changed = replace(items[0], content=items[0].content + "  ")
    assert changed.chunk_id == items[0].chunk_id
    prepare_chunk_id_index(store.content_store, max_rows=5)
    assert store.upsert_vectors(
        "a", [changed], np.array([[42, 1]], dtype=np.float32), bounded_provenance=True,
    ) == 1
