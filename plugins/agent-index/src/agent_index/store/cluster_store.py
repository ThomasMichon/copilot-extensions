"""SQLite-backed store for similarity clusters (the Phase 3 artifact).

Clusters are a derived artifact, refreshed by the offline post-index pass.
They live in their own ``clusters.db`` (separate from the LanceDB vectors and
``tasks.db``) so a recluster never touches the index and the artifact survives
reindexing.  Each public method opens its own WAL connection, mirroring
``TaskStore``, so callers may use ``asyncio.to_thread()`` safely.

``cluster_id`` is a deterministic hash of the slice + sorted membership, so a
stable cluster keeps the same id across runs (useful for deep links).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.store.clustering import Cluster

log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS clusters (
    cluster_id          TEXT PRIMARY KEY,
    bucket              TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    size                INTEGER NOT NULL,
    rep_source          TEXT NOT NULL,
    rep_file_path       TEXT NOT NULL,
    has_exact_dupes     INTEGER NOT NULL DEFAULT 0,
    avg_score           REAL NOT NULL DEFAULT 0.0,
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id      TEXT NOT NULL,
    source          TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0.0,
    is_exact_dupe   INTEGER NOT NULL DEFAULT 0,
    member_rank     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cluster_id, source, file_path)
);

CREATE INDEX IF NOT EXISTS idx_clusters_bucket
    ON clusters(bucket, model_id);
CREATE INDEX IF NOT EXISTS idx_clusters_size
    ON clusters(size);
CREATE INDEX IF NOT EXISTS idx_members_item
    ON cluster_members(source, file_path);
"""


@dataclass(frozen=True)
class StoredMember:
    source: str
    file_path: str
    score: float
    is_exact_dupe: bool


@dataclass(frozen=True)
class StoredCluster:
    cluster_id: str
    bucket: str
    model_id: str
    size: int
    rep_source: str
    rep_file_path: str
    has_exact_dupes: bool
    avg_score: float
    created_at: float
    members: tuple[StoredMember, ...]


def cluster_id_for(cluster: Cluster) -> str:
    """Deterministic id from the slice + sorted membership."""
    keys = sorted(f"{m.source}\x01{m.file_path}" for m in cluster.members)
    raw = "\x00".join([cluster.bucket, cluster.model_id, *keys])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class ClusterStore:
    """Persisted similarity clusters with simple query access."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # -- write ---------------------------------------------------------------

    def replace_all(self, clusters: list[Cluster]) -> int:
        """Atomically swap in a fresh full set of clusters.

        A clustering run is a full recompute, so the simplest correct refresh
        is to wipe and reinsert in one transaction.  Returns the number of
        clusters written.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM cluster_members")
            conn.execute("DELETE FROM clusters")
            for cluster in clusters:
                cid = cluster_id_for(cluster)
                rep = cluster.representative
                conn.execute(
                    "INSERT OR REPLACE INTO clusters "
                    "(cluster_id, bucket, model_id, size, rep_source, "
                    "rep_file_path, has_exact_dupes, avg_score, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cid,
                        cluster.bucket,
                        cluster.model_id,
                        cluster.size,
                        rep.source,
                        rep.file_path,
                        int(cluster.has_exact_dupes),
                        cluster.avg_score,
                        now,
                    ),
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO cluster_members "
                    "(cluster_id, source, file_path, score, is_exact_dupe, "
                    "member_rank) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            cid,
                            m.source,
                            m.file_path,
                            m.score,
                            int(m.is_exact_dupe),
                            rank,
                        )
                        for rank, m in enumerate(cluster.members)
                    ],
                )
            conn.commit()
            return len(clusters)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- read ----------------------------------------------------------------

    def _members(
        self, conn: sqlite3.Connection, cluster_id: str,
    ) -> tuple[StoredMember, ...]:
        rows = conn.execute(
            "SELECT source, file_path, score, is_exact_dupe "
            "FROM cluster_members WHERE cluster_id = ? "
            "ORDER BY member_rank",
            (cluster_id,),
        ).fetchall()
        return tuple(
            StoredMember(
                source=r["source"],
                file_path=r["file_path"],
                score=r["score"],
                is_exact_dupe=bool(r["is_exact_dupe"]),
            )
            for r in rows
        )

    def _hydrate(
        self, conn: sqlite3.Connection, row: sqlite3.Row,
    ) -> StoredCluster:
        return StoredCluster(
            cluster_id=row["cluster_id"],
            bucket=row["bucket"],
            model_id=row["model_id"],
            size=row["size"],
            rep_source=row["rep_source"],
            rep_file_path=row["rep_file_path"],
            has_exact_dupes=bool(row["has_exact_dupes"]),
            avg_score=row["avg_score"],
            created_at=row["created_at"],
            members=self._members(conn, row["cluster_id"]),
        )

    def list_clusters(
        self,
        *,
        bucket: str | None = None,
        model_id: str | None = None,
        has_exact_dupes: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[StoredCluster]:
        """List clusters (largest first), optionally filtered."""
        where: list[str] = []
        params: list[object] = []
        if bucket is not None:
            where.append("bucket = ?")
            params.append(bucket)
        if model_id is not None:
            where.append("model_id = ?")
            params.append(model_id)
        if has_exact_dupes is not None:
            where.append("has_exact_dupes = ?")
            params.append(int(has_exact_dupes))
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM clusters {clause} "
                "ORDER BY size DESC, avg_score DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [self._hydrate(conn, r) for r in rows]
        finally:
            conn.close()

    def get_cluster(self, cluster_id: str) -> StoredCluster | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,),
            ).fetchone()
            return self._hydrate(conn, row) if row else None
        finally:
            conn.close()

    def cluster_for_item(
        self, source: str, file_path: str,
    ) -> StoredCluster | None:
        """Return the cluster an item belongs to, if any."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT c.* FROM clusters c "
                "JOIN cluster_members m ON m.cluster_id = c.cluster_id "
                "WHERE m.source = ? AND m.file_path = ? LIMIT 1",
                (source, file_path),
            ).fetchone()
            return self._hydrate(conn, row) if row else None
        finally:
            conn.close()

    def stats(self) -> dict[str, object]:
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM clusters"
            ).fetchone()["n"]
            members = conn.execute(
                "SELECT COUNT(*) AS n FROM cluster_members"
            ).fetchone()["n"]
            exact = conn.execute(
                "SELECT COUNT(*) AS n FROM clusters WHERE has_exact_dupes = 1"
            ).fetchone()["n"]
            buckets = conn.execute(
                "SELECT bucket, model_id, COUNT(*) AS n FROM clusters "
                "GROUP BY bucket, model_id"
            ).fetchall()
            return {
                "clusters": total,
                "clustered_items": members,
                "clusters_with_exact_dupes": exact,
                "by_slice": {
                    f"{r['bucket']}|{r['model_id']}": r["n"] for r in buckets
                },
            }
        finally:
            conn.close()
