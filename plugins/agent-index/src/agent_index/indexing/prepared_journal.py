"""Prepared bytes subordinate to an exact pending-crawl acquisition."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import AbstractContextManager, closing, nullcontext
from dataclasses import fields
from typing import TYPE_CHECKING, Any

import numpy as np

from agent_index.chunking.base import Chunk
from agent_index.indexing.file_batches import FileBatch
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex


class PreparedJournal:
    """No cursor or ownership of its own; the parent journal retires these rows."""

    def __init__(
        self, paths: PathIndex, source: str, scope: str, payload: str, *,
        manifest: CrawlManifest | None = None, full: bool | None = None,
        point_claim: PendingPointClaim | None = None,
        require_existing: bool = False,
    ) -> None:
        if (manifest is None) != (full is None) or full is not None and type(full) is not bool:
            raise ValueError("Prepared manifest and boolean full mode must be supplied together")
        if point_claim is None:
            acquired = PendingCrawls.decode_pending(payload, source)
        else:
            if type(point_claim) is not PendingPointClaim:
                raise ValueError("Prepared point work requires a complete typed claim")
            acquired_manifest, retained_claim = PendingCrawls.decode_point_pending(payload, source)
            if retained_claim != point_claim:
                raise PendingCrawlConflict("Prepared point claim does not match its pending crawl")
            acquired = acquired_manifest, False
        if acquired is None:
            raise ValueError("Prepared application requires a completed pending crawl")
        if manifest is not None:
            supplied = json.dumps(PendingCrawls.manifest_data(manifest), sort_keys=True, allow_nan=False)
            retained = json.dumps(PendingCrawls.manifest_data(acquired[0]), sort_keys=True, allow_nan=False)
            if (supplied, full) != (retained, acquired[1]):
                raise PendingCrawlConflict("Prepared manifest does not match its acquired pending crawl")
        self.paths, self.source, self.scope, self.payload = paths, source, scope, payload
        self.point_claim = point_claim
        self._batch_plan: bytes | None = None
        with closing(paths._connect()) as conn, conn:
            if require_existing:
                conn.execute("BEGIN")
                row = conn.execute(
                    "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
                ).fetchone()
                bound = conn.execute(
                    "SELECT scope, generation FROM prepared_crawl_parents WHERE source = ?", (source,),
                ).fetchone()
                if (
                    row is None or (row["scope"], row["payload"]) != (scope, payload)
                    or bound is None or bound["scope"] != scope
                ):
                    raise PendingCrawlConflict("Prepared completion requires its existing pending parent")
                self.generation = bound["generation"]
                return
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if row is None or (row["scope"], row["payload"]) != (scope, payload):
                raise PendingCrawlConflict("Prepared application no longer matches its pending crawl")
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

    @classmethod
    def for_point_application(
        cls, paths: PathIndex, source: str, scope: str, payload: str, *,
        manifest: CrawlManifest, point_claim: PendingPointClaim,
    ) -> PreparedJournal:
        """Bind a complete retained point candidate without replacing its pending parent."""
        from agent_index.indexing.prepared_points import PreparedPoints

        journal = cls(paths, source, scope, payload, point_claim=point_claim)
        PreparedPoints(journal).bind_application(manifest)
        return journal

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
            if kind == "batch":
                kind = self._require_batch_plan(conn, writing=True)
                if self._batch_plan is not None:
                    for key, payload in values:
                        self._put_batch_reference(conn, kind, key, payload)
                    return
            conn.executemany(
                "INSERT OR REPLACE INTO prepared_crawl_rows VALUES (?, ?, ?, ?, ?)",
                [(self.source, kind, key, payload, hashlib.sha256(payload).hexdigest())
                 for key, payload in values],
            )

    def get(self, kind: str, keys: list[str]) -> dict[str, bytes]:
        bound_batch = kind == "batch" and self._batch_plan is not None
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN")
            self._require_parent(conn)
            if kind == "batch":
                kind = self._require_batch_plan(conn)
            result = self._read(conn, kind, keys)
            if not bound_batch:
                return result
            references = {key: value.decode("ascii") for key, value in result.items()}
            blobs = self._read(conn, "batch-payload/v1", list(set(references.values())))
            resolved = {}
            for key, digest in references.items():
                payload = blobs.get(digest)
                if payload is None or hashlib.sha256(payload).hexdigest() != digest:
                    raise ValueError("Missing or corrupt prepared batch payload")
                resolved[key] = payload
            return resolved

    def _read(self, conn: sqlite3.Connection, kind: str, keys: list[str]) -> dict[str, bytes]:
        return dict(self._iter_read(conn, kind, keys))

    def iter_rows(self, kind: str, keys: list[str]) -> Iterator[tuple[str, bytes]]:
        """Validate rows incrementally under one parent-checked read snapshot."""
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN")
            self._require_parent(conn)
            if kind == "batch":
                raise ValueError("Batch plans require reference-qualified reads")
            yield from self._iter_read(conn, kind, keys)

    def _iter_read(
        self, conn: sqlite3.Connection, kind: str, keys: list[str],
    ) -> Iterator[tuple[str, bytes]]:
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
                yield row["key"], payload

    def put_immutable(self, kind: str, key: str, payload: bytes) -> None:
        """Retain exact attempt intent without replacing an earlier publication."""
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_parent(conn)
            if kind == "batch":
                kind = self._require_batch_plan(conn, writing=True)
                if self._batch_plan is not None:
                    self._put_batch_reference(conn, kind, key, payload)
                    return
            self._put_immutable(conn, kind, key, payload)

    def _put_batch_reference(
        self, conn: sqlite3.Connection, kind: str, key: str, payload: bytes,
    ) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        self._put_immutable(conn, "batch-payload/v1", digest, payload)
        self._put_immutable(conn, kind, key, digest.encode("ascii"))

    def _put_immutable(
        self, conn: sqlite3.Connection, kind: str, key: str, payload: bytes,
    ) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        row = conn.execute(
            "SELECT payload, digest FROM prepared_crawl_rows "
            "WHERE source = ? AND kind = ? AND key = ?", (self.source, kind, key),
        ).fetchone()
        if row is not None:
            if (row["payload"], row["digest"]) != (payload, digest):
                raise PendingCrawlConflict("Prepared publication intent changed")
            return
        conn.execute(
            "INSERT INTO prepared_crawl_rows VALUES (?, ?, ?, ?, ?)",
            (self.source, kind, key, payload, digest),
        )

    def _require_batch_plan(self, conn: sqlite3.Connection, *, writing: bool = False) -> str:
        plan = self._batch_plan
        if plan is None:
            if writing and conn.execute(
                "SELECT 1 FROM prepared_crawl_rows WHERE source = ? "
                "AND kind IN ('batch-plan/v1', 'batch-plan/v2') LIMIT 1", (self.source,),
            ).fetchone() is not None:
                raise PendingCrawlConflict("Prepared batch plan belongs to a bound application")
            return "batch"
        row = conn.execute(
            "SELECT payload, digest FROM prepared_crawl_rows "
            "WHERE source = ? AND kind = 'batch-plan/v2' AND key = ?",
            (self.source, plan.decode("ascii")),
        ).fetchone()
        if row is None or (row["payload"], row["digest"]) != (
            plan, hashlib.sha256(plan).hexdigest(),
        ):
            raise PendingCrawlConflict("Prepared batch plan is missing or corrupt")
        return "batch-ref/v1:" + plan.decode("ascii")

    def reset_batches(
        self, *, bind_plan: bool = False, guard: AbstractContextManager[None] | None = None,
    ) -> None:
        """Reset legacy batches or bind a fresh handle to its own immutable plan."""
        if self._batch_plan is not None:
            raise PendingCrawlConflict("Prepared batch plan is already bound")
        plan = uuid.uuid4().hex.encode("ascii") if bind_plan else None
        with closing(self.paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            with guard if guard is not None else nullcontext():
                self._require_parent(conn)
                if not bind_plan:
                    self._require_batch_plan(conn, writing=True)
                    conn.execute(
                        "DELETE FROM prepared_crawl_rows WHERE source = ? AND kind = 'batch'",
                        (self.source,),
                    )
                if plan is not None:
                    conn.execute(
                        "INSERT INTO prepared_crawl_rows VALUES (?, ?, ?, ?, ?)",
                        (self.source, "batch-plan/v2", plan.decode("ascii"), plan,
                         hashlib.sha256(plan).hexdigest()),
                    )
        self._batch_plan = plan

    def put_batch(self, index: int, batch: FileBatch) -> None:
        init_fields = [field.name for field in fields(Chunk) if field.init]
        payload = json.dumps({
            "version": 1,
            "chunks": [{name: getattr(chunk, name) for name in init_fields} for chunk in batch.chunks],
            "checkpoints": batch.checkpoints,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.put("batch", [(str(index), payload)])

    def batches(self, count: int) -> Iterator[FileBatch]:
        if self._batch_plan is not None:
            self.get("batch", [])
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

    def vectors(self, keys: list[str], dim: int) -> dict[str, np.ndarray[Any, Any]]:
        result = {}
        for key, payload in self.get("vector", keys).items():
            if len(payload) != dim * 4:
                raise ValueError("Prepared embedding has an incompatible dimension")
            vector = np.frombuffer(payload, dtype="<f4").copy()
            if not np.isfinite(vector).all():
                raise ValueError("Prepared embedding is not finite")
            result[key] = vector
        return result

    def put_vectors(self, keys: list[str], vectors: np.ndarray[Any, Any], dim: int) -> None:
        vectors = np.asarray(vectors, dtype="<f4")
        if vectors.shape != (len(keys), dim) or not np.isfinite(vectors).all():
            raise ValueError("Embedding response has an incompatible shape or nonfinite values")
        self.put("vector", [(key, vector.tobytes()) for key, vector in zip(keys, vectors, strict=True)])
