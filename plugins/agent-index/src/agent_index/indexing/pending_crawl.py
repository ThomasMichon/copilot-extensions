"""Replay acquired source work before observing a newer upstream delta."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager, closing, nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Protocol, cast

from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.component_manifest import (
    ComponentVersion, require_unordered_application, validate_components,
)
from agent_index.indexing.manifest import CrawlManifest, CrawlStats, PollRefresh
from agent_index.indexing.poll_refresh import PollRefreshJournal, validate_refresh
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.point_claim import PendingPointClaim, protect_point_parents
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry, SourceConnector
from agent_index.sources.delta import DeletedFile, SourceDelta


class ApplyManifest(Protocol):
    def __call__(self, *, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]: ...


class PrepareManifest(Protocol):
    def __call__(self, *, manifest: CrawlManifest, full: bool) -> ApplyManifest: ...


class PendingCrawlConflict(RuntimeError):
    """Acquired work must finish before another owner mutates or retires its data."""


def recovery_scope(config_scope: str, connector: SourceConnector) -> str:
    """Bind stable account configuration to the same resolved connector used to crawl."""
    identity = getattr(connector, "recovery_identity", None)
    if identity is None:
        if any(
            callable(getattr(connector, name, None))
            for name in (
                "discover_delta", "discover_hints", "discover_hints_page",
                "discover_delta_page", "discover_snapshot_page",
            )
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
            protect_point_parents(conn)
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
        return cast(str, row["payload"])

    def compare_and_save(
        self, source: str, *, scope: str, payload: str, expected: str | None, family: str,
    ) -> None:
        """Publish acquisition progress under the same family exclusion and journal key."""
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("version") == 9:
            raise ValueError("Point-claim pending publication requires explicit claim binding")
        completed = None
        if isinstance(data, dict) and data.get("version") == 8:
            decoded = self.decode_pending(payload, source)
            assert decoded is not None
            completed = decoded[0]
        elif isinstance(data, dict) and data.get("version") in (3, 6, 7):
            completed = self.decode_manifest(data["manifest"], source)
        expected_components = expected is not None and json.loads(expected).get("version") in (8, 9)
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
            if expected_components:
                if payload != expected:
                    raise ValueError(
                        "Component pending work cannot be downgraded or replaced "
                        "before ordered completion"
                    )
                return
            if row is None:
                conn.execute(
                    "INSERT INTO pending_crawls (source, scope, payload) VALUES (?, ?, ?)",
                    (source, scope, payload),
                )
            else:
                conn.execute(
                    "UPDATE pending_crawls SET payload = ? WHERE source = ?", (payload, source),
                )
            if completed is not None:
                self.retain_refresh(conn, completed, scope)

    def load(self, source: str, scope: str) -> tuple[CrawlManifest, bool] | None:
        payload = self.read_payload(source, scope)
        if payload is None:
            return None
        return self.decode_pending(payload, source)

    @classmethod
    def decode_pending(
        cls, payload: str, source: str, *, required_pages: bool = False,
        requested_full: bool = False, change_hints: ChangeHints | None = None,
    ) -> tuple[CrawlManifest, bool] | None:
        """Decode one observed payload without rereading a possibly newer generation."""
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("version") == 9:
            raise ValueError("Point-claim pending work requires explicit claim binding")
        if required_pages:
            from agent_index.indexing.acquisition_requirement import require_pending_pages

            require_pending_pages(data, full=requested_full, change_hints=change_hints)
        if isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 8:
            if set(data) != {"version", "full", "manifest"} or data["full"] is not False:
                raise ValueError("Invalid pending component envelope")
            return cls.decode_manifest(data["manifest"], source, components=True), False
        if isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 7:
            if set(data) != {"version", "full", "manifest", "page_protocol"} or (
                type(data["full"]) is not bool
                or data["page_protocol"] != ("full" if data["full"] else "incremental")
            ):
                raise ValueError("Invalid pending page-protocol provenance")
            return cls.decode_manifest(data["manifest"], source), data["full"]
        if isinstance(data, dict) and data.get("version") in (2, 4, 5, 6):
            from agent_index.indexing.paged_crawl import decode_acquisition
            from agent_index.indexing.snapshot_crawl import decode_snapshot

            if data["version"] == 4:
                decode_snapshot(payload, source)
            else:
                progress = decode_acquisition(payload, source)
                if data["version"] == 6:
                    return progress.manifest, False
            return None
        if not isinstance(data, dict) or set(data) != {"version", "full", "manifest"}:
            raise ValueError("Invalid pending crawl envelope")
        if type(data["version"]) is not int or data["version"] not in (1, 3) or type(data["full"]) is not bool:
            raise ValueError("Unsupported pending crawl version or mode")
        manifest = cls.decode_manifest(data["manifest"], source)
        if (data["version"] == 3) != (manifest.poll_refresh is not None):
            raise ValueError("Pending polling refresh requires the expanded journal version")
        return manifest, data["full"]

    @classmethod
    def decode_point_pending(cls, payload: str, source: str) -> tuple[CrawlManifest, PendingPointClaim]:
        """Inspect bound work explicitly, without admitting ordinary application."""
        def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate pending point-claim field")
                result[key] = value
            return result

        data = json.loads(payload, object_pairs_hook=unique_fields)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "full", "manifest", "point_claim"}
            or type(data["version"]) is not int or data["version"] != 9
            or data["full"] is not False
        ):
            raise ValueError("Invalid pending point-claim envelope")
        manifest = cls.decode_manifest(data["manifest"], source, components=True)
        claim = PendingPointClaim.from_dict(data["point_claim"])
        if (
            claim.hints.source != source
            or manifest.accounting_family != claim.hints.canonical_family
            or manifest.stored_family not in (None, claim.hints.canonical_family)
            or manifest.poll_refresh is not None
        ):
            raise ValueError("Pending point claim requires its exact source and component family")
        return manifest, claim

    def discard_acquisition(
        self, source: str, *, scope: str, expected_payload: str, family: str,
    ) -> None:
        """Explicitly abandon only the exact unapplied phase; complete work must replay."""
        from agent_index.indexing.paged_crawl import decode_acquisition
        from agent_index.indexing.snapshot_crawl import decode_snapshot

        with closing(self._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if row is None or row["scope"] != scope or row["payload"] != expected_payload:
                raise PendingCrawlConflict("Pending acquisition changed before explicit discard")
            data = json.loads(row["payload"])
            if isinstance(data, dict) and data.get("version") in (6, 7, 8, 9):
                raise ValueError("Completed acquisition requires mandatory replay")
            progress = (
                decode_snapshot(row["payload"], source)
                if isinstance(data, dict) and data.get("version") == 4
                else decode_acquisition(row["payload"], source)
            )
            if progress.manifest.accounting_family != family:
                raise ValueError("Pending acquisition belongs to a different source family")
            conn.execute("DELETE FROM pending_crawls WHERE source = ?", (source,))

    @classmethod
    def decode_manifest(cls, raw: object, source: str, *, components: bool = False) -> CrawlManifest:
        fields = set(CrawlManifest.__dataclass_fields__) - {"component_versions"}
        if components:
            fields.add("component_versions")
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
            component_versions=(
                [ComponentVersion.from_dict(item) for item in raw["component_versions"]]
                if components and isinstance(raw.get("component_versions"), list) else None
            ),
        )
        if components and manifest.component_versions is None:
            raise ValueError("Pending component envelope requires component versions")
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
        validate_components(manifest)

    def save(
        self, manifest: CrawlManifest, *, scope: str, full: bool,
        point_claim: PendingPointClaim | None = None,
    ) -> None:
        self._validate(manifest)
        if manifest.component_versions is not None and full:
            raise ValueError("Component recovery cannot authorize full reconciliation")
        data: dict[str, Any] = {
            "version": 8 if manifest.component_versions is not None else (
                3 if manifest.poll_refresh is not None else 1
            ),
            "full": full, "manifest": self.manifest_data(manifest),
        }
        if point_claim is not None:
            if type(point_claim) is not PendingPointClaim:
                raise ValueError("Pending point work requires a complete typed claim")
            data.update(version=9, point_claim=point_claim.to_dict())
        payload = json.dumps(data)
        if point_claim is not None:
            self.decode_point_pending(payload, manifest.source)
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
    def manifest_data(manifest: CrawlManifest) -> dict[str, Any]:
        data = asdict(manifest)
        if manifest.poll_refresh is None:
            data.pop("poll_refresh")
        if manifest.component_versions is None:
            data.pop("component_versions")
        else:
            data["component_versions"] = [item.to_dict() for item in manifest.component_versions]
        return data

    def clear(
        self, source: str, *, manifest: CrawlManifest | None = None,
        scope: str | None = None, expected_payload: str | None = None,
    ) -> None:
        with closing(self._paths._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if row is None:
                return
            data = json.loads(row["payload"])
            if data.get("version") in (8, 9):
                raise ValueError("Component pending completion is not supported")
            if manifest is not None:
                assert scope is not None
                if (
                    row["scope"] != scope
                    or expected_payload is not None and row["payload"] != expected_payload
                    or self.decode_manifest(data["manifest"], source) != manifest
                ):
                    raise PendingCrawlConflict("Pending crawl changed before completion")
                self.cover_refresh(conn, manifest, scope)
            conn.execute("DELETE FROM pending_crawls WHERE source = ?", (source,))

    @staticmethod
    def _retire_point_claim(
        conn: sqlite3.Connection, *, source: str, scope: str, payload: str,
    ) -> None:
        from agent_index.indexing.point_claim import _point_completion_transition

        row = conn.execute(
            "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
        ).fetchone()
        if row is None or (row["scope"], row["payload"]) != (scope, payload):
            raise PendingCrawlConflict("Point pending parent changed before retirement")
        PendingCrawls.decode_point_pending(payload, source)
        with _point_completion_transition(conn, "pending_point_claim_delete"):
            conn.execute("DELETE FROM pending_crawls WHERE source = ?", (source,))


def recover_source(
    source: str, *, scope: str, state: IndexState, state_file: Path,
    paths: PathIndex, full: bool, acquire: Callable[[], CrawlManifest], apply: ApplyManifest,
    family: str | None = None,
    publication: Callable[[], AbstractContextManager[None]] | None = None,
    prepare: PrepareManifest | None = None,
    change_hints: ChangeHints | None = None,
    require_paged_acquisition: bool = False,
) -> tuple[int, int, int]:
    """Publish pinned work and its scope before admitting another scoped acquisition."""
    journal = PendingCrawls(paths)
    journal.assert_family_available(source, family or source)
    totals = (0, 0, 0)

    def apply_and_save(
        manifest: CrawlManifest, *, full: bool, application: ApplyManifest,
    ) -> tuple[int, int, int]:
        previous = state.sources.get(source)
        previous = replace(previous) if previous is not None else None
        previous_family = state.stored_families.get(source)
        previous_total = state.total_chunks
        completed_family = manifest.accounting_family or manifest.stored_family or family or source
        had_completed = completed_family in state.completed_family_cursors
        previous_completed = state.completed_family_cursors.get(completed_family)
        try:
            result = application(manifest=manifest, full=full)
            state.sources.setdefault(source, SourceState()).acquisition_scope = scope
            state.completed_family_cursors[completed_family] = manifest.commit
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
            if had_completed:
                state.completed_family_cursors[completed_family] = previous_completed
            else:
                state.completed_family_cursors.pop(completed_family, None)
            raise
        return result

    def publish(
        manifest: CrawlManifest, *, full: bool, clear: bool, expected_payload: str | None = None,
    ) -> tuple[int, int, int]:
        require_unordered_application(manifest)
        application = prepare(manifest=manifest, full=full) if prepare is not None else apply
        with publication() if publication is not None else nullcontext():
            result = apply_and_save(manifest, full=full, application=application)
            if clear:
                journal.clear(
                    source, manifest=manifest, scope=scope, expected_payload=expected_payload,
                )
            return result

    pending_raw = journal.read_payload(source, scope)
    pending = journal.decode_pending(
        pending_raw, source, required_pages=require_paged_acquisition,
        requested_full=full, change_hints=change_hints,
    ) if pending_raw is not None else None
    if pending is not None:
        manifest, pending_full = pending
        data = json.loads(pending_raw or "")
        completed_hints = None
        if data["version"] == 6:
            completed_hints = ChangeHints.from_dict(data["hints"])
            prior = state.sources.get(source)
            if data["base_commit"] != (prior.last_commit if prior is not None else None):
                raise ValueError("Completed hinted acquisition belongs to a different checkpoint")
        totals = publish(manifest, full=pending_full, clear=True, expected_payload=pending_raw)
        if not full and completed_hints is not None and completed_hints == change_hints:
            return totals

    state.require_acquisition_scope(source, scope, full=full)
    state.require_family_cursor(source, family or source, full=full)
    manifest = acquire()
    acquired_raw = journal.read_payload(source, scope)
    acquired = journal.decode_pending(
        acquired_raw, source, required_pages=require_paged_acquisition,
        requested_full=full, change_hints=change_hints,
    ) if acquired_raw is not None else None
    if require_paged_acquisition and acquired is None:
        raise PendingCrawlConflict("Required acquisition did not finish its pending pages")
    if acquired is not None and acquired != (manifest, full):
        raise PendingCrawlConflict("Acquired manifest does not match pending publication")
    if acquired is None and (not manifest.is_empty or manifest.poll_refresh is not None):
        journal.save(manifest, scope=scope, full=full)
    current = publish(
        manifest, full=full,
        clear=acquired is not None or not manifest.is_empty or manifest.poll_refresh is not None,
        expected_payload=acquired_raw,
    )
    return totals[0] + current[0], totals[1] + current[1], totals[2] + current[2]
