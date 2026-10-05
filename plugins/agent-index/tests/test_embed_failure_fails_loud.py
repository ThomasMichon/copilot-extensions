"""A per-model embed/upsert failure other than EngineUnavailableError must
ALSO fail loud (re-raise), not just log a warning and continue.

``upsert_content()`` runs BEFORE the embed call in each batch iteration, so
by the time an embed call fails, the batch's chunks are already persisted in
the content store. Silently continuing past any OTHER exception type left
them stored WITHOUT their vector -- permanently invisible to semantic
search, with the enclosing reindex task still reporting a clean "complete"
(masked further, until #5025's TaskStatus.PARTIAL, behind a plain
``complete`` even when ``sources_failed`` happened to be populated for some
OTHER reason).

Confirmed live on a real host: once the embedding engine started returning
HTTP 500s (a reachable-but-internally-broken engine -- a since-fixed
``transformers`` version-pin typo, copilot-extensions#114), every affected
reindex task kept reporting success while chunks across MULTIPLE already-
indexed sources were missing their vectors in the real vector table.
``embed_texts()`` only maps connection-level unreachability (httpx.
TransportError) to ``EngineUnavailableError``; an HTTP error from a
reachable engine surfaces as ``httpx.HTTPStatusError``, a plain ``Exception``
that fell through the (previously) silent branch untouched.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.engine.client import EngineUnavailableError
from agent_index.indexing.engine import _embed_and_store_batch


def _chunk(text: str = "hello world") -> Chunk:
    return Chunk(
        content=text,
        file_path="a.py",
        chunk_type="function",
        language="python",
        line_start=1,
        line_end=1,
        source="git:repo",
    )


class _FakeMultiStore:
    def __init__(self) -> None:
        self.content_upserts: list[list[Chunk]] = []
        self.vector_upserts: list[tuple[str, list[Chunk]]] = []

    def upsert_content(self, chunks: list[Chunk]) -> int:
        self.content_upserts.append(list(chunks))
        return len(chunks)

    def upsert_vectors(self, model_id: str, chunks: list[Chunk], vectors) -> None:
        self.vector_upserts.append((model_id, list(chunks)))


class _RaisingClient:
    """A client whose embed_texts() always raises the given exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        raise self._exc


class _WorkingClient:
    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 4), dtype=np.float32)


def test_engine_unavailable_still_fails_loud() -> None:
    """Pre-existing, already-correct behavior (#775) -- must not regress."""
    store = _FakeMultiStore()
    client = _RaisingClient(EngineUnavailableError("down"))
    with pytest.raises(EngineUnavailableError):
        _embed_and_store_batch([_chunk()], store, {"code": client})
    # Content was persisted before the embed call failed -- confirms the
    # exact at-risk shape (stored content, no vector) the fail-loud
    # behavior exists to prevent completing "successfully".
    assert store.content_upserts
    assert store.vector_upserts == []


def test_other_embed_exception_also_fails_loud(caplog) -> None:
    """Regression test: an HTTP 500 (httpx.HTTPStatusError in production,
    any non-EngineUnavailableError exception here) must raise too, not be
    silently logged and skipped."""
    store = _FakeMultiStore()
    client = _RaisingClient(RuntimeError("HTTP 500 from a broken engine"))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            _embed_and_store_batch([_chunk()], store, {"code": client})
    assert store.content_upserts
    assert store.vector_upserts == []
    assert any("Failed to embed batch" in r.message for r in caplog.records)


def test_successful_embed_stores_both_content_and_vectors() -> None:
    store = _FakeMultiStore()
    client = _WorkingClient()
    total = _embed_and_store_batch([_chunk()], store, {"code": client})
    assert total == 1
    assert len(store.content_upserts) == 1
    assert len(store.vector_upserts) == 1
    assert store.vector_upserts[0][0] == "code"
