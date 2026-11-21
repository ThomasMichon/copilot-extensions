"""Native bootstrap and registration retain exact point model/table ownership."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.point_hints import PointBinding
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import (
    LAYOUT, MARKER, PointScope, SourceDomain, SourceLayoutError,
    initialize_source_layout, initialize_source_store,
)

SCOPE: PointScope = (
    "sample:components", "sample:points", "account",
    PointBinding("https://example.test/project", "union", "context"),
)


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IndexConfig:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    return IndexConfig(data_dir=tmp_path / "store", model_profiles={
        "first": ModelProfile("first", "fixture", dim=2, table_name="vectors_first"),
        "second": ModelProfile("second", "fixture", dim=2, table_name="vectors_second"),
    })


def test_point_models_reopen_and_reject_swaps_and_second_facade_aliases(config: IndexConfig) -> None:
    store = initialize_source_store(config, point_scope=SCOPE)
    domain = store._source_domain
    assert domain is not None
    retained = domain.receipt()
    before = snapshot(config.data_dir)
    reopened = initialize_source_store(config, point_scope=SCOPE)
    facade = MultiModelStore(config.lance_dir, point_scope=SCOPE)
    first, second = config.model_profiles.values()
    for owner in (store, reopened, facade):
        table = owner.register_model(first)
        assert owner.register_model(replace(
            first, revision="updated-policy", model_name="new-embedding",
            engine_host="different.test", engine_port=1234, query_prefix="query:",
        )) is table
        for profile in (
            replace(first, table_name=second.table_name),
            replace(first, dim=3), replace(first, dim=cast(Any, 2.0)),
            replace(first, model_id="alias"), replace(first, table_name=config.content_table),
        ):
            with pytest.raises(SourceLayoutError, match="retained point binding"):
                owner.register_model(profile)
            assert snapshot(config.data_dir) == before
        with pytest.raises(SourceLayoutError, match="not admitted"):
            owner.upsert_vectors(first.model_id, [], np.zeros((0, 2), dtype=np.float32))
    swapped = replace(config, model_profiles={
        "first": replace(first, table_name=second.table_name),
        "second": replace(second, table_name=first.table_name),
    })
    for changed in (
        swapped, replace(config, model_profiles={"first": first}),
        replace(config, model_profiles={**config.model_profiles, "third": replace(
            first, model_id="third", table_name="vectors_third",
        )}),
        replace(config, content_table="different_content"),
    ):
        with pytest.raises(SourceLayoutError, match="bootstrap model binding"):
            initialize_source_store(changed, point_scope=SCOPE)
        assert snapshot(config.data_dir) == before
    assert domain.receipt() == retained
    assert snapshot(config.data_dir) == before


def test_binding_save_checks_current_disk_and_refuses_drop_or_replacement(config: IndexConfig) -> None:
    initialize_source_layout(config.lance_dir, point_scope=SCOPE)
    stale = SourceDomain(config.lance_dir, point_scope=SCOPE)
    unbound = stale.receipt()
    store = initialize_source_store(config, point_scope=SCOPE)
    domain = store._source_domain
    assert domain is not None
    before = snapshot(config.data_dir)
    retained = domain.receipt()
    for candidate in (
        unbound,
        {key: value for key, value in retained.items() if key != "point_models"},
        {**retained, "point_models": {**retained["point_models"], "models": {}}},
    ):
        with pytest.raises(SourceLayoutError, match="changed or removed"):
            stale.save(candidate)
        assert snapshot(config.data_dir) == before
    with domain._write(store.content_store.db, point_scope=SCOPE):
        during = snapshot(config.data_dir)
        with pytest.raises(SourceLayoutError, match="busy"):
            stale.save(retained)
        with pytest.raises(SourceLayoutError, match="changed or removed"):
            domain.save(unbound)
        assert snapshot(config.data_dir) == during
    assert domain.receipt()["point_models"] == retained["point_models"]


@pytest.mark.parametrize("retained_binding", ["absent", "null", "malformed"])
def test_existing_point_tables_without_valid_binding_are_never_adopted(
    config: IndexConfig, retained_binding: str,
) -> None:
    store = initialize_source_store(config, point_scope=SCOPE)
    domain = store._source_domain
    assert domain is not None
    receipt = domain.receipt()
    binding = receipt.pop("point_models")
    if retained_binding != "absent":
        receipt["point_models"] = None if retained_binding == "null" else {
            **binding, "models": {"first": {"table_name": config.content_table, "dim": 2}},
        }
    (config.lance_dir / MARKER).write_text(json.dumps(receipt))
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="(?:bootstrap model binding|invalid point model)"):
        initialize_source_store(config, point_scope=SCOPE)
    facade = MultiModelStore(config.lance_dir, point_scope=SCOPE) if retained_binding == "absent" else None
    if facade is not None:
        with pytest.raises(SourceLayoutError, match="retained point binding"):
            facade.register_model(config.model_profiles["first"])
    assert snapshot(config.data_dir) == before


def test_binding_is_only_first_saved_inside_empty_schema_writer(config: IndexConfig) -> None:
    initialize_source_layout(config.lance_dir, point_scope=SCOPE)
    unbound = MultiModelStore(config.lance_dir, point_scope=SCOPE)
    domain = unbound._source_domain
    assert domain is not None
    before = snapshot(config.data_dir)
    value = {**domain.receipt(), "point_models": {
        "protocol": 1, "content_table": config.content_table,
        "models": {"first": {"table_name": "vectors_first", "dim": 2}},
    }}
    with pytest.raises(SourceLayoutError, match="empty owning writer"):
        domain.save(value)
    with pytest.raises(SourceLayoutError, match="retained point binding"):
        unbound.register_model(config.model_profiles["first"])
    assert snapshot(config.data_dir) == before
    with domain._write(unbound.content_store.db, point_scope=SCOPE):
        unbound.content_store._get_or_create_table()
        during = snapshot(config.data_dir)
        with pytest.raises(SourceLayoutError, match="empty owning writer"):
            domain.save({**domain.receipt(), "point_models": value["point_models"]})
        assert snapshot(config.data_dir) == during
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="bootstrap model binding"):
        initialize_source_store(config, point_scope=SCOPE)
    assert snapshot(config.data_dir) == before


def test_default_model_registration_keeps_legacy_behavior(config: IndexConfig) -> None:
    store = initialize_source_store(config)
    profile = config.model_profiles["first"]
    owner = store.register_model(profile)
    assert store.register_model(replace(profile, table_name="old-ignored", dim=3)) is owner
    assert store._source_domain is not None
    assert "point_models" not in store._source_domain.receipt()
