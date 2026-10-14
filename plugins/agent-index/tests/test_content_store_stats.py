from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pyarrow as pa
import pytest

from agent_index.store.content_store import ContentStore


def test_real_stats_counts_all_rows_without_pandas(tmp_path, monkeypatch):
    store = ContentStore(str(tmp_path / "db"))
    table = store._get_or_create_table()
    rows = [
        {"source": "alpha" if i < 1200 else "beta",
         "language": "" if i % 2 else "python", "content": "large omitted text"}
        for i in range(2501)
    ]
    rows.append({"source": None, "language": None})
    table.add(pa.Table.from_pylist(rows, schema=table.schema))
    monkeypatch.setattr(type(table), "to_pandas", lambda *_a, **_kw: pytest.fail("no pandas"))
    assert store.stats() == {
        "table_name": store._table_name,
        "total_chunks": 2502,
        "sources": {"beta": 1301, "alpha": 1200},
        "languages": {"python": 1251, "": 1250},
    }


def test_stats_projects_only_metadata_and_requests_bounded_batches(tmp_path, monkeypatch):
    store = ContentStore(str(tmp_path / "db"))
    calls = []
    batch = pa.record_batch({"source": ["a", None], "language": ["python", ""]})

    class Query:
        def select(self, columns):
            calls.append(("select", columns))
            return self

        def limit(self, limit):
            calls.append(("limit", limit))
            return self

        def to_batches(self, *, batch_size):
            calls.append(("batch_size", batch_size))
            return nullcontext(iter([batch, batch]))

    table = SimpleNamespace(count_rows=lambda: 4, search=Query)
    monkeypatch.setattr(store, "_get_or_create_table", lambda: table)
    result = store.stats()
    assert calls == [
        ("select", ["source", "language"]), ("limit", None), ("batch_size", 1024)
    ]
    assert result["sources"] == {"a": 2}
    assert result["languages"] == {"python": 2, "": 2}


def test_empty_store_preserves_stats_shape(tmp_path):
    store = ContentStore(str(tmp_path / "db"))
    assert store.stats() == {"total_chunks": 0, "table_name": store._table_name}


def test_stats_surfaces_storage_failure_instead_of_reporting_zero(tmp_path, monkeypatch):
    store = ContentStore(str(tmp_path / "db"))

    def fail():
        raise RuntimeError("fixture count failure")

    monkeypatch.setattr(store, "_get_or_create_table", lambda: SimpleNamespace(count_rows=fail))
    with pytest.raises(RuntimeError, match="fixture count failure"):
        store.stats()


def test_stats_rejects_partial_scan_results(tmp_path, monkeypatch):
    store = ContentStore(str(tmp_path / "db"))

    def batches():
        yield pa.record_batch({"source": ["a"], "language": ["python"]})
        raise RuntimeError("fixture scan failure")

    class Query:
        def select(self, _columns):
            return self

        def limit(self, _limit):
            return self

        def to_batches(self, *, batch_size):
            return nullcontext(batches())

    monkeypatch.setattr(
        store, "_get_or_create_table",
        lambda: SimpleNamespace(count_rows=lambda: 2, search=Query),
    )
    with pytest.raises(RuntimeError, match="fixture scan failure"):
        store.stats()
