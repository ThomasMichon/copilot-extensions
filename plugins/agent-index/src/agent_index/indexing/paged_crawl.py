"""Resume bounded connector acquisition inside the existing pending-crawl owner."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from agent_index.indexing.manifest import CrawlManifest, CrawlStats
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.sources.base import SourceConnector
from agent_index.sources.delta import AcquisitionPage, SourceDelta


class DiscoverPage(Protocol):
    def __call__(
        self, last_commit: str | None, cursor: str | None,
        *, cancel_check: Callable[[], None] | None = None,
    ) -> AcquisitionPage: ...


@dataclass
class AcquisitionState:
    manifest: CrawlManifest
    base_commit: str | None
    cursor: str
    generation: str


def discard_acquisition(
    source: str, *, paths: PathIndex, connector: SourceConnector,
    source_scope: str, expected_payload: str,
) -> None:
    """Caller-authorized repair; never a response automatically inferred from an HTTP error.

    The unchanged committed cursor remains the repair authority. CAS serialization
    with progress publication makes an in-flight acquisition fail before indexing;
    a completed v1 manifest is never eligible for this operation.
    """
    if getattr(connector, "recovery_identity", None) is None:
        raise ValueError("Acquisition discard requires the actual connector recovery identity")
    PendingCrawls(paths).discard_acquisition(
        source, scope=recovery_scope(source_scope, connector),
        expected_payload=expected_payload, family=connector.source_name,
    )


def decode_acquisition(payload: str, source: str) -> AcquisitionState:
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {
        "version", "full", "manifest", "base_commit", "cursor", "generation",
    } or type(data["version"]) is not int or data["version"] != 2 or data["full"] is not False:
        raise ValueError("Invalid pending acquisition envelope")
    if data["base_commit"] is not None and not isinstance(data["base_commit"], str):
        raise ValueError("Invalid pending acquisition base checkpoint")
    generation = data["generation"]
    if not isinstance(generation, str) or uuid.UUID(generation).hex != generation:
        raise ValueError("Invalid pending acquisition generation")
    manifest = PendingCrawls.decode_manifest(data["manifest"], source)
    AcquisitionPage(SourceDelta(commit=manifest.commit), data["cursor"]).validate(
        manifest.accounting_family or manifest.stored_family or source,
    )
    if data["cursor"] is None:
        raise ValueError("Pending acquisition requires a continuation cursor")
    return AcquisitionState(manifest, data["base_commit"], data["cursor"], generation)


def acquire_pages(
    source: str, *, family: str, stored_family: str | None, scope: str,
    last_commit: str | None, journal: PendingCrawls, discover: DiscoverPage,
    cancel_check: Callable[[], None] | None = None, page_budget: int = 128,
) -> CrawlManifest:
    """Persist each complete unit before requesting the next; publish no source state."""
    if type(page_budget) is not int or page_budget <= 0:
        raise ValueError("Acquisition page budget must be positive")
    raw = journal.read_payload(source, scope)
    progress = decode_acquisition(raw, source) if raw is not None else None
    if progress is not None and (
        progress.base_commit != last_commit
        or progress.manifest.accounting_family != family
        or progress.manifest.stored_family != stored_family
    ):
        raise ValueError("Pending acquisition belongs to a different checkpoint or source binding")
    manifest = progress.manifest if progress else CrawlManifest(
        source=source, stored_family=stored_family, accounting_family=family,
    )
    cursor = progress.cursor if progress else None
    generation = progress.generation if progress else uuid.uuid4().hex
    for _ in range(page_budget):
        if cancel_check:
            cancel_check()
        page = discover(last_commit, cursor, cancel_check=cancel_check)
        if not isinstance(page, AcquisitionPage):
            raise ValueError("Connector returned an incompatible acquisition page")
        page.validate(family)
        if cancel_check:
            cancel_check()
        if raw is not None and page.delta.commit != manifest.commit:
            raise ValueError("Connector acquisition changed its pinned target checkpoint")
        if page.cursor is not None and page.cursor == cursor:
            raise ValueError("Connector acquisition cursor made no progress")
        upserts = manifest.upserts + page.delta.upserts
        deletions = manifest.deletions + page.delta.deletions
        combined = SourceDelta(upserts=upserts, deletions=deletions, commit=page.delta.commit)
        combined.validate(family)
        manifest = CrawlManifest(
            source=source, upserts=upserts, deletions=deletions, commit=combined.commit,
            stored_family=stored_family, accounting_family=family,
            stats=CrawlStats(
                files_scanned=len(upserts) + len(deletions), files_changed=len(upserts),
                files_deleted=len(deletions),
            ),
        )
        envelope: dict[str, object] = {
            "version": 1 if page.cursor is None else 2, "full": False,
            "manifest": asdict(manifest),
        }
        if page.cursor is not None:
            envelope.update(base_commit=last_commit, cursor=page.cursor, generation=generation)
        payload = json.dumps(envelope)
        journal.compare_and_save(source, scope=scope, payload=payload, expected=raw, family=family)
        raw, cursor = payload, page.cursor
        if cursor is None:
            return manifest
    raise RuntimeError(
        f"Acquisition page budget ({page_budget}) exhausted; retry resumes the pending acquisition",
    )
