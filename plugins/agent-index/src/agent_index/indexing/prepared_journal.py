"""Prepared bytes subordinate to an exact pending-crawl acquisition."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import closing
from dataclasses import fields
from typing import TYPE_CHECKING

import numpy as np

from agent_index.chunking.base import Chunk
from agent_index.indexing.file_batches import FileBatch
from agent_index.indexing.pending_crawl import PendingCrawlConflict

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

    from agent_index.indexing.path_index import PathIndex


class PreparedJournal:
    """No cursor or ownership of its own; the parent journal retires these rows."""

    def __init__(self, paths: PathIndex, source: str, scope: str, payload: str) -> None:
        self.paths, self.source, self.scope, self.payload = paths, source, scope, payload
        with closing(paths._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS prepared_crawl_rows "
                "(source TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, "
                "payload BLOB NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(source, kind, key))",
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS prepared_crawl_parents "
                "(source TEXT PRIMARY KEY, scope TEXT NOT NULL, generation TEXT NOT NULL)",
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS prepared_crawl_retire "
                "AFTER DELETE ON pending_crawls BEGIN "
                "DELETE FROM prepared_crawl_rows WHERE source = old.source; "
                "DELETE FROM prepared_crawl_parents WHERE source = old.source; END",
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS prepared_crawl_replace "
                "AFTER UPDATE OF scope, payload ON pending_crawls "
                "WHEN old.scope != new.scope OR old.payload != new.payload BEGIN "
                "DELETE FROM prepared_crawl_rows WHERE source = old.source; "
                "DELETE FROM prepared_crawl_parents WHERE source = old.source; END",
            )
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if row is None or (row["scope"], row["payload"]) != (scope, payload):
                raise PendingCrawlConflict("Prepared application no longer matches its pending crawl")
            conn.execute(
                "INSERT OR IGNORE INTO prepared_crawl_parents VALUES (?, ?, ?)",
                (source, scope, uuid.uuid4().hex),
            )
            bound = conn.execute(
                "SELECT scope, generation FROM prepared_crawl_parents WHERE source = ?", (source,),
            ).fetchone()
            if bound["scope"] != scope:
                raise PendingCrawlConflict("Prepared cache belongs to a different account scope")
            self.generation = bound["generation"]

    def _require_parent(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT scope, generation FROM prepared_crawl_parents WHERE source = ?", (self.source,),
        ).fetchone()
        if row is None or (row["scope"], row["generation"]) != (self.scope, self.generation):
            raise PendingCrawlConflict("Prepared application no longer matches its pending crawl")

    def put(self, kind: str, values: list[tuple[str, bytes]]) -> None:
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_parent(conn)
            conn.executemany(
                "INSERT OR REPLACE INTO prepared_crawl_rows VALUES (?, ?, ?, ?, ?)",
                [(self.source, kind, key, payload, hashlib.sha256(payload).hexdigest())
                 for key, payload in values],
            )

    def get(self, kind: str, keys: list[str]) -> dict[str, bytes]:
        result = {}
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN")
            self._require_parent(conn)
            for start in range(0, len(keys), 200):
                selected = keys[start:start + 200]
                placeholders = ",".join("?" for _ in selected)
                for row in conn.execute(
                    "SELECT key, payload, digest FROM prepared_crawl_rows "
                    f"WHERE source = ? AND kind = ? AND key IN ({placeholders})",
                    [self.source, kind, *selected],
                ):
                    payload = row["payload"]
                    if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != row["digest"]:
                        raise ValueError("Corrupt prepared crawl data")
                    result[row["key"]] = payload
        return result

    def reset_batches(self) -> None:
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_parent(conn)
            conn.execute(
                "DELETE FROM prepared_crawl_rows WHERE source = ? AND kind = 'batch'", (self.source,),
            )

    def put_batch(self, index: int, batch: FileBatch) -> None:
        init_fields = [field.name for field in fields(Chunk) if field.init]
        payload = json.dumps({
            "version": 1,
            "chunks": [{name: getattr(chunk, name) for name in init_fields} for chunk in batch.chunks],
            "checkpoints": batch.checkpoints,
        }, ensure_ascii=False).encode("utf-8")
        self.put("batch", [(str(index), payload)])

    def batches(self, count: int) -> Iterator[FileBatch]:
        for index in range(count):
            stored = self.get("batch", [str(index)])
            if str(index) not in stored:
                raise ValueError("Prepared crawl batch is missing")
            data = json.loads(stored[str(index)])
            if data.get("version") != 1:
                raise ValueError("Unsupported prepared crawl batch")
            yield FileBatch(
                [Chunk(**chunk) for chunk in data["chunks"]],
                [(source, path, fingerprint, size)
                 for source, path, fingerprint, size in data["checkpoints"]],
            )

    def vectors(self, keys: list[str], dim: int) -> dict[str, np.ndarray]:
        result = {}
        for key, payload in self.get("vector", keys).items():
            if len(payload) != dim * 4:
                raise ValueError("Prepared embedding has an incompatible dimension")
            vector = np.frombuffer(payload, dtype="<f4").copy()
            if not np.isfinite(vector).all():
                raise ValueError("Prepared embedding is not finite")
            result[key] = vector
        return result

    def put_vectors(self, keys: list[str], vectors: np.ndarray, dim: int) -> None:
        vectors = np.asarray(vectors, dtype="<f4")
        if vectors.shape != (len(keys), dim) or not np.isfinite(vectors).all():
            raise ValueError("Embedding response has an incompatible shape or nonfinite values")
        self.put("vector", [(key, vector.tobytes()) for key, vector in zip(keys, vectors, strict=True)])
