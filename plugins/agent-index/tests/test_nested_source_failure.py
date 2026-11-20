"""Caught nested failures must not certify a partially effected source writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.point_hints import PointBinding
from agent_index.store.content_mutations import content_record
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import (
    LAYOUT, PointScope, SourceLayoutError, initialize_source_store,
)

Case = tuple[IndexConfig, MultiModelStore, PointScope | None, Chunk]


@pytest.fixture(params=[False, True], ids=["ordinary", "selected-fixture"])
def source_case(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Case:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    scope: PointScope | None = (
        ("sample:components", "sample:points", "account",
         PointBinding("https://example.test/project", "union", "context"))
        if request.param else None
    )
    profile = ModelProfile("small", "fixture", dim=2, table_name="vectors_small")
    config = IndexConfig(data_dir=tmp_path, model_profiles={"small": profile})
    store = initialize_source_store(config, point_scope=scope)
    domain = store._source_domain
    assert domain is not None
    item = Chunk("retained text", "one.md", "text", "markdown", 1, 1,
                 scope[1] if scope else "sample:source")
    with domain._write(store.content_store.db, point_scope=scope):
        domain.effect("fixture-seed")
        store.content_store._get_or_create_table().add([{**content_record(item), "indexed_at": 0.0}])
        store.get_vector_table("small")._get_or_create_table().add([{
            "chunk_id": item.chunk_id, "vector": [1.0, 0.0], "indexed_at": 0.0,
            "source": item.source, "language": item.language,
        }])
    return config, store, scope, item


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_caught_nested_vector_failure_cannot_publish_the_outer_writer_clean(
    source_case: Case, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store, scope, item = source_case
    domain = store._source_domain
    assert domain is not None
    owner = store.get_vector_table("small")
    table = owner._get_or_create_table()

    def failed_add(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("fixture insertion failed after vector deletion")

    with monkeypatch.context() as patch:
        patch.setattr(type(table), "add", failed_add)
        with pytest.raises(SourceLayoutError, match="nested.*effect"):
            with domain._write(store.content_store.db, point_scope=scope):
                with pytest.raises(RuntimeError, match="after vector deletion"):
                    if scope is None:
                        owner.upsert([item], np.ones((1, 2), dtype=np.float32))
                    else:
                        # The selected branch is a private owner fixture, not point API admission.
                        with domain._write(store.content_store.db, point_scope=scope):
                            owner._write_vectors(
                                [item], np.ones((1, 2), dtype=np.float32),
                                canonical={item.chunk_id: content_record(item)},
                            )
                assert table.count_rows() == 0
                assert domain.receipt()["uncertain_effect"]
                with domain._write(store.content_store.db, point_scope=scope):
                    pass
    assert domain.receipt()["state"] == "dirty" and domain.receipt()["uncertain_effect"]
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError):
        with domain.read(store.content_store.db, point_scope=scope):
            pytest.fail("partial native state must not be readable")
    with pytest.raises(SourceLayoutError, match="explicit recovery"):
        with domain._write(store.content_store.db, point_scope=scope):
            pytest.fail("caught partial effects must not replay implicitly")
    assert snapshot(config.data_dir) == before


def test_caught_pre_effect_failure_preserves_earlier_completed_nested_work(source_case: Case) -> None:
    _config, store, scope, item = source_case
    domain = store._source_domain
    assert domain is not None
    table = store.content_store._get_or_create_table()
    with domain._write(store.content_store.db, point_scope=scope):
        with domain._write(store.content_store.db, point_scope=scope):
            domain.effect("fixture-completed-content-update")
            table.update(
                where=f"chunk_id = '{item.chunk_id}'", values={"indexed_at": 1.0},
            )
        with pytest.raises(ValueError, match="before effect"):
            with domain._write(store.content_store.db, point_scope=scope):
                raise ValueError("fixture refusal before effect")
    receipt = domain.receipt()
    assert receipt["state"] == "clean" and not receipt.get("uncertain_effect")
    assert table.search().select(["indexed_at"]).limit(2).to_list() == [{"indexed_at": 1.0}]
    with domain._write(store.content_store.db, point_scope=scope):
        pass
    assert domain.receipt()["state"] == "clean"


def test_caught_nested_seal_failure_keeps_retained_reconciliation_uncertain(source_case: Case) -> None:
    config, store, scope, _item = source_case
    domain = store._source_domain
    assert domain is not None
    with pytest.raises(SourceLayoutError, match="nested.*effect"):
        with domain._write(store.content_store.db, point_scope=scope):
            with pytest.raises(RuntimeError, match="after seal"):
                with domain._write(store.content_store.db, point_scope=scope):
                    domain.seal_reconciliation("fixture-owner", "fixture-token")
                    raise RuntimeError("fixture interruption after seal")
    receipt = domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"]
    assert receipt["reconciliation"]["owner"] == "fixture-owner"
    before = snapshot(config.data_dir)
    with pytest.raises(SourceLayoutError, match="explicit recovery"):
        with domain._write(store.content_store.db, point_scope=scope):
            pytest.fail("retained phase must keep its existing reconciliation owner")
    assert snapshot(config.data_dir) == before
