"""Resume checkpointing + skip logic for the indexing embed loop (slice 3)."""

from __future__ import annotations

import types

from agent_index.indexing import engine
from agent_index.indexing.path_index import PathIndex


def _files(specs: list[tuple[str, str, str]]) -> list[types.SimpleNamespace]:
    return [types.SimpleNamespace(source=s, path=p, content=c) for s, p, c in specs]


class _FakeChunker:
    def __init__(self, n: int) -> None:
        self._n = n

    def chunk(self, content: object, path: str, source: str | None = None) -> list[object]:
        return [object() for _ in range(self._n)]


def _run(pi: PathIndex, files: list[types.SimpleNamespace], resume_since: float | None):
    return engine._embed_and_store_files(
        files,
        multi_store=None,
        multi_clients={},
        model_profiles=None,
        path_index=pi,
        stream_batch_size=2,
        resume_since=resume_since,
    )


def test_checkpoint_and_resume_skip(monkeypatch, tmp_path) -> None:
    pi = PathIndex(tmp_path / "path_index.db")
    # Fake the expensive embed/store (return count) and the chunker (fixed chunks).
    monkeypatch.setattr(engine, "_embed_and_store_batch", lambda batch, *a, **k: len(batch))
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda path: (_FakeChunker(3), "text"))

    files = _files([("git", "a.py", "AAA"), ("git", "b.py", "BBB"), ("git", "c.py", "CCC")])

    # Fresh run (resume_since=None): everything embedded + checkpointed per flush.
    stored, stored_files = _run(pi, files, None)
    assert stored == 9  # 3 files * 3 chunks
    assert stored_files == {("git", "a.py"), ("git", "b.py"), ("git", "c.py")}
    for path in ("a.py", "b.py", "c.py"):
        entry = pi.get_entry("git", path)
        assert entry is not None and entry[0]  # content_hash was written

    # Resume run: all files already stored at the same hash within the window
    # (resume_since=0 <= indexed_at) -> all skipped, nothing re-embedded.
    stored2, stored_files2 = _run(pi, files, 0.0)
    assert stored2 == 0
    assert stored_files2 == set()

    # A changed file (new content -> new hash) is NOT skipped on resume;
    # an unchanged file still is.
    changed = _files([("git", "a.py", "AAA-changed"), ("git", "b.py", "BBB")])
    stored3, stored_files3 = _run(pi, changed, 0.0)
    assert ("git", "a.py") in stored_files3      # changed -> re-embedded
    assert ("git", "b.py") not in stored_files3  # unchanged -> skipped
    assert stored3 == 3


def test_no_resume_reembeds_everything(monkeypatch, tmp_path) -> None:
    pi = PathIndex(tmp_path / "path_index.db")
    monkeypatch.setattr(engine, "_embed_and_store_batch", lambda batch, *a, **k: len(batch))
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda path: (_FakeChunker(2), "text"))

    files = _files([("git", "a.py", "AAA"), ("git", "b.py", "BBB")])
    _run(pi, files, None)
    # Even though the files are already stored, a fresh run (resume_since=None)
    # re-embeds them all — full-rebuild semantics are preserved.
    stored, stored_files = _run(pi, files, None)
    assert stored == 4  # 2 files * 2 chunks, nothing skipped
    assert stored_files == {("git", "a.py"), ("git", "b.py")}
