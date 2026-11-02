"""Budgeted full reconciliation under the existing pending-crawl journal."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agent_index.indexing.manifest import CrawlManifest, CrawlStats, PollRefresh
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.sources.delta import DeletedFile, SnapshotPage, SourceDelta


class DiscoverSnapshotPage(Protocol):
    def __call__(
        self, snapshot: str | None, cursor: str | None,
        *, cancel_check: Callable[[], None] | None = None,
    ) -> SnapshotPage: ...


@dataclass
class SnapshotState:
    manifest: CrawlManifest
    base_commit: str | None
    generation: str
    snapshot: str
    cursor: str | None
    after: tuple[str, str] | None = None


def decode_snapshot(payload: str, source: str) -> SnapshotState:
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {
        "version", "full", "manifest", "base_commit", "generation", "snapshot", "cursor", "after",
    } or type(data["version"]) is not int or data["version"] != 4 or data["full"] is not True:
        raise ValueError("Invalid pending full-snapshot envelope")
    if data["base_commit"] is not None and not isinstance(data["base_commit"], str):
        raise ValueError("Invalid full-snapshot base checkpoint")
    generation = data["generation"]
    if not isinstance(generation, str) or uuid.UUID(generation).hex != generation:
        raise ValueError("Invalid full-snapshot generation")
    manifest = PendingCrawls.decode_manifest(data["manifest"], source)
    if manifest.poll_refresh is not None:
        raise ValueError("Incomplete full snapshot cannot acknowledge polling refresh")
    family = manifest.accounting_family or manifest.stored_family or source
    SnapshotPage(
        data["snapshot"], commit=manifest.commit, cursor=data["cursor"],
    ).validate(family)
    after = data["after"]
    if after is not None and (
        not isinstance(after, list) or len(after) != 2
        or any(not isinstance(value, str) or not value for value in after)
        or not (after[0] == family or after[0].startswith(family + ":"))
    ):
        raise ValueError("Invalid full-snapshot comparison cursor")
    if data["cursor"] is not None and (after is not None or manifest.deletions):
        raise ValueError("Incomplete full snapshot cannot infer deletions")
    return SnapshotState(
        manifest, data["base_commit"], generation, data["snapshot"], data["cursor"],
        (after[0], after[1]) if after is not None else None,
    )


def acquire_snapshot(
    source: str, *, family: str, stored_family: str | None, scope: str,
    last_commit: str | None, journal: PendingCrawls, discover: DiscoverSnapshotPage,
    refresh: PollRefresh | None = None, cancel_check: Callable[[], None] | None = None,
    unit_budget: int = 128,
) -> CrawlManifest:
    """Acquire immutable coverage, then compare bounded stored-identity pages.

    The budget counts provider pages and 256-identity comparison units. The
    accumulated manifest retains the existing journal's whole-payload memory and
    serialization costs; this is not a byte/time or whole-store memory bound.
    """
    if type(unit_budget) is not int or unit_budget <= 0:
        raise ValueError("Full reconciliation unit budget must be positive")
    raw = journal.read_payload(source, scope)
    progress = decode_snapshot(raw, source) if raw is not None else None
    if progress is not None and (
        progress.base_commit != last_commit
        or progress.manifest.accounting_family != family
        or progress.manifest.stored_family != stored_family
    ):
        raise ValueError("Pending full snapshot belongs to a different checkpoint or source binding")
    manifest = progress.manifest if progress else CrawlManifest(
        source=source, stored_family=stored_family, accounting_family=family,
    )
    generation = progress.generation if progress else uuid.uuid4().hex
    snapshot = progress.snapshot if progress else None
    cursor = progress.cursor if progress else None
    after = progress.after if progress else None
    identities = {(item.source, item.path) for item in manifest.upserts}
    for _ in range(unit_budget):
        if cancel_check:
            cancel_check()
        complete = False
        if progress is None or cursor is not None:
            page = discover(snapshot, cursor, cancel_check=cancel_check)
            if not isinstance(page, SnapshotPage):
                raise ValueError("Connector returned an incompatible full-snapshot page")
            page.validate(family)
            if snapshot is not None and (
                page.snapshot != snapshot or page.commit != manifest.commit
            ):
                raise ValueError("Connector changed its pinned full snapshot")
            if page.cursor is not None and page.cursor == cursor:
                raise ValueError("Full-snapshot cursor made no progress")
            combined = SourceDelta(upserts=manifest.upserts + page.upserts, commit=page.commit)
            combined.validate(family)
            manifest.upserts, manifest.commit = combined.upserts, combined.commit
            identities.update((item.source, item.path) for item in page.upserts)
            snapshot, cursor = page.snapshot, page.cursor
        else:
            stored = journal._paths.get_family_page(family, after=after)
            manifest.deletions.extend(
                DeletedFile(*identity) for identity in stored if identity not in identities
            )
            after = stored[-1] if stored else after
            complete = len(stored) < 256
        if cancel_check:
            cancel_check()
        manifest.stats = CrawlStats(
            files_scanned=len(manifest.upserts), files_changed=len(manifest.upserts),
            files_deleted=len(manifest.deletions),
        )
        if complete:
            covered = identities | {(item.source, item.path) for item in manifest.deletions}
            if refresh is not None:
                for namespace, candidates in (
                    (family, refresh.paths), (family + ":commits", refresh.history),
                ):
                    manifest.deletions.extend(
                        DeletedFile(namespace, path) for path in candidates
                        if (namespace, path) not in covered
                    )
                manifest.poll_refresh = refresh
                manifest.stats.files_deleted = len(manifest.deletions)
            journal._validate(manifest)
            envelope: dict[str, object] = {
                "version": 3 if refresh is not None else 1, "full": True,
                "manifest": journal.manifest_data(manifest),
            }
        else:
            envelope = {
                "version": 4, "full": True, "manifest": journal.manifest_data(manifest),
                "base_commit": last_commit, "generation": generation, "snapshot": snapshot,
                "cursor": cursor, "after": after,
            }
        payload = json.dumps(envelope)
        journal.compare_and_save(source, scope=scope, payload=payload, expected=raw, family=family)
        if complete:
            return manifest
        raw = payload
        progress = decode_snapshot(raw, source)
    raise RuntimeError(
        f"Full reconciliation unit budget ({unit_budget}) exhausted; retry resumes the pending snapshot",
    )
