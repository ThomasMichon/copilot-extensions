"""Index engine - orchestrates scanning, chunking, embedding, and storage.

Four-phase pipeline:
1. **Crawl** - discover files, diff against stored state → CrawlManifest
2. **Embed/Store** - chunk files, embed via GPU, upsert to LanceDB
3. **Reconcile** - delete removed files and stale modified-file chunks
4. **Commit** - update IndexState with new commit marker

Deletions are always deferred to Phase 3 (after successful upserts) for
crash safety.  Every reindex - full or incremental - handles deletions.

Supports multi-model indexing: when multiple model profiles are configured,
each batch is embedded by all available engines and stored in per-model
vector tables alongside the shared content store.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from typing import TYPE_CHECKING

# UTF-8 stdio so human-facing status glyphs render safely (Rule B, #769).
_utf8_sys = __import__("sys")
_utf8_sys.stdout.reconfigure(encoding="utf-8")
_utf8_sys.stderr.reconfigure(encoding="utf-8")

if TYPE_CHECKING:
    from agent_index.chunking.base import Chunk
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig, ModelProfile
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.indexing.state import IndexState
    from agent_index.sources.base import FileEntry
    from agent_index.store.multi_model_store import MultiModelStore

logger = logging.getLogger(__name__)

STREAM_BATCH_SIZE = 500  # fallback default; the live value is config.stream_batch_size (#115)


def configured_sources() -> list[str]:
    """Return the configured default source list for indexing."""
    raw = os.environ.get("AGENT_INDEX_SOURCES")
    if raw:
        sources = [source.strip() for source in raw.split(",") if source.strip()]
        if sources:
            return sources
    return ["git"]


def run_reindex(
    *,
    full: bool = False,
    source: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, float]:
    """Run the four-phase indexing pipeline.

    Args:
        full: If True, do a full crawl (discover everything, not just changes).
        source: If set, only reindex this specific source.
        progress_cb: Optional callback for progress reporting and cancellation.

    Returns:
        Dict with keys: chunks_total, chunks_added, chunks_deleted, files_crawled.
    """
    from agent_index.engine.client import EngineClient
    from agent_index.engine.lifecycle import ensure_engine, stop_engine
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import IndexingCancelled
    from agent_index.indexing.state import IndexState
    from agent_index.store.multi_model_store import MultiModelStore

    config = IndexConfig()
    config.ensure_dirs()

    print(f"Data directory: {config.data_dir}")
    print(f"Model: {config.model_name}")
    print(f"Mode: {'full' if full else 'incremental'}")
    if source:
        print(f"Source: {source}")
    print()

    state = IndexState.load(config.state_file)
    path_index = PathIndex(config.data_dir / "path_index.db")

    # Multi-model store + clients
    multi_store = MultiModelStore(
        config.lance_dir, content_table=config.content_table
    )
    multi_clients: dict[str, EngineClient] = {}
    for profile in config.model_profiles.values():
        multi_store.register_model(profile)
        multi_clients[profile.model_id] = EngineClient(
            base_url=profile.engine_url,
            query_prefix=profile.query_prefix,
            model_id=profile.model_id,
        )

    # Track which on-demand engines we start so we can stop them afterwards.
    # Initialized before the try so the finally can always see it, even if
    # ensuring engines (or the migration) fails partway.
    started_engines: list[ModelProfile] = []
    ensure_engines = os.environ.get("AGENT_INDEX_INDEX_ENSURE_ENGINES", "1") != "0"

    sources_to_index: list[str]
    if source is None:
        sources_to_index = configured_sources()
    else:
        sources_to_index = [source]

    total_chunks = 0
    total_deleted = 0
    total_files_crawled = 0
    start = time.monotonic()

    try:
        # Ensure embedding engines are running BEFORE the embed phase. The
        # code engine (8421) is on-demand and is left stopped by deploy, so
        # without this a reindex would silently skip code embedding -- chunks
        # get content-stored but never vectorised (#775). Fail loudly here
        # (before the expensive crawl) rather than stalling silently mid-run.
        # Inside the try so the finally always stops engines we started, even
        # if a later ensure_engine or the migration below raises.
        if ensure_engines:
            for profile in config.model_profiles.values():
                if ensure_engine(profile, multi_clients[profile.model_id]):
                    started_engines.append(profile)

        for src_name in sources_to_index:
            if progress_cb:
                progress_cb.check_cancelled()
                progress_cb.source_started(src_name)

            try:
                stored, deleted, crawled = _index_source(
                    src_name,
                    config=config,
                    state=state,
                    path_index=path_index,
                    multi_store=multi_store,
                    multi_clients=multi_clients,
                    full=full,
                    progress_cb=progress_cb,
                )
                total_chunks += stored
                total_deleted += deleted
                total_files_crawled += crawled
            except IndexingCancelled:
                raise
            except Exception:
                logger.exception("Failed to index source: %s", src_name)
                print(f"  ERROR: {src_name} failed - continuing with next source")

            if progress_cb:
                progress_cb.source_complete(src_name, total_chunks)

            # Save state after each source so progress isn't lost
            state.save(config.state_file)
    finally:
        for c in multi_clients.values():
            c.close()
        # Stop on-demand engines we started, freeing VRAM (honors the
        # on-demand design). Best-effort; never let cleanup mask a real error.
        for profile in started_engines:
            try:
                stop_engine(profile)
            except Exception:
                logger.warning(
                    "Failed to stop on-demand engine '%s'",
                    profile.model_id, exc_info=True,
                )

    if full:
        state.last_full_reindex = time.time()

        # Purge stale source generations (abandoned naming schemes) BEFORE
        # compaction so the reclaimed space reflects the de-duplicated
        # baseline (#879). Keyed off the naming pattern, so a live per-repo
        # source that produced no rows this run is never purged. Opt-out via
        # AGENT_INDEX_REINDEX_GC=0 -- GC purges any source not matching the current
        # scheme, so if remote /api/ingest producers (e.g. a revived
        # agent-index-push-daemon) write machine-scoped sources, either disable this
        # or add their names to AGENT_INDEX_GC_KEEP_SOURCES.
        if os.environ.get("AGENT_INDEX_REINDEX_GC", "1") != "0":
            print("\nGarbage-collecting stale source generations...")
            try:
                from agent_index.indexing.gc import gc_stale_sources

                gc_summary = gc_stale_sources(multi_store, path_index, state)
                purged = gc_summary["purged"]
                if purged:
                    for src, cnt in sorted(purged.items()):
                        print(f"  purged {src}: {cnt} chunks")
                    print(f"  GC removed {gc_summary['chunks_deleted']} stale chunks")
                else:
                    print("  No stale sources found")
            except Exception:
                logger.warning("Source GC failed", exc_info=True)
                print("  WARNING: source GC failed (see logs)")

        # Compact LanceDB tables after full reindex to reclaim space
        print("\nCompacting LanceDB tables...")
        try:
            compact_stats = multi_store.compact()
            for table_name, table_stats in compact_stats.items():
                fb = table_stats.get("fragments_before", 0)
                fa = table_stats.get("fragments_after", 0)
                if fb > 0:
                    print(f"  {table_name}: {fb} -> {fa} fragments")
        except Exception:
            logger.warning("Post-reindex compaction failed", exc_info=True)
            print("  WARNING: compaction failed (see logs)")

    state.save(config.state_file)

    elapsed = time.monotonic() - start
    print(f"\nIndexing complete: {total_chunks} chunks in {elapsed:.1f}s")

    result: dict[str, float] = {
        "chunks_total": total_chunks,
        "chunks_deleted": total_deleted,
        "files_crawled": total_files_crawled,
        "duration_seconds": round(elapsed, 1),
    }

    # Refresh the similarity-cluster artifact from the just-updated vectors.
    # Reuses stored embeddings (no re-embedding), so it runs post-index in the
    # same pipeline (covering both the service task runner and the CLI). Must
    # never fail a reindex, so it is best-effort and guarded by config.
    cluster_stats = _refresh_clusters(multi_store, config)
    if cluster_stats is not None:
        result["clusters"] = cluster_stats["clusters"]

    return result


def _refresh_clusters(
    multi_store: MultiModelStore, config: IndexConfig
) -> dict[str, int | float] | None:
    """Recompute the similarity-cluster artifact after indexing (best-effort).

    Sweeps stored centroids and replaces ``clusters.db``; never re-embeds.
    Returns the pass stats dict, or ``None`` when clustering is disabled or
    fails (a clustering failure must never fail the reindex it follows).
    """
    if not config.cluster_enabled:
        return None
    try:
        from agent_index.indexing.cluster_pass import run_clustering_pass
        from agent_index.store.cluster_store import ClusterStore

        cluster_store = ClusterStore(config.clusters_db)
        print("\nRefreshing similarity clusters...")
        stats = run_clustering_pass(multi_store, cluster_store, config)
        print(
            f"  {stats['clusters']} clusters across {stats['slices']} slices "
            f"({stats['items']} items, {stats['elapsed_ms']}ms)"
        )
        return stats
    except Exception:
        logger.warning("Clustering pass failed", exc_info=True)
        print("  WARNING: clustering pass failed (see logs)")
        return None


def _index_source(
    source_name: str,
    *,
    config: IndexConfig,
    state: IndexState,
    path_index: PathIndex,
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    full: bool,
    progress_cb: ProgressCallback | None = None,
) -> tuple[int, int, int]:
    """Index a single data source using the four-phase pipeline.

    Phase 1: Crawl -> CrawlManifest (cheap, I/O-bound)
    Phase 2: Embed/Store upserts (expensive, GPU-bound)
    Phase 3: Reconcile deletions + stale chunks (cheap)
    Phase 4: Commit state

    Returns:
        Tuple of (chunks_stored, chunks_deleted, files_crawled).
    """
    print(f"Indexing source: {source_name}")
    run_start = time.time()

    # ── Phase 1: Crawl ──────────────────────────────────────
    if progress_cb:
        progress_cb.phase("crawling", msg=f"Crawling {source_name}")

    manifest = _crawl_source(
        source_name,
        config=config,
        state=state,
        path_index=path_index,
        full=full,
        progress_cb=progress_cb,
    )

    if manifest.is_empty:
        print(f"  No changes detected for {source_name}")
        # Still update commit marker so we don't re-crawl next time
        if manifest.commit:
            src_state = state.sources.get(source_name)
            state.mark_source_indexed(
                source_name,
                manifest.commit,
                src_state.chunk_count if src_state else 0,
            )
        return (0, 0, 0)

    stats = manifest.stats
    print(f"  Crawl: {stats.files_changed} to index, {stats.files_deleted} to delete")

    if progress_cb:
        progress_cb.file_discovered(stats.files_changed, total=stats.files_changed)

    # ── Phase 2: Embed/Store upserts ────────────────────────
    total_stored = 0
    upserted_files: set[tuple[str, str]] = set()  # (source, file_path) for stale cleanup

    if manifest.upserts:
        if progress_cb:
            progress_cb.phase("chunking", msg=f"Chunking {len(manifest.upserts)} files")

        total_stored = _embed_and_store_files(
            manifest.upserts,
            multi_store=multi_store,
            multi_clients=multi_clients,
            model_profiles=config.model_profiles,
            path_index=path_index,
            stream_batch_size=config.stream_batch_size,
            progress_cb=progress_cb,
        )

        # Track which files were upserted for stale chunk cleanup
        for entry in manifest.upserts:
            upserted_files.add((entry.source, entry.path))

    # ── Phase 3: Reconcile ──────────────────────────────────
    if progress_cb:
        progress_cb.phase("reconciling", msg="Cleaning up deletions and stale chunks")

    total_deleted = 0

    # 3a. Explicit deletions (files removed from source)
    for deleted in manifest.deletions:
        if progress_cb:
            progress_cb.check_cancelled()
        try:
            # Multi-model store (content + vectors)
            multi_store.delete_by_file(deleted.source, deleted.path)
            # Path index
            path_index.remove(deleted.source, deleted.path)
            total_deleted += 1
        except Exception:
            logger.warning(
                "Failed to delete %s:%s", deleted.source, deleted.path,
                exc_info=True,
            )

    # 3b. Stale chunk cleanup for modified files
    # Modified files produce new chunk_ids (content hash changed), so old
    # chunks with the same (source, file_path) but indexed before this run
    # need to be removed.
    stale_cleaned = 0
    for src, path in upserted_files:
        try:
            removed = multi_store.delete_stale_by_file(src, path, before=run_start)
            stale_cleaned += removed
        except Exception:
            logger.debug("Stale cleanup failed for %s:%s", src, path, exc_info=True)

    if total_deleted or stale_cleaned:
        print(f"  Reconciled: {total_deleted} files deleted, {stale_cleaned} stale chunks cleaned")

    # ── Phase 4: Commit state ───────────────────────────────
    state.mark_source_indexed(source_name, manifest.commit, total_stored)

    total_removed = total_deleted + stale_cleaned
    print(f"  Total: {total_stored} chunks stored, {total_removed} removed")
    return (total_stored, total_removed, stats.files_changed)


def _crawl_source(
    source_name: str,
    *,
    config: IndexConfig,
    state: IndexState,
    path_index: PathIndex,
    full: bool,
    progress_cb: ProgressCallback | None = None,
) -> CrawlManifest:
    """Phase 1: Crawl a source and produce a CrawlManifest.

    For full crawls: discover all files, diff paths against stored for deletions.
    For incremental: list current paths (cheap), diff for deletions,
    then discover changed files for upserts.
    """
    from agent_index.indexing.manifest import CrawlManifest, CrawlStats, DeletedFile
    from agent_index.sources import get_connector

    connector = get_connector(source_name)
    commit = connector.current_commit()

    # Build a cancel checker from the progress callback
    cancel_check = progress_cb.check_cancelled if progress_cb else None

    # Get what's currently in our index for deletion detection
    stored_by_source = path_index.get_paths_by_prefix(source_name)

    if full:
        # Full crawl: discover everything
        upserts = connector.discover(cancel_check=cancel_check)

        if progress_cb:
            progress_cb.check_cancelled()

        # Build current path set from discovered files
        current_by_source: dict[str, set[str]] = {}
        for entry in upserts:
            current_by_source.setdefault(entry.source, set()).add(entry.path)
    else:
        # Incremental: get current path set cheaply for deletion detection
        current_by_source = connector.list_paths(cancel_check=cancel_check)

        if progress_cb:
            progress_cb.check_cancelled()

        # Get changed files for upserts
        src_state = state.sources.get(source_name)
        last_commit = src_state.last_commit if src_state else None
        upserts = connector.discover_changed(last_commit, cancel_check=cancel_check)

        if progress_cb:
            progress_cb.check_cancelled()

    # Compute deletions: stored paths not in current paths
    deletions: list[DeletedFile] = []
    all_stored_sources = set(stored_by_source.keys())

    for src in all_stored_sources:
        stored_paths = stored_by_source[src]
        current_paths = current_by_source.get(src, set())
        for path in stored_paths - current_paths:
            deletions.append(DeletedFile(source=src, path=path))

    # Note: sources that no longer exist at all are already fully handled by
    # the loop above -- their current path set is empty, so every stored path
    # falls into (stored - current) and is queued for deletion.

    stats = CrawlStats(
        files_scanned=sum(len(p) for p in current_by_source.values()),
        files_changed=len(upserts),
        files_deleted=len(deletions),
        files_unchanged=sum(len(p) for p in current_by_source.values()) - len(upserts),
    )

    return CrawlManifest(
        source=source_name,
        upserts=upserts,
        deletions=deletions,
        commit=commit,
        stats=stats,
    )


def _embed_and_store_files(
    files: list[FileEntry],
    *,
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    model_profiles: dict[str, ModelProfile] | None = None,
    path_index: PathIndex,
    stream_batch_size: int = STREAM_BATCH_SIZE,
    progress_cb: ProgressCallback | None = None,
) -> int:
    """Phase 2: Chunk files, embed via GPU, upsert to stores.

    Returns total chunks stored.
    """
    from agent_index.chunking import get_chunker

    batch: list[Chunk] = []
    total_chunks = 0
    total_stored = 0
    # Track per-file chunk counts for path index
    file_chunk_counts: dict[tuple[str, str], int] = {}  # (source, path) → count

    for entry in files:
        if progress_cb:
            progress_cb.check_cancelled()

        chunker, _lang = get_chunker(entry.path)
        try:
            chunks = chunker.chunk(entry.content, entry.path, source=entry.source)
        except Exception:
            logger.warning("Failed to chunk %s, skipping", entry.path)
            continue

        if getattr(entry, "metadata", None):
            chunks = [replace(c, metadata=entry.metadata) for c in chunks]

        batch.extend(chunks)
        total_chunks += len(chunks)
        file_chunk_counts[(entry.source, entry.path)] = len(chunks)

        if len(batch) >= stream_batch_size:
            if progress_cb:
                progress_cb.check_cancelled()
            total_stored += _embed_and_store_batch(
                batch, multi_store, multi_clients,
                model_profiles,
                stream_batch_size=stream_batch_size,
            )
            if progress_cb:
                progress_cb.batch_complete(total_stored, total_chunks)
            batch = []

    # Final partial batch
    if batch:
        if progress_cb:
            progress_cb.check_cancelled()
        total_stored += _embed_and_store_batch(
            batch, multi_store, multi_clients,
            model_profiles,
            stream_batch_size=stream_batch_size,
        )
        if progress_cb:
            progress_cb.batch_complete(total_stored, total_chunks)

    # Update path index with what we just stored
    path_entries: list[tuple[str, str, str | None, int]] = [
        (src, path, None, count)
        for (src, path), count in file_chunk_counts.items()
    ]
    path_index.mark_indexed_batch(path_entries)

    print(f"  Embedded: {total_chunks} chunks generated, {total_stored} stored")
    return total_stored


def _embed_and_store_batch(
    chunks: list[Chunk],
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    model_profiles: dict[str, ModelProfile] | None = None,
    *,
    stream_batch_size: int = STREAM_BATCH_SIZE,
) -> int:
    """Embed a batch of chunks via the engine(s) and upsert into stores.

    When *model_profiles* is provided, chunks are routed to each model
    based on ``ModelProfile.content_types``.  Chunks whose type is not
    claimed by any profile are sent to all models as a fallback.
    """
    from agent_index.engine.client import EngineUnavailableError

    # Pre-compute the union of all configured content types so we can
    # detect "unclaimed" chunk types and route them to every model.
    all_claimed_types: frozenset[str] = frozenset()
    if model_profiles:
        all_claimed_types = frozenset().union(
            *(p.content_types for p in model_profiles.values() if p.content_types)
        )

    total = 0
    for i in range(0, len(chunks), stream_batch_size):
        sub = chunks[i : i + stream_batch_size]

        # Store content once (canonical chunk count)
        content_count = multi_store.upsert_content(sub)
        total += content_count

        # Embed with each model's engine, routing by content type
        for model_id, client in multi_clients.items():
            profile = model_profiles.get(model_id) if model_profiles else None
            if profile and profile.content_types:
                # Route: chunks matching this profile's types + unclaimed types
                model_chunks = [
                    c for c in sub
                    if c.chunk_type in profile.content_types
                    or c.chunk_type not in all_claimed_types
                ]
            else:
                model_chunks = sub  # no filter = embed all

            if not model_chunks:
                continue

            model_texts = [c.content for c in model_chunks]
            try:
                model_vectors = client.embed_texts(model_texts)
                multi_store.upsert_vectors(model_id, model_chunks, model_vectors)
            except EngineUnavailableError:
                # Fail loud -- never silently skip vectorization (#775). The
                # client already retried once (re-activating a transiently-idle
                # on-demand engine), so reaching here means *genuine*
                # unavailability. Raising aborts this source so its commit state
                # is NOT advanced and the next reindex retries it, instead of
                # completing "successfully" with chunks content-stored but
                # unvectorized (and thus missing from search until a full reindex).
                logger.error(
                    "Engine for model '%s' unavailable mid-run; failing the "
                    "source so %d chunks are retried, not silently skipped",
                    model_id, len(model_chunks),
                )
                raise
            except Exception:
                logger.warning(
                    "Failed to embed batch with model '%s'", model_id,
                    exc_info=True,
                )

    return total
