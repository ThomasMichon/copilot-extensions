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

import hashlib
import logging
import os
import time
from dataclasses import dataclass, replace
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
    """Return the configured default source **names** for indexing.

    Precedence: the ``AGENT_INDEX_SOURCES`` env override (legacy/testing), then
    the grafted ``corpus.sources`` swept from adopted local projects, else the
    single default ``["git"]``. See ``configured_source_specs`` for the richer
    per-source spec (type/repo/auth) that drives connector construction.
    """
    return [spec.name for spec in configured_source_specs()]


@dataclass(frozen=True)
class SourceSpec:
    """A resolved corpus source: its name, connector type, and how to reach it."""

    name: str
    type: str = "git"
    repo: str | None = None
    auth_account: str | None = None
    trust_domain: str | None = None
    repo_path: str | None = None


def _type_from_name(name: str) -> str:
    """Infer a connector type from a source name prefix (``git:``/``github:``/…)."""
    head = name.split(":", 1)[0].strip().lower()
    return head or "git"


def configured_source_specs() -> list[SourceSpec]:
    """Resolve the corpus source specs to index (dynamic; re-read each call).

    Precedence:
    1. ``AGENT_INDEX_SOURCES`` env — a comma-separated name list (legacy/testing);
       each name's type is inferred from its prefix.
    2. The grafted ``corpus.sources`` from adopted local projects
       (``config.read_corpus_sources()`` — the federated virtual config).
    3. The single default ``git``.
    """
    raw = os.environ.get("AGENT_INDEX_SOURCES")
    if raw:
        names = [s.strip() for s in raw.split(",") if s.strip()]
        if names:
            return [SourceSpec(name=n, type=_type_from_name(n)) for n in names]

    from agent_index import config as _cfg

    specs: list[SourceSpec] = []
    for entry in _cfg.read_corpus_sources():
        name = str(entry.get("name"))
        auth = entry.get("auth")
        specs.append(
            SourceSpec(
                name=name,
                type=str(entry.get("type") or _type_from_name(name)),
                repo=entry.get("repo"),
                auth_account=(auth or {}).get("account") if isinstance(auth, dict) else None,
                trust_domain=entry.get("trust_domain"),
                repo_path=entry.get("_repo_path"),
            )
        )
    if specs:
        return specs
    return [SourceSpec(name="git", type="git")]


def _resolve_repo_path(spec: SourceSpec) -> str | None:
    """Local checkout path for a ``git`` source.

    Precedence: an explicit ``repo:`` target resolved via the agent-worktrees
    registry (so a *central* config can list OTHER repos, not just the declaring
    one), then the grafted ``_repo_path`` (a self-declaring repo's own checkout),
    then the source name's tail resolved via the registry.
    """
    from agent_index import config as _cfg

    if spec.repo:
        p = _cfg.repo_checkout_path(spec.repo)
        if p:
            return str(p)
    if spec.repo_path:
        return spec.repo_path
    tail = spec.name.split(":", 1)[-1]
    if tail:
        p = _cfg.repo_checkout_path(tail)
        if p:
            return str(p)
    return None


def _resolve_gh_token(account: str) -> str | None:
    """Resolve a GitHub token for ``account`` via ``gh auth token --user`` (no
    stored secret). Used for ``github`` issue/PR sources whose owning account
    differs per repo (EMU vs personal)."""
    try:
        import subprocess

        out = subprocess.run(  # noqa: S603
            ["gh", "auth", "token", "--user", account],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[-1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _connector_kwargs(spec: SourceSpec) -> dict[str, object]:
    """Per-source connector kwargs. Raises on an *explicitly-targeted* source that
    can't be resolved so the run loop records it as failed (never a silent no-op;
    #1350). The bare default ``git`` source (no repo/repo_path) resolves to no
    kwargs, letting ``GitRepoConnector`` fall back to its cwd/env default."""
    if spec.type == "git":
        path = _resolve_repo_path(spec)
        if path:
            return {"repo_path": path}
        if spec.repo or spec.repo_path:
            raise RuntimeError(
                f"git source {spec.name!r}: could not resolve a checkout path "
                f"(repo={spec.repo!r}) via the agent-worktrees registry"
            )
        return {}  # bare default 'git' — connector uses cwd / AGENT_INDEX_GIT_REPO
    if spec.type == "github":
        if not spec.auth_account:
            return {}  # anonymous (low rate limit) — the connector warns
        token = _resolve_gh_token(spec.auth_account)
        if not token:
            raise RuntimeError(
                f"github source {spec.name!r}: could not resolve a token for "
                f"account {spec.auth_account!r} (gh auth token --user)"
            )
        return {"token": token}
    return {}


def run_reindex(
    *,
    full: bool = False,
    source: str | None = None,
    progress_cb: ProgressCallback | None = None,
    resume_since: float | None = None,
) -> dict[str, object]:
    """Run the four-phase indexing pipeline.

    Args:
        full: If True, do a full crawl (discover everything, not just changes).
        source: If set, only reindex this specific source.
        progress_cb: Optional callback for progress reporting and cancellation.
        resume_since: When set (a resumed task's ``created_at`` epoch), files
            already stored at the same content hash **within this task's window**
            (``path_index.indexed_at >= resume_since``) are skipped — so an
            interrupted reindex resumes mid-source instead of restarting. ``None``
            (a fresh run) re-embeds everything the crawl selected, preserving
            full-rebuild semantics.

    Returns:
        Dict with keys ``chunks_total``, ``chunks_deleted``, ``files_crawled``,
        and ``duration_seconds`` (plus ``clusters`` when the post-index cluster
        pass runs). If one or more sources fail to index, the run continues past
        them and a ``sources_failed`` list of ``{"source", "error"}`` entries is
        also included (see #1350).
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

    sources_to_index: list[SourceSpec]
    if source is None:
        sources_to_index = configured_source_specs()
    else:
        # An explicit --source names one source: prefer its configured spec so
        # its repo/auth still resolve; otherwise synthesize a bare spec.
        by_name = {s.name: s for s in configured_source_specs()}
        sources_to_index = [
            by_name.get(source) or SourceSpec(name=source, type=_type_from_name(source))
        ]

    total_chunks = 0
    total_deleted = 0
    total_files_crawled = 0
    failed_sources: list[dict[str, str]] = []
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

        for spec in sources_to_index:
            src_name = spec.name
            if progress_cb:
                progress_cb.check_cancelled()
                progress_cb.source_started(src_name)

            try:
                connector_kwargs = _connector_kwargs(spec)
                stored, deleted, crawled = _index_source(
                    src_name,
                    config=config,
                    state=state,
                    path_index=path_index,
                    multi_store=multi_store,
                    multi_clients=multi_clients,
                    full=full,
                    progress_cb=progress_cb,
                    resume_since=resume_since,
                    connector_kwargs=connector_kwargs,
                )
                total_chunks += stored
                total_deleted += deleted
                total_files_crawled += crawled
            except IndexingCancelled:
                raise
            except Exception as exc:
                logger.exception("Failed to index source: %s", src_name)
                print(f"  ERROR: {src_name} failed - continuing with next source")
                # Record the failure so it is surfaced in the result instead of
                # masquerading as a clean ``files_crawled: 0`` (a wholly-failed
                # source otherwise looked identical to "found nothing new"; #1350).
                failed_sources.append({"source": src_name, "error": str(exc)})

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

    result: dict[str, object] = {
        "chunks_total": total_chunks,
        "chunks_deleted": total_deleted,
        "files_crawled": total_files_crawled,
        "duration_seconds": round(elapsed, 1),
    }
    if failed_sources:
        result["sources_failed"] = failed_sources

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
    resume_since: float | None = None,
    connector_kwargs: dict[str, object] | None = None,
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
        connector_kwargs=connector_kwargs,
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

        total_stored, upserted_files = _embed_and_store_files(
            manifest.upserts,
            multi_store=multi_store,
            multi_clients=multi_clients,
            model_profiles=config.model_profiles,
            path_index=path_index,
            stream_batch_size=config.stream_batch_size,
            progress_cb=progress_cb,
            resume_since=resume_since,
        )
        # NOTE: ``upserted_files`` holds only files this run actually (re)stored;
        # files SKIPPED on resume are excluded, so Phase 3b stale-cleanup (which
        # deletes chunks older than run_start) won't drop their valid chunks.

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
    connector_kwargs: dict[str, object] | None = None,
) -> CrawlManifest:
    """Phase 1: Crawl a source and produce a CrawlManifest.

    For full crawls: discover all files, diff paths against stored for deletions.
    For incremental: list current paths (cheap), diff for deletions,
    then discover changed files for upserts.
    """
    from agent_index.indexing.manifest import CrawlManifest, CrawlStats, DeletedFile
    from agent_index.sources import get_connector

    connector = get_connector(source_name, **(connector_kwargs or {}))
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
    resume_since: float | None = None,
) -> tuple[int, set[tuple[str, str]]]:
    """Phase 2: Chunk files, embed via the engine, upsert to stores.

    Checkpoints ``path_index`` after EACH batch flush (with content hashes) so an
    interruption leaves a crash-consistent record of what is stored. When
    ``resume_since`` is set, files already stored at the same content hash within
    the task window (``indexed_at >= resume_since``) are skipped, so a resumed run
    continues mid-source instead of restarting.

    Returns ``(total_chunks_stored, stored_files)`` where ``stored_files`` is the
    set of ``(source, path)`` this run actually (re)stored — excluding skipped
    files, so Phase 3 stale-cleanup does not delete their still-valid chunks.
    """
    from agent_index.chunking import get_chunker

    batch: list[Chunk] = []
    total_chunks = 0
    total_stored = 0
    stored_files: set[tuple[str, str]] = set()
    skipped = 0
    # Bulk-load the stored-file map ONCE for resume lookups (avoids opening a
    # SQLite connection per file on large sources).
    resume_index: dict[tuple[str, str], tuple[str | None, float]] = (
        path_index.get_all_entries() if resume_since is not None else {}
    )
    # Files whose chunks are in the current (not-yet-flushed) batch, with their
    # content hash + chunk count — checkpointed to path_index on each flush. A
    # file's chunks never straddle a flush (a whole file is added before the
    # size check), so every pending file is fully persisted once the batch stores.
    pending: list[tuple[str, str, str, int]] = []

    def _flush() -> None:
        nonlocal batch, total_stored, pending
        if not batch:
            return
        if progress_cb:
            progress_cb.check_cancelled()
        total_stored += _embed_and_store_batch(
            batch, multi_store, multi_clients,
            model_profiles,
            stream_batch_size=stream_batch_size,
        )
        if pending:
            path_index.mark_indexed_batch(pending)  # crash-consistent checkpoint
        pending = []
        batch = []
        if progress_cb:
            progress_cb.batch_complete(total_stored, total_chunks)

    for entry in files:
        if progress_cb:
            progress_cb.check_cancelled()

        chash = _content_hash(entry.content)

        # Resume-skip: already stored at this hash within THIS task's window.
        if resume_since is not None:
            prior = resume_index.get((entry.source, entry.path))
            if prior is not None and prior[0] == chash and prior[1] >= resume_since:
                skipped += 1
                continue

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
        pending.append((entry.source, entry.path, chash, len(chunks)))
        stored_files.add((entry.source, entry.path))

        if len(batch) >= stream_batch_size:
            _flush()

    # Final partial batch
    _flush()

    msg = f"  Embedded: {total_chunks} chunks generated, {total_stored} stored"
    if skipped:
        msg += f" ({skipped} files skipped -- already stored this run)"
    print(msg)
    return (total_stored, stored_files)


def _content_hash(content: object) -> str:
    """Stable content hash for resume comparison (sha256 hex, first 16 chars)."""
    data = content if isinstance(content, bytes) else str(content).encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()[:16]


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
