"""Derived chunk/model receipts, qualified against the actual stored vectors."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from agent_index.chunking.base import Chunk
    from agent_index.index_config import ModelProfile
    from agent_index.indexing.path_index import PathIndex
    from agent_index.store.vector_table import VectorTable

SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_coverage (
    table_name TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    source TEXT NOT NULL,
    file_path TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    vector_hash TEXT NOT NULL,
    PRIMARY KEY (table_name, chunk_id)
);
CREATE INDEX IF NOT EXISTS embedding_coverage_file
    ON embedding_coverage(source, file_path);
CREATE TRIGGER IF NOT EXISTS embedding_coverage_remove_file
AFTER DELETE ON indexed_files BEGIN
    DELETE FROM embedding_coverage WHERE source = old.source AND file_path = old.file_path;
END;
"""


def input_hash(chunk: Chunk, profile: ModelProfile) -> str:
    """Hash raw embedding input, not the chunk ID's normalized text hash."""
    raw = json.dumps(
        [chunk.content, profile.model_id, profile.model_name, profile.revision,
         profile.dim, profile.query_prefix, profile.max_seq_length],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def vector_hash(vector: np.ndarray) -> str:
    import numpy as np

    return hashlib.sha256(np.asarray(vector, dtype="<f4").tobytes()).hexdigest()


class EmbeddingCoverage:
    """Optimization evidence only; never a cursor, lease or reader authority."""

    def __init__(self, paths: PathIndex) -> None:
        self.paths = paths

    def missing(
        self, table: VectorTable, chunks: list[Chunk], profile: ModelProfile,
    ) -> list[Chunk]:
        receipts: dict[str, tuple[str, str]] = {}
        conn = self.paths._connect()
        try:
            for start in range(0, len(chunks), 200):
                ids = [chunk.chunk_id for chunk in chunks[start:start + 200]]
                placeholders = ",".join("?" for _ in ids)
                for row in conn.execute(
                    "SELECT chunk_id, input_hash, vector_hash FROM embedding_coverage "
                    f"WHERE table_name = ? AND chunk_id IN ({placeholders})",
                    [table.table_name, *ids],
                ):
                    receipts[row["chunk_id"]] = (row["input_hash"], row["vector_hash"])
        finally:
            conn.close()
        candidates = [
            chunk for chunk in chunks
            if chunk.chunk_id in receipts
            and receipts[chunk.chunk_id][0] == input_hash(chunk, profile)
        ]
        stored: dict[str, np.ndarray] = {}
        for start in range(0, len(candidates), 200):
            stored.update(table.get_vectors(
                [chunk.chunk_id for chunk in candidates[start:start + 200]], strict=True,
            ))
        reusable = {
            cid for cid, vector in stored.items()
            if vector_hash(vector) == receipts[cid][1]
        }
        return [chunk for chunk in chunks if chunk.chunk_id not in reusable]

    def invalidate(self, table: VectorTable, chunks: list[Chunk]) -> None:
        conn = self.paths._connect()
        try:
            conn.executemany(
                "DELETE FROM embedding_coverage WHERE table_name = ? AND chunk_id = ?",
                [(table.table_name, chunk.chunk_id) for chunk in chunks],
            )
            conn.commit()
        finally:
            conn.close()

    def record(
        self, table: VectorTable, chunks: list[Chunk], profile: ModelProfile,
        vectors: np.ndarray,
    ) -> None:
        expected = {
            chunk.chunk_id: vector_hash(vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        }
        actual: dict[str, np.ndarray] = {}
        for start in range(0, len(chunks), 200):
            actual.update(table.get_vectors(
                [chunk.chunk_id for chunk in chunks[start:start + 200]], strict=True,
            ))
        if any(cid not in actual or vector_hash(actual[cid]) != digest
               for cid, digest in expected.items()):
            raise RuntimeError("Stored embeddings do not match the completed embedding batch")
        conn = self.paths._connect()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO embedding_coverage VALUES (?, ?, ?, ?, ?, ?)",
                [(table.table_name, chunk.chunk_id, chunk.source, chunk.file_path,
                  input_hash(chunk, profile), expected[chunk.chunk_id]) for chunk in chunks],
            )
            conn.commit()
        finally:
            conn.close()

    def retain(self, source: str, path: str, chunk_ids: set[str]) -> None:
        """Prune only this changed file's obsolete receipts after reconciliation."""
        conn = self.paths._connect()
        try:
            obsolete = [
                (row["table_name"], row["chunk_id"])
                for row in conn.execute(
                    "SELECT table_name, chunk_id FROM embedding_coverage "
                    "WHERE source = ? AND file_path = ?", (source, path),
                )
                if row["chunk_id"] not in chunk_ids
            ]
            conn.executemany(
                "DELETE FROM embedding_coverage WHERE table_name = ? AND chunk_id = ?",
                obsolete,
            )
            conn.commit()
        finally:
            conn.close()
