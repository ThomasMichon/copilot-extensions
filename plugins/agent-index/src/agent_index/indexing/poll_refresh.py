"""Pending-crawl-owned candidates that must outlive a hint's unchanged cursor."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from typing import TYPE_CHECKING

from agent_index.indexing.manifest import CrawlManifest, PollRefresh

if TYPE_CHECKING:
    from agent_index.indexing.path_index import PathIndex
    from agent_index.sources.delta import SourceDelta


def retained_candidates(delta: SourceDelta, family: str) -> PollRefresh | None:
    candidates = []
    for source in (family, family + ":commits"):
        identities = {item.path for item in delta.upserts if item.source == source} | {
            item.path for item in delta.deletions if item.source == source
        }
        candidates.append({path: uuid.uuid4().hex for path in sorted(identities)})
    paths, history = candidates
    return PollRefresh(True, paths, history) if paths or history else None


def require_refresh_support(discover: object) -> Callable[..., SourceDelta]:
    versions = getattr(discover, "poll_refresh_versions", ())
    if not callable(discover) or not isinstance(versions, (tuple, list, set, frozenset)) or not any(
        type(version) is int and version == 1 for version in versions
    ):
        raise ValueError("Selected connector cannot refresh outstanding hinted candidates")
    return discover


def validate_refresh(refresh: PollRefresh | None) -> None:
    if refresh is None:
        return
    if (
        not isinstance(refresh, PollRefresh) or type(refresh.retain) is not bool
        or not isinstance(refresh.paths, dict) or not isinstance(refresh.history, dict)
        or not (refresh.paths or refresh.history)
    ):
        raise ValueError("Invalid pending polling-refresh candidates")
    for path, generation in (*refresh.paths.items(), *refresh.history.items()):
        if (
            not isinstance(path, str) or not path or "\0" in path
            or not isinstance(generation, str) or uuid.UUID(generation).hex != generation
        ):
            raise ValueError("Invalid pending polling-refresh identity")


class PollRefreshJournal:
    """Implementation seam of PendingCrawls, using its same database/transactions."""

    _paths: PathIndex

    @staticmethod
    def initialize_refresh(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS poll_refresh "
            "(source TEXT NOT NULL, scope TEXT NOT NULL, family TEXT NOT NULL, "
            "kind TEXT NOT NULL CHECK (kind IN ('file', 'history')), "
            "path TEXT NOT NULL, generation TEXT NOT NULL, PRIMARY KEY (source, kind, path))",
        )
        conn.execute("CREATE INDEX IF NOT EXISTS poll_refresh_family ON poll_refresh (family)")

    def refresh_candidates(self, source: str, scope: str, family: str) -> PollRefresh | None:
        with closing(self._paths._connect()) as conn:
            rows = conn.execute(
                "SELECT scope, family, kind, path, generation FROM poll_refresh WHERE source = ?",
                (source,),
            ).fetchall()
        if any(row["scope"] != scope or row["family"] != family for row in rows):
            raise ValueError("Hinted candidates belong to a different source/account configuration")
        result = PollRefresh(
            False,
            {row["path"]: row["generation"] for row in rows if row["kind"] == "file"},
            {row["path"]: row["generation"] for row in rows if row["kind"] == "history"},
        ) if rows else None
        validate_refresh(result)
        return result

    @staticmethod
    def retain_refresh(conn: sqlite3.Connection, manifest: CrawlManifest, scope: str) -> None:
        refresh = manifest.poll_refresh
        if refresh is None or not refresh.retain:
            return
        family = manifest.accounting_family or manifest.stored_family or manifest.source
        if conn.execute(
            "SELECT 1 FROM poll_refresh WHERE source = ? AND (scope != ? OR family != ?) LIMIT 1",
            (manifest.source, scope, family),
        ).fetchone() is not None:
            raise ValueError("Hinted candidates belong to a different source/account configuration")
        conn.executemany(
            "INSERT INTO poll_refresh (source, scope, family, kind, path, generation) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, kind, path) DO UPDATE SET generation = excluded.generation",
            [
                (manifest.source, scope, family, kind, path, generation)
                for kind, candidates in (("file", refresh.paths), ("history", refresh.history))
                for path, generation in candidates.items()
            ],
        )

    @staticmethod
    def cover_refresh(conn: sqlite3.Connection, manifest: CrawlManifest, scope: str) -> None:
        refresh = manifest.poll_refresh
        if refresh is None or refresh.retain:
            return
        conn.executemany(
            "DELETE FROM poll_refresh WHERE source = ? AND scope = ? "
            "AND kind = ? AND path = ? AND generation = ?",
            [
                (manifest.source, scope, kind, path, generation)
                for kind, candidates in (("file", refresh.paths), ("history", refresh.history))
                for path, generation in candidates.items()
            ],
        )
