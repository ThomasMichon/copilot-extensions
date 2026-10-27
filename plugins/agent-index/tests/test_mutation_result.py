"""Commit observations do not extend ownership or become recovery evidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.mutation_result import observe_mutations, record_write
from agent_index.store.source_layout import LAYOUT, initialize_source_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    return initialize_source_store(IndexConfig(
        data_dir=tmp_path,
        model_profiles={"small": ModelProfile("small", "fixture", dim=2, table_name="vectors_small")},
    ))


def chunk(path="one.md"):
    return Chunk("Useful text.", path, "text", "markdown", 1, 1, "git:sample")


def test_nested_transactions_observe_only_the_outer_clean_commit(store):
    domain = store._source_domain
    before = domain.receipt()["logical_projection"]
    with observe_mutations(domain) as result:
        with domain.write(store.content_store.db):
            store.upsert("small", [chunk()], np.array([[1, 0]], np.float32))
            assert result.write_commits == 0
    assert result.write_commits == result.logical_commits == 1
    assert result.tables == ["chunks", "vectors_small"]
    assert result.epoch == before["epoch"]
    assert result.generation == before["generation"] + 1


def test_completed_writes_survive_failed_later_write_but_no_false_commit_is_observed(store):
    domain = store._source_domain
    with observe_mutations(domain) as result:
        store.upsert_content([chunk()])
        with pytest.raises(RuntimeError, match="interruption"):
            with domain.write(store.content_store.db):
                raise RuntimeError("fixture interruption")
    assert result.write_commits == result.logical_commits == 1
    assert result.tables == ["chunks"]
    assert domain.receipt()["state"] == "dirty"


def test_failed_clean_receipt_publication_is_not_a_successful_observation(store, monkeypatch):
    domain = store._source_domain
    save = domain.save

    def fail_clean(value):
        if value["state"] == "clean":
            raise OSError("fixture clean receipt failure")
        save(value)

    monkeypatch.setattr(domain, "save", fail_clean)
    with observe_mutations(domain) as result:
        with pytest.raises(OSError, match="clean receipt"):
            store.upsert_content([chunk()])
    assert result.write_commits == result.logical_commits == 0
    assert domain.receipt()["state"] == "dirty"


def test_physical_maintenance_and_identical_upsert_do_not_report_logical_changes(store):
    domain = store._source_domain
    store.upsert_content([chunk()])
    before = domain.receipt()["logical_projection"]
    table = store.content_store._get_or_create_table()
    with observe_mutations(domain) as result:
        assert store.upsert_content([chunk()], skip_identical=True) == 0
        with domain.write(store.content_store.db):
            with domain.physical_maintenance(table.name, "fixture-physical"):
                table.update(values={"indexed_at": 1.0})
    assert result.write_commits == 2
    assert result.logical_commits == 0 and result.tables == []
    assert result.generation == before["generation"]


@pytest.mark.parametrize("missing", ["before", "all"])
def test_unavailable_physical_witness_remains_unknown(store, monkeypatch, missing):
    from agent_index.store import logical_projection

    domain = store._source_domain
    physical_tables = logical_projection.physical_tables
    calls = 0

    def physical(*args):
        nonlocal calls
        calls += 1
        return None if missing == "all" or calls == 1 else physical_tables(*args)

    monkeypatch.setattr(logical_projection, "physical_tables", physical)
    with observe_mutations(domain) as result:
        store.upsert_content([chunk()])
    assert result.available and result.write_commits == 1
    assert result.logical_commits is None and result.tables is None
    if missing == "all":
        assert result.epoch is result.generation is None
    else:
        assert result.generation is not None


def test_result_does_not_report_unpublished_optional_generation(store, monkeypatch):
    domain = store._source_domain
    save = domain.save

    def omit_optional(value):
        save({key: item for key, item in value.items() if key != "logical_projection"})

    monkeypatch.setattr(domain, "save", omit_optional)
    with observe_mutations(domain) as result:
        store.upsert_content([chunk()])
    assert result.logical_commits == 1 and result.tables == ["chunks"]
    assert result.epoch is result.generation is None


def test_observation_restores_nested_scope_and_ignores_another_domain():
    domain, other = object(), object()
    with observe_mutations(domain) as outer:
        with observe_mutations(other) as inner:
            record_write(domain, {"wrong"}, {}, lambda: {})
            record_write(other, {"other"}, {}, lambda: {})
        record_write(domain, {"outer"}, {}, lambda: {})
        with observe_mutations(None) as unavailable:
            record_write(domain, {"wrong"}, {}, lambda: {})
    record_write(domain, {"outside"}, {}, lambda: pytest.fail("observer leaked"))
    assert outer.tables == ["outer"] and outer.write_commits == 1
    assert inner.tables == ["other"] and inner.write_commits == 1
    assert not unavailable.available and unavailable.logical_commits is None


def test_concurrent_observers_do_not_mix_even_for_the_same_domain():
    barrier = Barrier(2)
    domain = object()

    def observe(name):
        with observe_mutations(domain) as result:
            barrier.wait(timeout=5)
            record_write(domain, {name}, {}, lambda: {})
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(observe, ["first", "second"])
    assert first.tables == ["first"] and second.tables == ["second"]
