"""The acquisition row, not prepared bytes, owns publication and retirement."""

from __future__ import annotations

from contextlib import closing
import sqlite3

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.indexing.file_batches import FileBatch
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.sources.base import FileEntry


@pytest.fixture
def journal(tmp_path):
    paths = PathIndex(tmp_path / "paths.db")
    pending = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="sample:repo", accounting_family="sample:repo",
        upserts=[FileEntry("one.md", "Useful text.", "markdown", "sample:repo")],
        commit="pinned",
    )
    pending.save(manifest, scope="account", full=False)
    payload = pending.read_payload(manifest.source, "account")
    prepared = PreparedJournal(paths, manifest.source, "account", payload)
    return paths, pending, manifest, prepared


def test_batches_and_float32_vectors_roundtrip_with_metadata(journal):
    paths, _pending, _manifest, prepared = journal
    chunk = Chunk("Useful text.", "one.md", "text", "markdown", 1, 1, "sample:repo",
                  metadata={"state": "open"})
    batch = FileBatch([chunk], [("sample:repo", "one.md", "fingerprint", 1)])
    prepared.put_batch(0, batch)
    prepared.put_vectors(["pair"], np.array([[1.25, 2.5]], dtype=np.float64), 2)
    resumed = PreparedJournal(paths, prepared.source, prepared.scope, prepared.payload)
    actual = list(resumed.batches(1))
    assert actual == [batch]
    assert actual[0].chunks[0].metadata == chunk.metadata
    assert actual[0].chunks[0].chunk_id == chunk.chunk_id
    np.testing.assert_array_equal(resumed.vectors(["pair"], 2)["pair"], [1.25, 2.5])
    assert resumed.vectors(["missing"], 2) == {}
    with pytest.raises(ValueError, match="dimension"):
        resumed.vectors(["pair"], 3)
    with pytest.raises(ValueError, match="batch is missing"):
        list(resumed.batches(2))


@pytest.mark.parametrize("operation", ["read", "write", "reset"])
def test_changed_parent_refuses_every_prepared_access(journal, operation):
    paths, pending, manifest, prepared = journal
    prepared.put("vector", [("pair", b"fixture")])
    expected = pending.read_payload(manifest.source, "account")
    pending.compare_and_save(
        manifest.source, scope="account", payload=expected + " ", expected=expected,
        family="sample:repo",
    )
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        if operation == "read":
            prepared.get("vector", ["pair"])
        elif operation == "write":
            prepared.put("vector", [("other", b"fixture")])
        else:
            prepared.reset_batches()
    with closing(paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prepared_crawl_rows").fetchone()[0] == 0


def test_legacy_clear_retires_staging_without_a_second_journal_protocol(journal):
    paths, pending, manifest, prepared = journal
    prepared.put("vector", [("pair", b"fixture")])
    # The unchanged v1 manifest is still readable by a caller that knows no staging API.
    assert pending.load(manifest.source, "account") == (manifest, False)
    pending.clear(manifest.source, manifest=manifest, scope="account")
    with closing(paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prepared_crawl_rows").fetchone()[0] == 0
    with pytest.raises(PendingCrawlConflict):
        prepared.get("vector", ["pair"])
    with pytest.raises(PendingCrawlConflict):
        PreparedJournal(paths, manifest.source, "account", prepared.payload)


def test_prepared_pairs_are_scoped_by_parent_source(journal):
    paths, pending, _manifest, first = journal
    other = CrawlManifest(
        source="sample:other", accounting_family="sample:other",
        upserts=[FileEntry("one.md", "Other text.", "markdown", "sample:other")],
    )
    pending.save(other, scope="other-account", full=False)
    second = PreparedJournal(
        paths, other.source, "other-account", pending.read_payload(other.source, "other-account"),
    )
    first.put_vectors(["pair"], np.array([[1, 0]], dtype=np.float32), 2)
    assert second.vectors(["pair"], 2) == {}
    second.put_vectors(["pair"], np.array([[0, 1]], dtype=np.float32), 2)
    np.testing.assert_array_equal(first.vectors(["pair"], 2)["pair"], [1, 0])
    np.testing.assert_array_equal(second.vectors(["pair"], 2)["pair"], [0, 1])


def test_cache_access_does_not_read_the_full_parent_manifest(journal, monkeypatch):
    paths, _pending, _manifest, prepared = journal
    connect = paths._connect

    def bounded_connection():
        conn = connect()

        def authorize(action, table, column, _database, _trigger):
            if action == sqlite3.SQLITE_READ and table == "pending_crawls" and column == "payload":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(paths, "_connect", bounded_connection)
    keys = [f"pair-{index}" for index in range(505)]
    prepared.put_vectors(keys, np.array([[1, 0]] * len(keys), dtype=np.float32), 2)
    assert len(prepared.vectors(keys, 2)) == len(keys)
    prepared.reset_batches()
    prepared.put_batch(0, FileBatch([], [("sample:repo", "empty.md", "fingerprint", 0)]))
    assert len(list(prepared.batches(1))) == 1


def test_parent_replacement_and_return_to_identical_payload_revokes_old_cache_binding(journal):
    paths, pending, manifest, original = journal
    pending.compare_and_save(
        manifest.source, scope="account", payload=original.payload + " ",
        expected=original.payload, family="sample:repo",
    )
    pending.compare_and_save(
        manifest.source, scope="account", payload=original.payload,
        expected=original.payload + " ", family="sample:repo",
    )
    current = PreparedJournal(paths, manifest.source, "account", original.payload)
    assert current.generation != original.generation
    current.put_vectors(["pair"], np.array([[1, 0]], dtype=np.float32), 2)
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        original.vectors(["pair"], 2)
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        original.put_vectors(["pair"], np.array([[0, 1]], dtype=np.float32), 2)
    np.testing.assert_array_equal(current.vectors(["pair"], 2)["pair"], [1, 0])
