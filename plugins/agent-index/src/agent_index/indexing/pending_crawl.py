"""Replay acquired source work before observing a newer upstream delta."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Protocol

from agent_index.indexing.manifest import CrawlManifest, CrawlStats, PollRefresh
from agent_index.indexing.poll_refresh import PollRefreshJournal, validate_refresh
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry, SourceConnector
from agent_index.sources.delta import DeletedFile, SourceDelta


class ApplyManifest(Protocol):
    def __call__(self, *, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]: ...


class PendingCrawlConflict(RuntimeError):
    """Acquired work must finish before another owner mutates or retires its data."""


def recovery_scope(config_scope: str, connector: SourceConnector) -> str:
    """Bind stable account configuration to the same resolved connector used to crawl."""
    identity = getattr(connector, "recovery_identity", None)
    if identity is None:
        if any(
            callable(getattr(connector, name, None))
            for name in ("discover_delta", "discover_hints", "discover_delta_page")
        ):
            raise ValueError("Delta connector must declare its effective recovery_identity")
        identity = (connector.source_name,)
    if not isinstance(identity, tuple) or not identity or any(
        not isinstance(part, str) or not part for part in identity
    ):
        raise ValueError("Connector recovery identity must be a nonempty tuple of nonsecret strings")
    value = (config_scope, connector.source_name, identity)
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


class PendingCrawls(PollRefreshJournal):
    """A write-ahead acquisition journal, not another source-cursor authority."""

    def __init__(self, paths: PathIndex) -> None:
        self._paths = paths
        with closing(paths._connect()) as conn, conn:
            self.initialize_refresh(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pending_crawls "
                "(source TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL)",
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS embedding_backfills "
                "(source TEXT PRIMARY KEY, payload TEXT NOT NULL, complete INTEGER NOT NULL)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS pending_crawl_family ON pending_crawls "
                "(COALESCE(json_extract(payload, '$.manifest.accounting_family'), "
                "json_extract(payload, '$.manifest.stored_family'), source))",
            )

    def assert_family_available(self, source: str, family: str) -> None:
        with closing(self._paths._connect()) as conn:
            self._assert_family_available(conn, source, family)

    @staticmethod
    def _assert_family_available(conn: sqlite3.Connection, source: str, family: str) -> None:
        expression = (
            "COALESCE(json_extract(payload, '$.manifest.accounting_family'), "
            "json_extract(payload, '$.manifest.stored_family'), source)"
        )
        ancestors = [family[:i] for i, char in enumerate(family) if char == ":"] + [family]
        placeholders = ", ".join("?" for _ in ancestors)
        row = conn.execute(
            f"SELECT source FROM pending_crawls WHERE source != ? AND "
            f"({expression} IN ({placeholders}) OR "
            f"({expression} >= ? AND {expression} < ?)) LIMIT 1",
            (source, *ancestors, family + ":", family + ";"),
        ).fetchone()
        if row is not None:
            raise PendingCrawlConflict(
                f"Complete pending crawl {row['source']!r} with its original configuration "
                f"before indexing another alias for {family!r}",
            )
        row = conn.execute(
            f"SELECT source FROM poll_refresh WHERE source != ? AND "
            f"(family IN ({placeholders}) OR (family >= ? AND family < ?)) LIMIT 1",
            (source, *ancestors, family + ":", family + ";"),
        ).fetchone()
        if row is not None:
            raise PendingCrawlConflict(
                f"Refresh hinted candidates for {row['source']!r} before indexing another alias",
            )

    def assert_gc_available(self) -> None:
        with closing(self._paths._connect()) as conn:
            row = conn.execute("SELECT source FROM pending_crawls LIMIT 1").fetchone()
        if row is not None:
            raise PendingCrawlConflict(
                f"Complete pending crawl {row['source']!r} with its original configuration "
                "before source garbage collection",
            )
        with closing(self._paths._connect()) as conn:
            row = conn.execute("SELECT source FROM poll_refresh LIMIT 1").fetchone()
        if row is not None:
            raise PendingCrawlConflict(
                f"Refresh hinted candidates for {row['source']!r} before source garbage collection",
            )
        with closing(self._paths._connect()) as conn:
            row = conn.execute(
                "SELECT source FROM embedding_backfills WHERE complete = 0 LIMIT 1",
            ).fetchone()
        if row is not None:
            raise PendingCrawlConflict(
                f"Complete, explicitly restart or discard embedding backfill {row['source']!r} "
                "before source garbage collection",
            )

    def sources(self) -> list[str]:
        with closing(self._paths._connect()) as conn:
            return [row["source"] for row in conn.execute(
                "SELECT source FROM pending_crawls UNION SELECT source FROM poll_refresh ORDER BY source",
            )]

    def backfill_sources(self) -> list[str]:
        """Unfinished derived jobs defer retirement, not ordinary acquisition."""
        with closing(self._paths._connect()) as conn:
            return [row["source"] for row in conn.execute(
                "SELECT source FROM embedding_backfills WHERE complete = 0 ORDER BY source",
            )]

    def read_payload(self, source: str, scope: str) -> str | None:
        with closing(self._paths._connect()) as conn, conn:
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
        if row is None:
            return None
        if row["scope"] != scope:
            raise ValueError("Pending crawl belongs to a different source/account configuration")
        return row["payload"]

    def compare_and_save(
        self, source: str, *, scope: str, payload: str, expected: str | None, family: str,
    ) -> None:
        """Publish acquisition progress under the same family exclusion and journal key."""
        with closing(self._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_family_available(conn, source, family)
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if (row is None and expected is not None) or (
                row is not None and (row["scope"] != scope or row["payload"] != expected)
            ):
                raise PendingCrawlConflict("Pending acquisition changed before progress publication")
            if row is None:
                conn.execute(
                    "INSERT INTO pending_crawls (source, scope, payload) VALUES (?, ?, ?)",
                    (source, scope, payload),
                )
            else:
                conn.execute(
                    "UPDATE pending_crawls SET payload = ? WHERE source = ?", (payload, source),
                )

    def load(self, source: str, scope: str) -> tuple[CrawlManifest, bool] | None:
        payload = self.read_payload(source, scope)
        if payload is None:
            return None
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("version") == 2:
            from agent_index.indexing.paged_crawl import decode_acquisition

            decode_acquisition(payload, source)
            return None
        if not isinstance(data, dict) or set(data) != {"version", "full", "manifest"}:
            raise ValueError("Invalid pending crawl envelope")
        if type(data["version"]) is not int or data["version"] not in (1, 3) or type(data["full"]) is not bool:
            raise ValueError("Unsupported pending crawl version or mode")
        manifest = self.decode_manifest(data["manifest"], source)
        if (data["version"] == 3) != (manifest.poll_refresh is not None):
            raise ValueError("Pending polling refresh requires the expanded journal version")
        return manifest, data["full"]

    def discard_acquisition(
        self, source: str, *, scope: str, expected_payload: str, family: str,
    ) -> None:
        """Explicitly abandon only the exact unapplied phase; complete work must replay."""
        from agent_index.indexing.paged_crawl import decode_acquisition

        with closing(self._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if row is None or row["scope"] != scope or row["payload"] != expected_payload:
                raise PendingCrawlConflict("Pending acquisition changed before explicit discard")
            progress = decode_acquisition(row["payload"], source)
            if progress.manifest.accounting_family != family:
                raise ValueError("Pending acquisition belongs to a different source family")
            conn.execute("DELETE FROM pending_crawls WHERE source = ?", (source,))

    @classmethod
    def decode_manifest(cls, raw: object, source: str) -> CrawlManifest:
        fields = set(CrawlManifest.__dataclass_fields__)
        if not isinstance(raw, dict) or set(raw) not in (fields, fields - {"poll_refresh"}):
            raise ValueError("Invalid pending crawl manifest")
        if raw["source"] != source:
            raise ValueError("Pending crawl source does not match its journal key")
        refresh = raw.get("poll_refresh")
        if refresh is not None and (
            not isinstance(refresh, dict) or set(refresh) != {"retain", "paths", "history"}
        ):
            raise ValueError("Invalid pending polling-refresh metadata")
        manifest = CrawlManifest(
            source=source,
            upserts=[FileEntry(**item) for item in raw["upserts"]],
            deletions=[DeletedFile(**item) for item in raw["deletions"]],
            commit=raw["commit"], stats=CrawlStats(**raw["stats"]),
            stored_family=raw["stored_family"], accounting_family=raw["accounting_family"],
            poll_refresh=PollRefresh(**raw["poll_refresh"]) if raw.get("poll_refresh") is not None else None,
        )
        cls._validate(manifest)
        return manifest

    @staticmethod
    def _validate(manifest: CrawlManifest) -> None:
        validate_refresh(manifest.poll_refresh)
        if manifest.poll_refresh is not None:
            family = manifest.accounting_family or manifest.stored_family or manifest.source
            for source, candidates in (
                (family, manifest.poll_refresh.paths),
                (family + ":commits", manifest.poll_refresh.history),
            ):
                acquired = {item.path for item in manifest.upserts if item.source == source} | {
                    item.path for item in manifest.deletions if item.source == source
                }
                if not set(candidates) <= acquired:
                    raise ValueError("Polling refresh did not acquire every retained candidate")
        SourceDelta(
            upserts=manifest.upserts, deletions=manifest.deletions, commit=manifest.commit,
        ).validate(manifest.accounting_family or manifest.stored_family or manifest.source)

    def save(self, manifest: CrawlManifest, *, scope: str, full: bool) -> None:
        self._validate(manifest)
        payload = json.dumps({
            "version": 3 if manifest.poll_refresh is not None else 1,
            "full": full, "manifest": self.manifest_data(manifest),
        })
        with closing(self._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_family_available(
                conn, manifest.source,
                manifest.accounting_family or manifest.stored_family or manifest.source,
            )
            conn.execute(
                "INSERT INTO pending_crawls (source, scope, payload) VALUES (?, ?, ?)",
                (manifest.source, scope, payload),
            )
            self.retain_refresh(conn, manifest, scope)

    @staticmethod
    def manifest_data(manifest: CrawlManifest) -> dict:
        data = asdict(manifest)
        if manifest.poll_refresh is None:
            data.pop("poll_refresh")
        return data

    def clear(self, source: str, *, manifest: CrawlManifest | None = None, scope: str | None = None) -> None:
        with closing(self._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if manifest is not None:
                assert scope is not None
                row = conn.execute(
                    "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
                ).fetchone()
                if row is None:
                    return
                data = json.loads(row["payload"])
                if (
                    row["scope"] != scope
                    or self.decode_manifest(data["manifest"], source) != manifest
                ):
                    raise PendingCrawlConflict("Pending crawl changed before completion")
                self.cover_refresh(conn, manifest, scope)
            conn.execute("DELETE FROM pending_crawls WHERE source = ?", (source,))


def recover_source(
    source: str, *, scope: str, state: IndexState, state_file: Path,
    paths: PathIndex, full: bool, acquire: Callable[[], CrawlManifest], apply: ApplyManifest,
    family: str | None = None,
) -> tuple[int, int, int]:
    """Publish pinned work and its scope before admitting another scoped acquisition."""
    journal = PendingCrawls(paths)
    journal.assert_family_available(source, family or source)
    totals = (0, 0, 0)

    def publish(manifest: CrawlManifest, *, full: bool) -> tuple[int, int, int]:
        previous = state.sources.get(source)
        previous = replace(previous) if previous is not None else None
        previous_family = state.stored_families.get(source)
        previous_total = state.total_chunks
        try:
            result = apply(manifest=manifest, full=full)
            state.sources.setdefault(source, SourceState()).acquisition_scope = scope
            state.save(state_file)
        except BaseException:
            if previous is None:
                state.sources.pop(source, None)
            else:
                state.sources[source] = previous
            if previous_family is None:
                state.stored_families.pop(source, None)
            else:
                state.stored_families[source] = previous_family
            state.total_chunks = previous_total
            raise
        return result

    pending = journal.load(source, scope)
    if pending is not None:
        manifest, pending_full = pending
        totals = publish(manifest, full=pending_full)
        journal.clear(source, manifest=manifest, scope=scope)

    state.require_acquisition_scope(source, scope, full=full)
    manifest = acquire()
    acquired = journal.load(source, scope)
    if acquired is not None and acquired != (manifest, full):
        raise PendingCrawlConflict("Acquired manifest does not match pending publication")
    if acquired is None and (not manifest.is_empty or manifest.poll_refresh is not None):
        journal.save(manifest, scope=scope, full=full)
    current = publish(manifest, full=full)
    if acquired is not None or not manifest.is_empty or manifest.poll_refresh is not None:
        journal.clear(source, manifest=manifest, scope=scope)
    return totals[0] + current[0], totals[1] + current[1], totals[2] + current[2]
