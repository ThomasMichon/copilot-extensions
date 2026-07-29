"""Crawl manifest — structured output from source crawling.

Separates the discovery phase (cheap, I/O-bound) from the indexing phase
(expensive, GPU-bound).  Every reindex — full or incremental — produces a
``CrawlManifest`` that drives the embed/store/reconcile pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_index.sources.base import FileEntry


@dataclass
class DeletedFile:
    """A file that was removed from a source and should be de-indexed.

    Uses the exact ``(source, path)`` identity stored in the index —
    no prefix matching, no ambiguity.
    """

    source: str
    path: str


@dataclass
class CrawlStats:
    """Summary counts from a crawl pass."""

    files_scanned: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    files_unchanged: int = 0


@dataclass
class CrawlManifest:
    """The complete output of a source crawl.

    Produced by the crawl phase and consumed by the indexing engine.
    ``upserts`` are files to (re-)index.  ``deletions`` are files that
    no longer exist in the source and should be removed from the index.
    ``commit`` is the target revision the crawl was performed against,
    stored in ``IndexState`` after successful processing.
    """

    source: str
    upserts: list[FileEntry] = field(default_factory=list)
    deletions: list[DeletedFile] = field(default_factory=list)
    commit: str | None = None
    stats: CrawlStats = field(default_factory=CrawlStats)

    @property
    def is_empty(self) -> bool:
        """True if there's nothing to do."""
        return not self.upserts and not self.deletions
