"""Mutation preflight cannot mistake an unreadable canonical ID for an absent ID."""

import numpy as np
import pytest
from test_source_aware_store import chunk, cold as cold

from agent_index.store.source_layout import SourceLayoutError, validate_source_store


def test_selected_lookup_failure_prevents_provenance_overwrite(cold, monkeypatch):
    _config, store = cold
    original = chunk()
    store.upsert("small", [original], np.array([[1, 0]], dtype=np.float32))
    before = store._source_domain.receipt()
    table = store.content_store._get_or_create_table()
    changed = chunk("git:other", "attempted replacement")
    object.__setattr__(changed, "chunk_id", original.chunk_id)

    def failed_lookup(*args, **kwargs):
        raise RuntimeError("fixture canonical query failed")

    with monkeypatch.context() as patch:
        patch.setattr(table, "search", failed_lookup)
        with pytest.raises(RuntimeError, match="canonical query failed"):
            store.upsert_content([changed])
    receipt = store._source_domain.receipt()
    assert receipt["state"] == "dirty" and receipt["versions"] == before["versions"]
    assert not receipt.get("uncertain_effect")  # Preflight failed before destructive intent.
    with pytest.raises(SourceLayoutError):
        store.stats()
    validate_source_store(store)  # Explicit no-effect validation, not automatic repair.
    record = store.content_store.get_by_ids([original.chunk_id])[original.chunk_id]
    assert record.source == original.source and record.content == original.content
    vector = store.get_vector_table("small")._get_or_create_table().search().limit(1).to_list()[0]
    assert vector["source"] == original.source and vector["chunk_id"] == original.chunk_id
    assert vector["vector"] == [1.0, 0.0]
    absent = chunk("git:new", "genuinely new")
    assert store.content_store.get_by_ids([absent.chunk_id]) == {}
    store.upsert("small", [absent], np.array([[0, 1]], dtype=np.float32))
    assert store.content_store.get_by_ids([absent.chunk_id])[absent.chunk_id].source == absent.source


def test_legacy_id_lookup_keeps_availability_fallback(tmp_path, monkeypatch):
    from agent_index.store.multi_model_store import MultiModelStore

    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path / "legacy")
    table = store.content_store._get_or_create_table()

    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture legacy query unavailable")

    monkeypatch.setattr(table, "search", unavailable)
    assert store.content_store.get_by_ids(["missing"]) == {}
