"""Canonical point writes coexist only with qualified native ordering evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion
from agent_index.indexing.point_hints import PointBinding
from agent_index.store.component_ledger import (
    BINDING, TABLE, ComponentEvidence, initialize_component_ledger,
)
from agent_index.store.content_mutations import content_record
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired
from agent_index.store.source_layout import (
    LAYOUT, PointScope, SourceLayoutError, initialize_source_store,
)

SCOPE: PointScope = (
    "sample:components", "sample:points", "account",
    PointBinding("https://example.test/project", "union", "context"),
)


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MultiModelStore, Chunk]:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path / "store", content_table="canonical", model_profiles={
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("first", "second")
    })
    store = initialize_source_store(config, point_scope=SCOPE)
    ledger = initialize_component_ledger(config, store, point_scope=SCOPE)
    domain = store._source_domain
    assert domain is not None
    chunk = Chunk("sample", "one.md", "text", "markdown", 1, 1, SCOPE[1])
    with domain._write(store.content_store.db, point_scope=SCOPE):
        domain.effect("fixture-seed")
        store.content_store._get_or_create_table().add([{**content_record(chunk), "indexed_at": 0.0}])
        for model in store.model_ids:
            store.get_vector_table(model)._get_or_create_table().add([{
                "chunk_id": chunk.chunk_id, "vector": [1.0, 0.0], "indexed_at": 0.0,
                "source": chunk.source, "language": chunk.language,
            }])
    prepare_ingestion_indices(store, max_rows=1, point_scope=SCOPE)
    evidence = ComponentEvidence(
        ComponentContent(ComponentVersion(chunk.source, chunk.file_path, 1, "render"), "fp", False),
        "account-endpoint-query", "selected-policy",
        capture_file_projection(config, store, chunk.source, chunk.file_path, max_rows=1, point_scope=SCOPE),
    )
    with domain._write(store.content_store.db, point_scope=SCOPE):
        assert ledger.put(evidence, expected=None, max_rows=1, point_scope=SCOPE)
    return store, chunk


def test_selected_content_preserves_existing_ledger_across_noops_and_writes(
    selected: tuple[MultiModelStore, Chunk],
) -> None:
    store, chunk = selected
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    binding = domain.receipt()[BINDING]
    ledger = snapshot(domain.path / f"{TABLE}.lance")
    assert ledger
    changed = replace(chunk, language="python", metadata={"changed": True})
    with domain._write(db, point_scope=SCOPE):
        before = snapshot(domain.path)
        assert store.upsert_point_content([], point_scope=SCOPE) == 0
        assert store.upsert_point_content([chunk], point_scope=SCOPE) == 0
        assert snapshot(domain.path) == before
        assert store.upsert_point_content([changed], point_scope=SCOPE) == 1
        prepare_ingestion_indices(store, max_rows=1, point_scope=SCOPE)
        before = snapshot(domain.path)
        assert store.upsert_point_content([changed], point_scope=SCOPE) == 0
        assert snapshot(domain.path) == before
    assert domain.receipt()[BINDING] == binding
    assert snapshot(domain.path / f"{TABLE}.lance") == ledger
    assert domain.receipt()["state"] == "clean"
    assert db.open_table("canonical").search().limit(2).to_list()[0]["metadata"] == '{"changed": true}'
    for model in store.model_ids:
        rows = db.open_table(store.get_vector_table(model).table_name).search().limit(2).to_list()
        assert len(rows) == 1
        assert rows[0]["language"] == "python" and rows[0]["vector"] == [1.0, 0.0]


@pytest.mark.parametrize("obstruction", [
    "missing-binding", "version", "identity", "schema", "missing-index", "tail",
    "missing-table", "unknown-table", "missing-inventory",
])
def test_selected_content_refuses_unqualified_ledger_before_any_effect(
    selected: tuple[MultiModelStore, Chunk], obstruction: str,
) -> None:
    store, chunk = selected
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    with domain._write(db, point_scope=SCOPE):
        receipt = domain.receipt()
        if obstruction == "missing-binding":
            del receipt[BINDING]
        elif obstruction == "version":
            receipt[BINDING]["version"] += 1
        elif obstruction == "identity":
            receipt[BINDING]["identity"] = "0" * 32
        elif obstruction == "missing-inventory":
            del receipt["versions"][TABLE]
        else:
            domain.effect("fixture-ledger-drift")
            table: Any = db.open_table(TABLE)
            if obstruction == "schema":
                table.add_columns({"extra": "'incompatible'"})
            elif obstruction == "missing-index":
                table.drop_index("point_key_idx")
            elif obstruction == "tail":
                table.add([{"point_key": "unindexed", "record": b"{}"}])
            elif obstruction == "missing-table":
                db.drop_table(TABLE)
            elif obstruction == "unknown-table":
                db.create_table("unbound_auxiliary", schema=table.schema)
            if obstruction in ("schema", "missing-index", "tail"):
                receipt[BINDING]["version"] = table.version
        domain.save(receipt)
        before = snapshot(domain.path)
        for chunks in ([], [replace(chunk, language="python")]):
            with pytest.raises((ScalarIndexPreparationRequired, SourceLayoutError)):
                store.upsert_point_content(chunks, point_scope=SCOPE)
            assert snapshot(domain.path) == before
