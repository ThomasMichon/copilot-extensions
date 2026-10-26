"""Missing-pair selection, partial-write recovery and native vector no-ops."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, input_hash, vector_hash
from agent_index.indexing.engine import _embed_and_store_batch, _embed_and_store_files
from agent_index.indexing.path_index import PathIndex
from agent_index.sources.base import FileEntry
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store


def chunk(text: str = "unchanged text", *, path: str = "one.md", source: str = "git:a") -> Chunk:
    return Chunk(text, path, "text", "markdown", 1, 1, source)


def profile(model: str = "a", **kwargs: object) -> ModelProfile:
    return ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}", **kwargs)


class Client:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.calls.append(texts)
        return np.array([[len(text), 1] for text in texts], dtype=np.float32)


class Table:
    def __init__(self, name: str) -> None:
        self.table_name = name
        self.rows: dict[str, np.ndarray] = {}
        self.reads: list[int] = []

    def get_vectors(self, ids: list[str], *, strict: bool = False) -> dict[str, np.ndarray]:
        assert strict
        self.reads.append(len(ids))
        return {cid: self.rows[cid] for cid in ids if cid in self.rows}


class Store:
    def __init__(self, profiles: dict[str, ModelProfile]) -> None:
        self.tables = {key: Table(value.table_name) for key, value in profiles.items()}
        self.writes: list[tuple[str, list[str]]] = []

    def get_vector_table(self, model: str) -> Table:
        return self.tables[model]

    def upsert_content(self, chunks: list[Chunk]) -> int:
        return len(chunks)

    def upsert_vectors(self, model: str, chunks: list[Chunk], vectors: np.ndarray) -> int:
        self.writes.append((model, [item.chunk_id for item in chunks]))
        self.tables[model].rows.update({
            item.chunk_id: vector for item, vector in zip(chunks, vectors, strict=True)
        })
        return len(chunks)


def run(
    paths: PathIndex, store: Store, clients: dict[str, Client],
    profiles: dict[str, ModelProfile], chunks: list[Chunk], **kwargs: object,
) -> int:
    return _embed_and_store_batch(
        chunks, store, clients, profiles, coverage=EmbeddingCoverage(paths), **kwargs,
    )


def test_metadata_and_restart_reuse_then_only_missing_model_and_chunk(tmp_path: Path) -> None:
    profiles = {"a": profile()}
    clients = {"a": Client()}
    store = Store(profiles)
    paths = PathIndex(tmp_path / "paths.db")
    original = chunk()
    assert run(paths, store, clients, profiles, [original]) == 1
    metadata = replace(original, metadata={"state": "closed"}, language="python")
    paths = PathIndex(tmp_path / "paths.db")
    run(paths, store, clients, profiles, [metadata])
    assert clients["a"].calls == [[original.content]]
    assert len(store.writes) == 1

    profiles["b"] = profile("b")
    clients["b"] = Client()
    store.tables["b"] = Table(profiles["b"].table_name)
    run(paths, store, clients, profiles, [metadata])
    assert len(clients["a"].calls) == 1
    assert clients["b"].calls == [[original.content]]
    new = chunk("changed part", path="two.md")
    run(paths, store, clients, profiles, [metadata, new])
    assert clients["a"].calls[-1] == clients["b"].calls[-1] == [new.content]


@pytest.mark.parametrize("change", [
    {"revision": "new"}, {"model_name": "new"}, {"max_seq_length": 512},
    {"query_prefix": "query: "},
])
def test_model_identity_invalidates_pairs(tmp_path: Path, change: dict[str, object]) -> None:
    profiles = {"a": profile()}
    clients = {"a": Client()}
    store = Store(profiles)
    paths = PathIndex(tmp_path / "paths.db")
    run(paths, store, clients, profiles, [chunk()])
    profiles["a"] = replace(profiles["a"], **change)
    run(paths, store, clients, profiles, [chunk()])
    assert len(clients["a"].calls) == 2


def test_raw_text_missing_vector_and_legacy_overwrite_are_not_reused(tmp_path: Path) -> None:
    profiles = {"a": profile()}
    clients = {"a": Client()}
    store = Store(profiles)
    paths = PathIndex(tmp_path / "paths.db")
    first = chunk("hello\r\n")
    second = chunk("hello\n")
    assert first.chunk_id == second.chunk_id
    run(paths, store, clients, profiles, [first])
    run(paths, store, clients, profiles, [second])
    table = store.tables["a"]
    table.rows.pop(second.chunk_id)
    run(paths, store, clients, profiles, [second])
    table.rows[second.chunk_id] = np.array([99, 99], dtype=np.float32)
    run(paths, store, clients, profiles, [second])
    assert len(clients["a"].calls) == 4
    run(paths, store, clients, profiles, [second], force=True)
    assert len(clients["a"].calls) == 5


def test_partial_new_profile_write_then_rollback_cannot_reuse_old_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = {"a": profile()}
    clients = {"a": Client()}
    store = Store(profiles)
    paths = PathIndex(tmp_path / "paths.db")
    items = [chunk(), chunk("other", path="two.md")]
    run(paths, store, clients, profiles, items)
    write = store.upsert_vectors

    def partial(model: str, chunks: list[Chunk], vectors: np.ndarray) -> int:
        store.tables[model].rows[chunks[0].chunk_id] = np.array([99, 0], dtype=np.float32)
        raise RuntimeError("partial write")

    monkeypatch.setattr(store, "upsert_vectors", partial)
    with pytest.raises(RuntimeError, match="partial write"):
        run(paths, store, clients, {"a": replace(profiles["a"], revision="B")}, items)
    monkeypatch.setattr(store, "upsert_vectors", write)
    run(PathIndex(tmp_path / "paths.db"), store, clients, profiles, items)
    assert clients["a"].calls == [[item.content for item in items]] * 3


def test_silent_incomplete_write_fails_before_publishing_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = {"a": profile()}
    clients = {"a": Client()}
    store = Store(profiles)
    paths = PathIndex(tmp_path / "paths.db")
    monkeypatch.setattr(store, "upsert_vectors", lambda *a: 1)
    with pytest.raises(RuntimeError, match="Stored embeddings"):
        run(paths, store, clients, profiles, [chunk()])
    assert EmbeddingCoverage(paths).missing(store.tables["a"], [chunk()], profiles["a"]) == [chunk()]


def test_routing_backfills_new_pairs_and_preserves_unclaimed_fallback(tmp_path: Path) -> None:
    profiles = {
        "a": profile(content_types=frozenset({"text"})),
        "b": profile("b", content_types=frozenset({"function"})),
    }
    clients = {key: Client() for key in profiles}
    store, paths = Store(profiles), PathIndex(tmp_path / "paths.db")
    prose, code, unknown = [
        replace(chunk(kind, path=f"{kind}.md"), chunk_type=kind)
        for kind in ("text", "function", "custom")
    ]
    run(paths, store, clients, profiles, [prose, code, unknown])
    assert clients["a"].calls == [["text", "custom"]]
    assert clients["b"].calls == [["function", "custom"]]
    profiles["b"] = replace(profiles["b"], content_types=frozenset({"text", "function"}))
    run(paths, store, clients, profiles, [prose, code, unknown])
    assert len(clients["a"].calls) == 1
    assert clients["b"].calls[-1] == ["text"]
    isolated = replace(prose, source="git:other")
    run(paths, store, clients, profiles, [isolated])
    assert clients["a"].calls[-1] == clients["b"].calls[-1] == ["text"]


def test_large_selected_batch_bounds_sql_and_vector_reads(tmp_path: Path) -> None:
    models = {"a": profile()}
    store, paths = Store(models), PathIndex(tmp_path / "paths.db")
    clients = {"a": Client()}
    items = [chunk(path=f"{i}.md") for i in range(505)]
    run(paths, store, clients, models, items, stream_batch_size=505)
    run(paths, store, clients, models, items, stream_batch_size=505)
    assert len(clients["a"].calls) == 1
    assert store.tables["a"].reads == [200, 200, 105] * 2


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_receipt_lookup_is_bounded_by_selected_pairs(tmp_path: Path, size: int) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    item, model = chunk(), profile()
    table = Table(model.table_name)
    vector = np.array([1, 0], dtype=np.float32)
    table.rows[item.chunk_id] = vector
    conn = paths._connect()
    try:
        conn.executemany(
            "INSERT INTO embedding_coverage VALUES (?, ?, ?, ?, ?, ?)",
            [(model.table_name, f"unselected-{i}", "git:other", f"{i}.md", "old", "old")
             for i in range(size)],
        )
        conn.execute(
            "INSERT INTO embedding_coverage VALUES (?, ?, ?, ?, ?, ?)",
            (model.table_name, item.chunk_id, item.source, item.file_path,
             input_hash(item, model), vector_hash(vector)),
        )
        conn.commit()
    finally:
        conn.close()
    connect = paths._connect
    steps = 0

    def tick() -> int:
        nonlocal steps
        steps += 1
        return 0

    def measured():
        conn = connect()
        conn.set_progress_handler(tick, 1)
        return conn

    paths._connect = measured
    assert EmbeddingCoverage(paths).missing(table, [item], model) == []
    assert table.reads == [1]
    assert steps < 100


@pytest.mark.parametrize("selected", [False, True])
def test_native_metadata_reuse_preserves_vector_version_and_lazy_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    models = {"a": profile()}
    config = IndexConfig(data_dir=tmp_path, model_profiles=models)
    store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
    if not selected:
        store.register_model(models["a"])
    client = Client()
    paths = PathIndex(tmp_path / "paths.db")
    ready: list[bool] = []
    entry = FileEntry("one.md", "# Example\n\nSome useful prose.", "markdown", source="git:a")

    def index(item: FileEntry, **kwargs: object) -> None:
        _embed_and_store_files(
            [item], multi_store=store, multi_clients={"a": client}, model_profiles=models,
            path_index=paths, reconcile_file=lambda *a: None, before_store=lambda: ready.append(True),
            **kwargs,
        )

    index(entry)
    vector_table = store.get_vector_table("a")._get_or_create_table()
    version = vector_table.version
    index(replace(entry, metadata={"state": "closed"}))
    assert len(client.calls) == len(ready) == 1
    assert vector_table.version == version
    assert store.get_document("git:a", entry.path).metadata == {"state": "closed"}
    index(replace(entry, metadata={"state": "closed"}), force=True)
    assert len(client.calls) == len(ready) == 2


def test_receipts_prune_only_reconciled_file_and_follow_file_removal(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    models = {"a": profile()}
    store, clients = Store(models), {"a": Client()}
    old, other = chunk(), chunk(path="else.md")
    run(paths, store, clients, models, [old, other])
    paths.mark_indexed(old.source, old.file_path)
    coverage = EmbeddingCoverage(paths)
    coverage.retain(old.source, old.file_path, set())
    assert coverage.missing(store.tables["a"], [old, other], models["a"]) == [old]
    run(paths, store, clients, models, [old])
    paths.remove(old.source, old.file_path)
    assert coverage.missing(store.tables["a"], [old, other], models["a"]) == [old]


def test_cleanup_failure_retries_without_reembedding_and_empty_file_prunes_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = {"a": profile()}
    store, paths = Store(models), PathIndex(tmp_path / "paths.db")
    client, item = Client(), chunk()
    from types import SimpleNamespace

    output = [item]
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda _path: (
        SimpleNamespace(chunk=lambda *a, **k: output), "markdown",
    ))
    entry = FileEntry(item.file_path, item.content, item.language, item.source)
    fail = True

    def cleanup(source: str, path: str) -> None:
        if fail:
            raise RuntimeError("cleanup interrupted")

    def index(entry: FileEntry) -> None:
        _embed_and_store_files(
            [entry], multi_store=store, multi_clients={"a": client}, model_profiles=models,
            path_index=paths, reconcile_file=cleanup,
        )

    with pytest.raises(RuntimeError, match="cleanup interrupted"):
        index(entry)
    assert paths.get_entry(item.source, item.file_path) is None
    fail = False
    index(entry)
    assert len(client.calls) == 1
    assert paths.get_content_hash(item.source, item.file_path)
    output = []
    index(replace(entry, content=""))
    assert EmbeddingCoverage(paths).missing(store.tables["a"], [item], models["a"]) == [item]
