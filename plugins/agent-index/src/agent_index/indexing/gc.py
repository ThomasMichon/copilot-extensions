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

Live source values (current scheme) -- a source is live iff a currently
**registered connector** owns its scheme (``agent_index.sources``):
  - ``git:{repo}`` and ``git:{repo}:commits`` (GitRepoConnector)
  - ``github:{owner}/{repo}`` and its ``:issues`` / ``:pulls`` (GitHubConnector)
  - ``ado:*`` / ``azure-devops:*`` (AzureDevOpsConnector)
  - the bare crawl-marker name of any connector (e.g. ``git``)
  - ``service-feed:clips``, ``analysis-feed:videos``
  - the legacy repo-qualified ``forge:{code|issues|pulls|commits}:{owner}/{repo}``
    (VEI-era generations kept live for back-compat)
  - any exact name in ``$AGENT_INDEX_GC_KEEP_SOURCES`` (comma-separated) -- an
    escape hatch for reviving remote-ingest sources (e.g. a push daemon
    pushing ``host:worktree``) without code changes.

Deriving the live set from the connector registry (rather than a hardcoded
scheme list) is the fix for #116: the extracted engine's GC previously knew
only VEI's ``forge:*`` and purged the generic ``git:*`` index on every full
reindex.
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from agent_index.store.source_layout import selected_layout

if TYPE_CHECKING:
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.state import IndexState
    from agent_index.store.multi_model_store import MultiModelStore

log = logging.getLogger(__name__)


def _retirement_writer(function):
    """Keep admission, row retirement and identity cleanup in one source operation."""
    @wraps(function)
    def call(multi_store, path_index, state, **kwargs):
        domain = getattr(multi_store, "_source_domain", None)
        if domain is None or kwargs.get("dry_run", False) or domain.writing:
            return function(multi_store, path_index, state, **kwargs)
        with domain.operation(multi_store.content_store.db):
            return function(multi_store, path_index, state, **kwargs)
    return call


@dataclass(frozen=True)
class SourceRetirementPolicy:
    """Caller-owned source inventory; collection still uses shared guards.

    A crawl selection need not describe push-ingested sources. A pure predicate
    can classify legacy naming generations without replacing the GC algorithm.
    """

    configured_snapshot: bool = True
    stale_generations: bool = True
    is_live_source: Callable[[str], bool] | None = None
    live_crawl_keys: frozenset[str] = frozenset()


class GCSummary(TypedDict):
    """Result of a source garbage-collection pass."""

    purged: dict[str, int]   # stale source -> chunks removed
    kept: list[str]          # live sources, untouched
    chunks_deleted: int
    dry_run: bool


class GCDecision(GCSummary, total=False):
    deferred: list[str]
    pending_crawls: list[str]

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

    A source is live iff a currently-registered connector owns its scheme -- the
    bare crawl-marker name (``git``) or any hierarchical source it emits
    (``git:<repo>``, ``git:<repo>:commits``, ``github:<owner>/<repo>:issues``,
    ``ado:<proj>:workitems``, ...). Deriving the live set from the connector
    registry keeps GC correct as connectors are added, rather than hardcoding
    each scheme -- the #116 gap, where GC knew only VEI's ``forge:*`` and so
    purged the generic ``git:*`` index on every full reindex.

    Anything that returns False is a stale generation eligible for GC.
    """
    if not source:
        return False
    if source in _SERVICE_SOURCES:
        return True
    if extra_keep and source in extra_keep:
        return True
    # Registered connector schemes (git, github, ado, ...) -- the generic engine.
    from agent_index.sources import registered_source_prefixes

    for prefix in registered_source_prefixes():
        if source == prefix or source.startswith(f"{prefix}:"):
            return True
    # Legacy Forge scheme (VEI-era generations): live Forge sources are
    # subtype-first AND repo-qualified (forge:<subtype>:<owner>/<repo>); the old
    # bare forge:<subtype> generation stays purgeable.
    if source.startswith("forge:"):
        parts = source.split(":", 2)
        return (
            len(parts) == 3
            and parts[1] in _FORGE_SUBTYPES
            and "/" in parts[2]
        )
    return False


def is_configured_source(
    source: str, configured_names: frozenset[str], *, extra_keep: frozenset[str] | None = None,
) -> bool:
    """True if *source* is still declared in the CURRENT corpus config.

    A stored source is configured iff it exactly matches a configured source
    spec's name, or is a hierarchical sub-source of one (e.g. the configured
    ``github:owner/repo`` covers stored ``github:owner/repo``,
    ``github:owner/repo:issues``, and ``github:owner/repo:pulls``). Anything
    else -- typically a source removed from ``corpus.sources`` sometime after
    it was indexed -- is NOT configured and is eligible for
    :func:`gc_unconfigured_sources`.
    """
    if not source:
        return False
    if extra_keep and source in extra_keep:
        return True
    if source in configured_names:
        return True
    return any(source.startswith(f"{name}:") for name in configured_names)


@_retirement_writer
def gc_configured_snapshot(
    multi_store: MultiModelStore, path_index: PathIndex, state: IndexState, *,
    configured_names: frozenset[str], source_aware: bool,
) -> GCDecision:
    """Retain canonical rows separately from active configured crawl identities."""
    from agent_index.indexing.pending_crawl import PendingCrawls

    journal = PendingCrawls(path_index)
    pending = journal.sources()
    if pending:
        log.warning("Source GC deferred: complete pending crawls with original configuration: %s",
                    pending)
        return {"purged": {}, "kept": [], "chunks_deleted": 0, "dry_run": False,
                "deferred": pending, "pending_crawls": pending}
    backfills = journal.backfill_sources()
    if backfills:
        log.warning("Source GC deferred: unfinished embedding backfills: %s", backfills)
        return {"purged": {}, "kept": [], "chunks_deleted": 0, "dry_run": False,
                "deferred": backfills}
    if not source_aware:
        return gc_unconfigured_sources(
            multi_store, path_index, state, configured_names=configured_names,
        )
    missing = sorted(name for name in configured_names if not state.stored_families.get(name))
    if missing:
        log.warning("Unconfigured-source GC deferred: canonical identities unresolved: %s", missing)
        return {"purged": {}, "kept": [], "chunks_deleted": 0,
                "dry_run": False, "deferred": missing}
    canonical = frozenset(state.stored_families[name] for name in configured_names)
    result = gc_unconfigured_sources(
        multi_store, path_index, state, configured_names=canonical, preserve_crawl_state=True,
    )
    extra_keep = _extra_keep_sources()
    for source in path_index.list_sources():
        if not is_configured_source(source, canonical, extra_keep=extra_keep):
            path_index.delete_source(source)
    for alias in (state.sources.keys() | state.stored_families.keys()) - configured_names:
        state.sources.pop(alias, None)
        state.stored_families.pop(alias, None)
    state.total_chunks = sum(source.chunk_count for source in state.sources.values())
    return result


@_retirement_writer
def gc_unconfigured_sources(
    multi_store: MultiModelStore,
    path_index: PathIndex,
    state: IndexState,
    *,
    configured_names: frozenset[str],
    dry_run: bool = False,
    preserve_crawl_state: bool = False,
) -> GCSummary:
    """Delete all chunks whose source is no longer in the CURRENT corpus config.

    Unlike :func:`gc_stale_sources` (which purges abandoned NAMING SCHEMES --
    a crawler-generation concern -- and only runs on a full reindex), this
    purges sources a user simply removed from ``corpus.sources`` (harness
    defaults, a knowledge-repo overlay, or a personal/machine-local addition).
    It is cheap (one ``source_counts()`` scan) and safe to run on EVERY
    reindex, incremental included, so a config change takes effect on the
    next routine reindex tick rather than only on an explicit full reindex or
    a service restart. Opt-out via ``AGENT_INDEX_REINDEX_GC=0`` (the same
    escape hatch as :func:`gc_stale_sources`).

    Removes the stale rows from the content + vector tables, the SQLite path
    index, and IndexState. Does NOT compact -- callers should compact after
    (a full reindex already does; an incremental reindex leaves compaction to
    its own periodic pass, consistent with existing incremental behavior).
    ``preserve_crawl_state`` separates selected stored-row retirement from crawl
    aliases; the snapshot owner prunes retired aliases only after this pass succeeds.
    """
    extra_keep = _extra_keep_sources()
    counts = multi_store.source_counts()

    purged: dict[str, int] = {}
    kept: list[str] = []
    for source, count in counts.items():
        if is_configured_source(source, configured_names, extra_keep=extra_keep):
            kept.append(source)
        else:
            purged[source] = count

    total_deleted = 0
    if not dry_run:
        from agent_index.indexing.pending_crawl import PendingCrawls

        PendingCrawls(path_index).assert_gc_available()
        for source in purged:
            try:
                removed = multi_store.delete_by_source_exact(source)
                path_index.delete_source(source)
                if (not preserve_crawl_state and not selected_layout()
                        and source in state.sources and source not in _LIVE_CRAWL_STATE_KEYS):
                    del state.sources[source]
                total_deleted += removed
                log.info(
                    "GC: purged unconfigured source %s (%d chunks)", source, removed,
                )
            except Exception:
                if selected_layout():
                    raise
                log.warning(
                    "GC: failed to purge unconfigured source %s", source, exc_info=True,
                )
        state.total_chunks = sum(s.chunk_count for s in state.sources.values())
    else:
        total_deleted = sum(purged.values())

    return {
        "purged": purged,
        "kept": sorted(kept),
        "chunks_deleted": total_deleted,
        "dry_run": dry_run,
    }


@_retirement_writer
def gc_stale_sources(
    multi_store: MultiModelStore,
    path_index: PathIndex,
    state: IndexState,
    *,
    dry_run: bool = False,
    policy: SourceRetirementPolicy | None = None,
) -> GCSummary:
    """Delete all chunks whose ``source`` is not in the live registry.

    Removes the stale rows from the content + vector tables, the SQLite path
    index, and IndexState. Does NOT compact -- callers should compact after.
    Live IndexState crawl markers are preserved.
    """
    policy = policy if policy is not None else SourceRetirementPolicy()
    if not policy.stale_generations:
        return {"purged": {}, "kept": [], "chunks_deleted": 0, "dry_run": dry_run}
    extra_keep = _extra_keep_sources()
    counts = multi_store.source_counts()

    purged: dict[str, int] = {}
    kept: list[str] = []
    for source, count in counts.items():
        live = source in extra_keep or (
            policy.is_live_source(source) if policy.is_live_source is not None
            else is_live_source(source)
        )
        if live:
            kept.append(source)
        else:
            purged[source] = count

    total_deleted = 0
    if not dry_run:
        from agent_index.indexing.pending_crawl import PendingCrawls

        PendingCrawls(path_index).assert_gc_available()
        for source in purged:
            try:
                removed = multi_store.delete_by_source_exact(source)
                path_index.delete_source(source)
                # Purge the stale chunks, but never delete a LIVE crawl marker
                # that merely shares this name in the IndexState namespace
                # (e.g. stale stored "forge:code" chunks vs. the live
                # "forge:code" crawl cursor).
                if (not selected_layout() and source in state.sources
                        and source not in _LIVE_CRAWL_STATE_KEYS
                        and source not in policy.live_crawl_keys):
                    del state.sources[source]
                total_deleted += removed
                log.info(
                    "GC: purged stale source %s (%d chunks)", source, removed,
                )
            except Exception:
                if selected_layout():
                    raise
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
