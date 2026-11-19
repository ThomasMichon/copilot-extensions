"""Native ordering/tombstone evidence, without enabling a component executor."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion
from agent_index.store.component_ledger import (
    BINDING, TABLE, ComponentEvidence, ComponentLedger, initialize_component_ledger,
)
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import (
    LAYOUT, SourceDomain, SourceLayoutError, initialize_source_store,
)

SOURCE, PATH = "sample:chosen", "items/one.md"


@pytest.fixture
def cold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[IndexConfig, MultiModelStore]:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "owned", content_table="canonical",
        model_profiles={"first": ModelProfile("first", "fixture", dim=2, table_name="vectors")},
    )
    return config, cast("Callable[[IndexConfig], MultiModelStore]", initialize_source_store)(config)


def domain(store: MultiModelStore) -> SourceDomain:
    value = store._source_domain
    assert value is not None
    return value


def native(store: Any) -> Any:
    return store.content_store._get_or_create_table()


def write(store: MultiModelStore, *, text: str = "sample", path: str = PATH) -> None:
    chunk = Chunk(text, path, "text", "markdown", 1, 1, SOURCE, {})
    store.upsert("first", [chunk], np.ones((1, 2), dtype=np.float32))
    prepare_ingestion_indices(store, max_rows=1)


def evidence(
    config: IndexConfig, store: MultiModelStore, *, version: int = 1, deleted: bool = False,
    previous: ComponentEvidence | None = None,
) -> ComponentEvidence:
    return ComponentEvidence(
        ComponentContent(ComponentVersion(SOURCE, PATH, version, "render-v1"), f"fp-{version}", deleted),
        "account-endpoint-query", "selected-policy-v1",
        capture_file_projection(
            config, store, SOURCE, PATH, max_rows=2,
            retained_chunk_ids=previous.projection.vector_chunk_ids if previous else (),
        ),
    )


def put(
    ledger: ComponentLedger, store: MultiModelStore, record: ComponentEvidence,
    previous: ComponentEvidence | None = None,
) -> bool:
    with domain(store).write(store.content_store.db):
        return ledger.put(record, expected=previous, max_rows=2)


def test_native_metadata_index_updates_and_reopen_retain_exact_evidence(
    cold: tuple[IndexConfig, MultiModelStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    write(store)
    first = evidence(config, store)
    binding = domain(store).receipt()[BINDING]

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("ordinary point evidence counted the corpus")

    monkeypatch.setattr(type(native(store)), "count_rows", forbidden)
    assert put(ledger, store, first)
    assert ledger.get(SOURCE, PATH) == first
    assert domain(store).receipt()[BINDING]["identity"] == binding["identity"]
    assert domain(store).receipt()[BINDING]["version"] > binding["version"]
    reopened = MultiModelStore(config.lance_dir, content_table="canonical")
    reopened.register_model(config.model_profiles["first"])
    assert ComponentLedger(config, reopened).get(SOURCE, PATH) == first
    assert initialize_component_ledger(config, reopened).get(SOURCE, PATH) == first


def test_stale_duplicate_equal_conflicts_and_scope_changes_preserve_order(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    write(store)
    newest = evidence(config, store, version=3)
    assert put(ledger, store, newest)
    before = domain(store).receipt()[BINDING]
    assert not put(ledger, store, newest, newest)
    assert not put(ledger, store, evidence(config, store, version=1), newest)
    assert domain(store).receipt()[BINDING] == before
    with domain(store).write(store.content_store.db):
        with pytest.raises(ValueError, match="Equal component versions"):
            ledger.put(
                replace(newest, content=replace(newest.content, fingerprint="conflicting")),
                expected=newest, max_rows=2,
            )
        with pytest.raises(SourceLayoutError, match="scope or policy"):
            ledger.put(replace(newest, scope="other-account"), expected=newest, max_rows=2)
        with pytest.raises(SourceLayoutError, match="prior record changed"):
            ledger.put(newest, expected=None, max_rows=2)
    assert ledger.get(SOURCE, PATH) == newest


def test_raw_point_drift_cannot_be_blessed_by_a_duplicate_version(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    write(store)
    first = evidence(config, store)
    put(ledger, store, first)
    write(store, text="replacement")
    with domain(store).write(store.content_store.db):
        with pytest.raises(SourceLayoutError, match="current native point"):
            ledger.put(first, expected=first, max_rows=2)


def test_newer_evidence_replaces_one_point_and_preserves_prior_vector_ids(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    write(store)
    first = evidence(config, store)
    put(ledger, store, first)
    with domain(store).write(store.content_store.db):
        store.delete_by_file(SOURCE, PATH)
        write(store, text="replacement")
        second = evidence(config, store, version=2, previous=first)
        assert ledger.put(second, expected=first, max_rows=2)
    assert set(first.projection.vector_chunk_ids) < set(second.projection.vector_chunk_ids)
    assert ledger.get(SOURCE, PATH) == second


def test_deletion_retains_ids_and_order_after_all_native_content_is_removed(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    write(store)
    first = evidence(config, store)
    put(ledger, store, first)
    store.delete_by_file(SOURCE, PATH)
    prepare_ingestion_indices(store, max_rows=0)
    tombstone = evidence(config, store, version=2, deleted=True, previous=first)
    assert tombstone.projection.vector_chunk_ids
    assert json.loads(tombstone.projection.content_rows) == []
    assert put(ledger, store, tombstone, first)
    assert ledger.get(SOURCE, PATH) == tombstone
    assert not put(ledger, store, first, tombstone)
    assert ledger.get(SOURCE, PATH) == tombstone


def test_deletion_cannot_omit_retained_vectors_or_claim_present_content(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    write(store)
    first = evidence(config, store)
    put(ledger, store, first)
    with domain(store).write(store.content_store.db):
        with pytest.raises(SourceLayoutError, match="still has native"):
            ledger.put(
                evidence(config, store, version=2, deleted=True, previous=first),
                expected=first, max_rows=2,
            )
        domain(store).effect("fixture-content-delete")
        native(store).delete("true")
    prepare_ingestion_indices(store, max_rows=0)
    with domain(store).write(store.content_store.db):
        with pytest.raises(SourceLayoutError, match="current native point"):
            ledger.put(
                evidence(config, store, version=2, deleted=True), expected=first, max_rows=2,
            )
        with pytest.raises(SourceLayoutError, match="still has native"):
            ledger.put(
                evidence(config, store, version=2, deleted=True, previous=first),
                expected=first, max_rows=2,
            )


@pytest.mark.parametrize("damage", ["drop", "rows", "recreate"])
def test_missing_history_is_not_blessed_by_another_clean_participating_write(
    cold: tuple[IndexConfig, MultiModelStore], damage: str,
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    prepare_ingestion_indices(store, max_rows=0)
    put(ledger, store, evidence(config, store, deleted=True))
    db = store.content_store.db
    with domain(store).write(db):
        table = db.open_table(TABLE)
        schema = table.schema
        domain(store).effect("fixture-ledger-damage")
        if damage == "rows":
            table.delete("true")
        else:
            db.drop_table(TABLE)
            if damage == "recreate":
                db.create_table(TABLE, schema=schema)
    write(store, path="unrelated.md")
    assert domain(store).receipt()["state"] == "clean"
    with pytest.raises(SourceLayoutError, match="missing or incompatible|identity or version"):
        ledger.get(SOURCE, PATH)
    with pytest.raises(SourceLayoutError):
        initialize_component_ledger(config, store)


def test_bootstrap_refuses_existing_data_and_orphan_native_table_without_effects(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    write(store)
    before = domain(store).receipt()
    with pytest.raises(SourceLayoutError, match="empty configured"):
        initialize_component_ledger(config, store)
    assert domain(store).receipt() == before
    store.delete_by_file(SOURCE, PATH)
    initialize_component_ledger(config, store)
    value = domain(store).receipt()
    value.pop(BINDING)
    domain(store).save(value)
    before = domain(store).receipt()
    with pytest.raises(SourceLayoutError, match="missing or incompatible"):
        initialize_component_ledger(config, store)
    assert domain(store).receipt() == before


@pytest.mark.parametrize("damage", [None, "missing-ledger", "dirty"])
def test_coherent_fixture_directory_restore_is_portable_but_incomplete_copy_refuses(
    cold: tuple[IndexConfig, MultiModelStore], tmp_path: Path, damage: str | None,
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    prepare_ingestion_indices(store, max_rows=0)
    tombstone = evidence(config, store, deleted=True)
    put(ledger, store, tombstone)
    restored_config = replace(config, data_dir=tmp_path / "restored")
    with domain(store).operation(store.content_store.db):
        shutil.copytree(config.lance_dir, restored_config.lance_dir)
    if damage == "missing-ledger":
        # A deliberately incomplete synthetic copy; no live backup is touched.
        shutil.rmtree(restored_config.lance_dir / f"{TABLE}.lance")
    elif damage == "dirty":
        marker = restored_config.lance_dir / ".source-layout.json"
        marker.write_text(json.dumps({**json.loads(marker.read_text()), "state": "dirty"}))
    restored = MultiModelStore(restored_config.lance_dir, content_table="canonical")
    restored.register_model(restored_config.model_profiles["first"])
    if damage:
        with pytest.raises(SourceLayoutError):
            ComponentLedger(restored_config, restored).get(SOURCE, PATH)
    else:
        assert ComponentLedger(restored_config, restored).get(SOURCE, PATH) == tombstone


def test_ledger_requires_writer_and_native_query_is_indexed_and_finite(
    cold: tuple[IndexConfig, MultiModelStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    prepare_ingestion_indices(store, max_rows=0)
    first = evidence(config, store, deleted=True)
    with pytest.raises(SourceLayoutError, match="owning source writer"):
        ledger.put(first, expected=None, max_rows=2)
    put(ledger, store, first)
    query_type = type(native(store).search())
    original = query_type.to_list
    plans: list[str] = []

    def observe(query: Any, *args: Any, **kwargs: Any) -> Any:
        plans.append(query.analyze_plan())
        return original(query, *args, **kwargs)

    monkeypatch.setattr(query_type, "to_list", observe)
    assert ledger.get(SOURCE, PATH) == first
    assert len(plans) == 1 and "ScalarIndexQuery" in plans[0], plans
    assert sum(map(int, re.findall(r"\brows_scanned=(\d+)", plans[0]))) == 1, plans


def test_unknown_binding_is_a_read_only_refusal_not_cold_bootstrap(
    cold: tuple[IndexConfig, MultiModelStore],
) -> None:
    config, store = cold
    value = domain(store).receipt()
    value[BINDING] = None
    domain(store).save(value)
    with pytest.raises(SourceLayoutError, match="missing or incompatible"):
        initialize_component_ledger(config, store)
    assert domain(store).receipt() == value
    assert TABLE not in store.content_store.db.list_tables().tables


def test_failed_binding_publication_leaves_native_effect_dirty_and_uncertain(
    cold: tuple[IndexConfig, MultiModelStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = cold
    ledger = initialize_component_ledger(config, store)
    prepare_ingestion_indices(store, max_rows=0)
    first = evidence(config, store, deleted=True)
    before = domain(store).receipt()[BINDING]

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("fixture receipt publication failure")

    monkeypatch.setattr(ledger, "_bind", broken)
    with pytest.raises(OSError, match="publication failure"):
        put(ledger, store, first)
    receipt = domain(store).receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"]
    assert receipt[BINDING] == before
    with pytest.raises(SourceLayoutError, match="incomplete or changed"):
        ledger.get(SOURCE, PATH)
