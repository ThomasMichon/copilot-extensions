"""Source garbage collection -- purge stale source generations (#879).

Over the life of the index, the crawler's source-naming scheme has changed
several times (the old local ``monorepo`` crawler; pre-Phase-7
``forge:owner/repo``; the bare generic ``forge:code`` / ``forge:issues`` /
``forge:pulls`` / ``forge:commits``; the ``forge:owner/repo:issues``
variant). Incremental reconcile only ever prunes *within* a source name the
crawler still emits, so when a naming scheme is abandoned its chunks are
orphaned forever -- they inflate the index and compete in ranking.

This module deletes every chunk whose ``source`` is not part of the current
("live") naming scheme, by exact source value, then leaves compaction to the
caller. It is safe to run on every full reindex: it keys off the *naming
pattern*, not off whether a source produced rows this run, so a live
per-repo source that happens to be empty right now is never purged.

Live source values (current scheme):
  - ``forge:{code|issues|pulls|commits}:{owner}/{repo}``
  - ``service-feed:clips``
  - ``analysis-feed:videos``
  - any exact name in ``$AGENT_INDEX_GC_KEEP_SOURCES`` (comma-separated) -- an
    escape hatch for reviving remote-ingest sources (e.g. a push daemon
    pushing ``host:worktree``) without code changes.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.state import IndexState
    from agent_index.store.multi_model_store import MultiModelStore

log = logging.getLogger(__name__)


class GCSummary(TypedDict):
    """Result of a source garbage-collection pass."""

    purged: dict[str, int]   # stale source -> chunks removed
    kept: list[str]          # live sources, untouched
    chunks_deleted: int
    dry_run: bool

_FORGE_SUBTYPES = frozenset({"code", "issues", "pulls", "commits"})
_SERVICE_SOURCES = frozenset({"service-feed:clips", "analysis-feed:videos"})

# IndexState is keyed by *crawl* source name (e.g. "forge:code"), a different
# namespace from stored chunk sources (per-repo, e.g. "forge:code:owner/repo").
# A bare "forge:code" is therefore simultaneously a stale STORED chunk source
# (to purge) and the LIVE crawl marker in IndexState (to keep). GC must purge
# the chunks but never delete the live crawl marker, or the next incremental
# crawl loses its commit cursor and re-crawls everything.
_LIVE_CRAWL_STATE_KEYS = frozenset(
    {f"forge:{st}" for st in _FORGE_SUBTYPES} | set(_SERVICE_SOURCES) | {"forge"}
)


def _extra_keep_sources() -> frozenset[str]:
    """Exact source names to always keep, from ``$AGENT_INDEX_GC_KEEP_SOURCES``."""
    raw = os.environ.get("AGENT_INDEX_GC_KEEP_SOURCES", "")
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def is_live_source(source: str, *, extra_keep: frozenset[str] | None = None) -> bool:
    """True if *source* matches the current ("live") naming scheme.

    Anything that returns False is a stale generation eligible for GC.
    """
    if not source:
        return False
    if source in _SERVICE_SOURCES:
        return True
    if extra_keep and source in extra_keep:
        return True
    if source.startswith("forge:"):
        # Live Forge sources are subtype-first AND repo-qualified:
        #   forge:<subtype>:<owner>/<repo>
        parts = source.split(":", 2)
        return (
            len(parts) == 3
            and parts[1] in _FORGE_SUBTYPES
            and "/" in parts[2]
        )
    return False


def gc_stale_sources(
    multi_store: MultiModelStore,
    path_index: PathIndex,
    state: IndexState,
    *,
    dry_run: bool = False,
) -> GCSummary:
    """Delete all chunks whose ``source`` is not in the live registry.

    Removes the stale rows from the content + vector tables, the SQLite path
    index, and IndexState. Does NOT compact -- callers should compact after.
    Live IndexState crawl markers are preserved.
    """
    extra_keep = _extra_keep_sources()
    counts = multi_store.source_counts()

    purged: dict[str, int] = {}
    kept: list[str] = []
    for source, count in counts.items():
        if is_live_source(source, extra_keep=extra_keep):
            kept.append(source)
        else:
            purged[source] = count

    total_deleted = 0
    if not dry_run:
        for source in purged:
            try:
                removed = multi_store.delete_by_source_exact(source)
                path_index.delete_source(source)
                # Purge the stale chunks, but never delete a LIVE crawl marker
                # that merely shares this name in the IndexState namespace
                # (e.g. stale stored "forge:code" chunks vs. the live
                # "forge:code" crawl cursor).
                if source in state.sources and source not in _LIVE_CRAWL_STATE_KEYS:
                    del state.sources[source]
                total_deleted += removed
                log.info(
                    "GC: purged stale source %s (%d chunks)", source, removed,
                )
            except Exception:
                log.warning(
                    "GC: failed to purge stale source %s", source, exc_info=True,
                )
        # Keep IndexState's total in sync with what remains.
        state.total_chunks = sum(s.chunk_count for s in state.sources.values())
    else:
        total_deleted = sum(purged.values())

    return {
        "purged": purged,
        "kept": sorted(kept),
        "chunks_deleted": total_deleted,
        "dry_run": dry_run,
    }
