"""Native immutable offset reads and conservative storage-incarnation checks."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_embedding_pair_reuse import chunk

from agent_index.store.content_store import ContentStore
from agent_index.store.content_snapshot import ContentSnapshot, ContentSnapshotUnavailable
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store
from agent_index.index_config import IndexConfig


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def content(request, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if request.param else "")
    if request.param:
        return initialize_source_store(IndexConfig(data_dir=tmp_path)).content_store
    return ContentStore(tmp_path)


def test_native_pinned_reads_survive_mutations_but_ctime_is_conservative(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", "")
    content = ContentStore(tmp_path)
    first, second = chunk(), chunk("second", path="two.md")
    content.upsert([first, second])
    table = content._get_or_create_table()
    version = table.version
    path = tmp_path / "chunks.lance"

    def identity():
        value = path.stat()
        return value.st_dev, value.st_ino, value.st_ctime_ns

    original = identity()
    pinned = content.db.open_table("chunks")
    pinned.checkout(version)
    content.upsert([chunk("third", path="three.md")])
    assert identity()[:2] == original[:2]
    content.upsert([first])
    assert identity()[:2] == original[:2]
    table.delete(f"chunk_id = '{second.chunk_id}'")
    assert identity()[:2] == original[:2]
    from lancedb.index import BTree
    table.create_index("chunk_id", config=BTree())
    assert identity()[:2] == original[:2]
    assert pinned.version == version
    assert pinned.count_rows() == 2
    rows = pinned.take_offsets([0, 1]).select(["chunk_id"]).to_arrow().to_pylist()
    assert {row["chunk_id"] for row in rows} == {first.chunk_id, second.chunk_id}
    assert table.version > version


def test_native_bounded_pages_and_restart_do_not_mutate_owner(content) -> None:
    items = [chunk(f"row {i}", path=f"{i}.md") for i in range(7)]
    content.upsert(items)
    owner = content._get_or_create_table()
    snapshot = content.capture_snapshot()
    assert snapshot.rows == 7
    assert owner.version == snapshot.version
    assert content.read_snapshot(snapshot, start=7, limit=3) == []
    seen = []
    for start in range(0, snapshot.rows, 3):
        page = content.read_snapshot(snapshot, start=start, limit=3)
        assert len(page) == min(3, snapshot.rows - start)
        seen.extend(page)
    assert {row.chunk_id for row in seen} == {item.chunk_id for item in items}
    reopened = ContentStore(content._db_path, table_name=content._table_name)
    assert {row.chunk_id for row in reopened.read_snapshot(snapshot, start=0, limit=3)} == {
        row.chunk_id for row in seen[:3]
    }
    assert content._table is owner
    assert owner.version == snapshot.version
    assert content.capture_snapshot() == snapshot


def test_missing_empty_and_expired_storage_refuse_without_creating(content) -> None:
    if content._source_domain is None:
        with pytest.raises(ContentSnapshotUnavailable, match="storage"):
            content.capture_snapshot()
    else:
        empty = content.capture_snapshot()
        assert empty.rows == 0
        assert content.read_snapshot(empty, start=0, limit=3) == []
    content.upsert([chunk()])
    snapshot = content.capture_snapshot()
    with pytest.raises(ContentSnapshotUnavailable, match="repair"):
        content.read_snapshot(replace(snapshot, version=999999), start=0, limit=1)
    wrong = replace(snapshot, storage=(snapshot.storage[0], snapshot.storage[1] + 1, 1))
    with pytest.raises(ContentSnapshotUnavailable, match="identity"):
        content.read_snapshot(wrong, start=0, limit=1)
    # Retired offsets cannot silently become an offset in a replacement table.
    path = Path(content._db_path) / f"{content._table_name}.lance"
    path.rename(path.with_name("retired.lance"))
    with pytest.raises((ContentSnapshotUnavailable, SourceLayoutError)):
        content.read_snapshot(snapshot, start=0, limit=1)


@pytest.mark.parametrize("start,limit", [
    (-1, 1), (2, 1), (0, 0), (0, -1), (True, 1), (0, True), (0, 1.5),
])
def test_invalid_range_fails_before_backend(content, start, limit) -> None:
    content.upsert([chunk()])
    snapshot = content.capture_snapshot()
    with pytest.raises(ValueError, match="range"):
        content.read_snapshot(snapshot, start=start, limit=limit)


def test_native_changed_identity_refuses_even_if_version_survives(content) -> None:
    content.upsert([chunk()])
    snapshot = content.capture_snapshot()
    path = Path(content._db_path) / f"{content._table_name}.lance"
    extra = path / "incarnation-change"
    extra.mkdir()
    assert path.stat().st_ctime_ns != snapshot.storage[2]
    with pytest.raises(ContentSnapshotUnavailable, match="identity"):
        content.read_snapshot(snapshot, start=0, limit=1)


@pytest.mark.parametrize("field,value", [
    ("root", ""), ("table_name", ""), ("version", True), ("version", 0),
    ("rows", -1), ("rows", True), ("storage", (1, 0, 1)), ("domain", ""),
])
def test_invalid_descriptor(field, value) -> None:
    values = dict(root="/local", table_name="chunks", storage=(1, 1, 1),
                  domain=None, version=1, rows=1)
    values[field] = value
    with pytest.raises(ValueError, match="descriptor"):
        ContentSnapshot(**values)


def test_bounded_query_never_searches_or_materializes_corpus(content, monkeypatch) -> None:
    items = [chunk(f"row {i}") for i in range(100)]
    content.upsert(items)
    snapshot = content.capture_snapshot()
    actual = content.db.open_table(content._table_name)
    requests = []

    def take(offsets):
        requests.append(offsets)
        return actual.take_offsets(offsets)

    fake = SimpleNamespace(
        checkout=actual.checkout, count_rows=actual.count_rows,
        version=snapshot.version, take_offsets=take,
    )
    open_table = content._db.open_table
    monkeypatch.setattr(content._db, "open_table", lambda name: (
        fake if name == content._table_name else open_table(name)
    ))
    # Source-aware receipt validation also uses this metadata-only handle.
    result = content.read_snapshot(snapshot, start=53, limit=4)
    assert len(result) == 4
    assert requests == [[53, 54, 55, 56]]


@pytest.mark.parametrize("corruption", [
    {"metadata": "{"}, {"metadata": "[]"}, {"line_start": None},
    {"content": None}, {"chunk_id": None},
])
def test_malformed_native_rows_fail_loudly(tmp_path, monkeypatch, corruption) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", "")
    content = ContentStore(tmp_path)
    content.upsert([chunk()])
    table = content._get_or_create_table()
    table.update(values=corruption)
    snapshot = content.capture_snapshot()
    with pytest.raises(ContentSnapshotUnavailable, match="repair"):
        content.read_snapshot(snapshot, start=0, limit=1)


def test_incomplete_query_cannot_return_success(content, monkeypatch) -> None:
    content.upsert([chunk(), chunk("two")])
    snapshot = content.capture_snapshot()
    import pyarrow as pa

    fake = SimpleNamespace(
        checkout=lambda _version: None, count_rows=lambda: snapshot.rows,
        version=snapshot.version,
        take_offsets=lambda _offsets: SimpleNamespace(
            select=lambda _columns: SimpleNamespace(to_arrow=lambda: pa.table({})),
        ),
    )
    open_table = content._db.open_table
    monkeypatch.setattr(content._db, "open_table", lambda name: (
        fake if name == content._table_name else open_table(name)
    ))
    with pytest.raises(ContentSnapshotUnavailable, match="repair"):
        content.read_snapshot(snapshot, start=0, limit=2)


def test_identity_rechecked_after_native_read(content, monkeypatch) -> None:
    content.upsert([chunk()])
    snapshot = content.capture_snapshot()
    actual = content.db.open_table(content._table_name)
    open_table = content._db.open_table

    def take(offsets):
        query = actual.take_offsets(offsets)
        (Path(content._db_path) / f"{content._table_name}.lance" / "race").mkdir()
        return query

    fake = SimpleNamespace(
        checkout=actual.checkout, count_rows=actual.count_rows,
        version=snapshot.version, take_offsets=take,
    )
    monkeypatch.setattr(content._db, "open_table", lambda name: (
        fake if name == content._table_name else open_table(name)
    ))
    with pytest.raises(ContentSnapshotUnavailable, match="identity"):
        content.read_snapshot(snapshot, start=0, limit=1)


def test_changed_rowcount_refuses_before_query(content, monkeypatch) -> None:
    content.upsert([chunk()])
    snapshot = content.capture_snapshot()
    actual = content.db.open_table(content._table_name)
    open_table = content._db.open_table
    fake = SimpleNamespace(
        checkout=actual.checkout, count_rows=lambda: snapshot.rows + 1,
        version=snapshot.version,
    )
    monkeypatch.setattr(content._db, "open_table", lambda name: (
        fake if name == content._table_name else open_table(name)
    ))
    with pytest.raises(ContentSnapshotUnavailable, match="repair"):
        content.read_snapshot(snapshot, start=0, limit=1)


def test_source_domain_guard_is_not_bypassed(content) -> None:
    if content._source_domain is None:
        return
    content.upsert([chunk()])
    snapshot = content.capture_snapshot()
    domain = content._source_domain
    receipt = domain.receipt()
    receipt["state"] = "dirty"
    domain.save(receipt)
    with pytest.raises(SourceLayoutError, match="incomplete"):
        content.capture_snapshot()
    with pytest.raises(SourceLayoutError, match="incomplete"):
        content.read_snapshot(snapshot, start=0, limit=1)
