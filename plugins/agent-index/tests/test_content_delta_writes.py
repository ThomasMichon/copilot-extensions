"""Physical content no-ops and complete identity-based cleanup on both layouts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_embedding_pair_reuse import Client, chunk, profile

from agent_index.index_config import IndexConfig
from agent_index.indexing.engine import _embed_and_store_files
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.path_index import PathIndex
from agent_index.sources.base import FileEntry
from agent_index.store.content_mutations import changed_chunks, coalesce_chunks, content_record
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store, validate_source_store


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def store(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if request.param else "")
    config = IndexConfig(data_dir=tmp_path, model_profiles={"a": profile(), "b": profile("b")})
    result = initialize_source_store(config) if request.param else MultiModelStore(config.lance_dir)
    for model in config.model_profiles.values():
        result.register_model(model)
    return config, result


def test_identical_rows_preserve_timestamp_versions_and_legacy_default(store) -> None:
    _config, db = store
    item = chunk()
    assert db.upsert_content([item]) == 1
    table = db.content_store._get_or_create_table()
    rows, version = table.search().limit(1).to_list(), table.version
    assert db.upsert_content([item], skip_identical=True) == 0
    assert table.version == version
    assert table.search().limit(1).to_list() == rows
    # Older callers still get refreshed timestamps for their timestamp cleanup.
    assert db.upsert_content([item]) == 1
    assert table.version > version
    assert db.upsert_content([], skip_identical=True) == 0


@pytest.mark.parametrize("change", [
    {"metadata": {"state": "closed"}}, {"language": "python"},
    {"content": "unchanged text  "}, {"chunk_type": "heading"},
    {"line_start": 2}, {"line_end": 2}, {"file_path": "other.md"},
])
def test_canonical_fields_are_not_replaced_by_content_hash_only(store, change) -> None:
    _config, db = store
    original = chunk()
    db.upsert_content([original])
    replacement = replace(original, **change)
    # Exercise every persisted field at a fixed ID, including raw-text hash collisions.
    object.__setattr__(replacement, "chunk_id", original.chunk_id)
    assert db.upsert_content([replacement], skip_identical=True) == 1
    actual = db.content_store.get_by_ids([original.chunk_id])[original.chunk_id]
    for name in change:
        assert getattr(actual, name) == change[name]
    assert db.upsert_content([replacement], skip_identical=True) == 0


def test_model_backfill_does_not_rewrite_content_and_counts_logical_chunks(
    store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = store
    original, extra = chunk(), chunk("later section", path="one.md")
    output = [original]
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda _path: (
        SimpleNamespace(chunk=lambda *a, **k: output), "markdown",
    ))
    paths = PathIndex(tmp_path / "paths.db")
    clients = {"a": Client()}
    profiles = {"a": config.model_profiles["a"]}
    entry = FileEntry(original.file_path, original.content, "markdown", original.source)

    def index(*, force: bool = False) -> int:
        return _embed_and_store_files(
            [entry], multi_store=db, multi_clients=clients, model_profiles=profiles,
            path_index=paths, reconcile_file=db.reconcile_file_chunks, force=force,
        )[0]

    assert index() == 1
    table = db.content_store._get_or_create_table()
    rows, version = table.search().limit(1).to_list(), table.version
    profiles["b"] = config.model_profiles["b"]
    clients["b"] = Client()
    assert index() == 1
    assert len(clients["a"].calls) == 1
    assert clients["b"].calls == [[original.content]]
    assert table.version == version
    assert table.search().limit(1).to_list() == rows
    # A chunker change can retain one chunk and introduce another at the same file.
    output.append(extra)
    paths.invalidate_batch([(entry.source, entry.path)])
    assert index() == 2
    assert set(db.content_store.get_chunk_ids_by_file(entry.source, entry.path)) == {
        original.chunk_id, extra.chunk_id,
    }
    original_row = next(row for row in table.search().limit(2).to_list()
                        if row["chunk_id"] == original.chunk_id)
    assert original_row == rows[0]
    assert clients["a"].calls[-1] == clients["b"].calls[-1] == [extra.content]
    assert index(force=True) == 2
    assert clients["a"].calls[-1] == clients["b"].calls[-1] == [
        original.content, extra.content,
    ]


def test_empty_file_cleanup_and_failure_retry_precede_checkpoint(
    store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = store
    first, second = chunk(), chunk("obsolete section")
    other = chunk(path="else.md")
    isolated = chunk(source="git:other")
    for model in config.model_profiles:
        db.upsert(model, [first, second, other, isolated], np.ones((4, 2), np.float32))
    keep = {first.chunk_id}
    assert db.reconcile_file_chunks(first.source, first.file_path, keep) == 1
    versions = db.projection_snapshot()
    assert db.reconcile_file_chunks(first.source, first.file_path, keep) == 0
    assert db.projection_snapshot() == versions
    for model in config.model_profiles:
        assert set(db.get_vector_table(model).get_vectors([
            first.chunk_id, second.chunk_id, other.chunk_id, isolated.chunk_id,
        ])) == {first.chunk_id, other.chunk_id, isolated.chunk_id}

    paths = PathIndex(tmp_path / "paths.db")
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda _path: (
        SimpleNamespace(chunk=lambda *a, **k: []), "markdown",
    ))
    cleanup = db.reconcile_file_chunks
    calls = []

    def interrupted(source: str, path: str, ids: set[str]) -> None:
        calls.append(ids)
        raise RuntimeError("cleanup interrupted")

    def index() -> None:
        _embed_and_store_files(
            [FileEntry(first.file_path, "", "markdown", first.source)],
            multi_store=db, multi_clients={}, model_profiles={},
            path_index=paths, reconcile_file=db.reconcile_file_chunks,
        )

    with monkeypatch.context() as patch:
        patch.setattr(db, "reconcile_file_chunks", interrupted)
        with pytest.raises(RuntimeError, match="cleanup interrupted"):
            index()
    assert calls == [set()]
    assert paths.get_entry(first.source, first.file_path) is None
    index()
    assert paths.get_entry(first.source, first.file_path) is not None
    assert paths.chunk_count_by_family(first.source) == 0
    assert cleanup(first.source, first.file_path, set()) == 0
    assert db.content_store.get_chunk_ids_by_file(first.source, first.file_path) == []
    assert db.content_store.get_by_ids([other.chunk_id, isolated.chunk_id])


def test_read_failure_cannot_authorize_rewrite_or_cleanup(store, monkeypatch) -> None:
    _config, db = store
    item = chunk()
    db.upsert_content([item])
    table = db.content_store._get_or_create_table()
    version = table.version

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("canonical read unavailable")

    for action in (
        lambda: db.upsert_content([item], skip_identical=True),
        lambda: db.reconcile_file_chunks(item.source, item.file_path, set()),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(table, "search", unavailable)
            with pytest.raises(RuntimeError, match="canonical read unavailable"):
                action()
        assert table.version == version
        if db._source_domain is not None:
            validate_source_store(db)


def test_bounded_comparison_and_incomplete_or_duplicate_rows_are_rejected() -> None:
    items = [chunk(path=f"{i}.md") for i in range(505)]
    reads = []
    selected = []

    class Query:
        def where(self, predicate):
            ids = set(predicate.removeprefix("chunk_id IN (").removesuffix(")").replace("'", "").split(", "))
            selected[:] = [content_record(item) for item in items if item.chunk_id in ids]
            reads.append(len(ids))
            return self

        def select(self, fields):
            assert "indexed_at" not in fields
            return self

        def limit(self, count):
            assert count <= 200
            return self

        def to_list(self):
            return selected.copy()

        def to_batches(self, *, batch_size):
            assert batch_size == 200
            yield SimpleNamespace(to_pylist=lambda: selected.copy())

    query = Query()
    table = SimpleNamespace(
        count_rows=lambda predicate: len(query.where(predicate).to_list()),
        search=lambda: query,
    )
    assert changed_chunks(table, items) == []
    assert max(reads) == 200
    table.count_rows = lambda _predicate: 2
    with pytest.raises(RuntimeError, match="Incomplete canonical"):
        changed_chunks(table, items[:1])
    table.count_rows = lambda _predicate: 1
    assert changed_chunks(table, [items[0], items[0]]) == []
    with pytest.raises(ValueError, match="Conflicting canonical"):
        changed_chunks(table, [items[0], replace(items[0], metadata={"different": True})])


def test_cleanup_bounds_id_deletions_and_preserves_retry_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    db = MultiModelStore(tmp_path / "lance")
    db.register_model(profile())
    items = [replace(chunk(str(i)), source="git:'quoted", file_path="it's.md") for i in range(505)]
    db.upsert("a", items, np.ones((len(items), 2), np.float32))
    vt = db.get_vector_table("a")
    delete = vt.delete_by_chunk_ids
    batches = []

    def interrupted(ids: list[str]) -> None:
        batches.append(len(ids))
        delete(ids)
        if len(batches) == 2:
            raise RuntimeError("vector delete interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(vt, "delete_by_chunk_ids", interrupted)
        with pytest.raises(RuntimeError, match="vector delete interrupted"):
            db.reconcile_file_chunks(items[0].source, items[0].file_path, set())
    assert batches == [200, 200]
    assert len(db.content_store.get_chunk_ids_by_file(items[0].source, items[0].file_path)) == 305
    assert db.reconcile_file_chunks(items[0].source, items[0].file_path, set()) == 305
    assert db.content_store.get_chunk_ids_by_file(items[0].source, items[0].file_path) == []
    assert vt.get_vectors([item.chunk_id for item in items]) == {}


def test_same_line_declarations_are_coalesced_on_incremental_and_forced_paths(
    store, tmp_path: Path,
) -> None:
    from agent_index.chunking import get_chunker

    config, db = store
    entry = FileEntry("same.js", "function a() {} function b() {}", "javascript", "git:sample")
    chunker, _ = get_chunker(entry.path)
    generated = chunker.chunk(entry.content, entry.path, source=entry.source)
    unique = coalesce_chunks(generated)
    assert len(generated) > len(unique)
    paths = PathIndex(tmp_path / "paths.db")
    client = Client()
    profiles = {"a": config.model_profiles["a"]}
    # A checkpoint from before canonical coalescing must not hide old copies.
    old_hash = file_fingerprint(entry, profiles).replace("file-v2:", "file-v1:", 1)
    paths.mark_indexed(entry.source, entry.path, content_hash=old_hash, chunk_count=len(generated))

    def index(*, force: bool = False) -> int:
        return _embed_and_store_files(
            [entry], multi_store=db, multi_clients={"a": client},
            model_profiles=profiles, path_index=paths,
            reconcile_file=db.reconcile_file_chunks, force=force,
        )[0]

    assert index() == len(unique)
    assert paths.chunk_count_by_family(entry.source) == len(unique)
    assert index() == 0
    assert index(force=True) == len(unique)
    assert client.calls == [[item.content for item in unique]] * 2
    assert len(db.content_store.get_chunk_ids_by_file(entry.source, entry.path)) == len(unique)
    assert len(db.get_vector_table("a").get_vectors(
        [item.chunk_id for item in unique], strict=True,
    )) == len(unique)


@pytest.mark.parametrize("skip_identical", [False, True])
def test_direct_content_coalesces_exact_records_and_rejects_conflicts(
    store, skip_identical: bool,
) -> None:
    _config, db = store
    item = chunk()
    assert db.upsert_content([item, item], skip_identical=skip_identical) == 1
    table = db.content_store._get_or_create_table()
    version = table.version
    with pytest.raises(ValueError, match="Conflicting canonical"):
        db.upsert_content(
            [item, replace(item, metadata={"different": True})], skip_identical=skip_identical,
        )
    assert table.version == version


def test_legacy_identical_stored_copies_are_coalesced_but_conflicts_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    db = MultiModelStore(tmp_path / "lance")
    original = chunk()
    db.upsert_content([original])
    table = db.content_store._get_or_create_table()
    table.add([dict(content_record(original), indexed_at=0.0)] * 505)
    search = table.search
    batches = []

    class Query:
        def __init__(self):
            self.query = search()

        def where(self, *args, **kwargs):
            self.query = self.query.where(*args, **kwargs)
            return self

        def select(self, *args):
            self.query = self.query.select(*args)
            return self

        def limit(self, *args):
            self.query = self.query.limit(*args)
            return self

        def to_batches(self, *, batch_size):
            reader = self.query.to_batches(batch_size=batch_size)
            try:
                for batch in reader:
                    batches.append(batch.num_rows)
                    yield batch
            finally:
                reader.close()

    with monkeypatch.context() as patch:
        patch.setattr(table, "search", Query)
        assert db.upsert_content([original], skip_identical=True) == 1
    assert sum(batches) == 506 and max(batches) <= 200
    assert table.count_rows() == 1
    assert db.upsert_content([original], skip_identical=True) == 0
    table.add([dict(content_record(replace(original, metadata={"conflict": True})), indexed_at=0.0)])
    version = table.version
    with pytest.raises(RuntimeError, match="Conflicting stored canonical"):
        db.upsert_content([original], skip_identical=True)
    assert table.version == version


def test_legacy_stale_duplicate_ids_do_not_block_file_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    db = MultiModelStore(tmp_path / "lance")
    original = chunk()
    db.upsert_content([original])
    table = db.content_store._get_or_create_table()
    table.add([dict(content_record(original), indexed_at=0.0)])
    assert db.content_store.get_chunk_ids_by_file(
        original.source, original.file_path,
    ) == [original.chunk_id]
    assert db.reconcile_file_chunks(original.source, original.file_path, set()) == 1
    assert table.count_rows() == 0
