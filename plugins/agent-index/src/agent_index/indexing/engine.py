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
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, replace
from functools import partial
from typing import TYPE_CHECKING

from agent_index.store.source_layout import selected_layout
from agent_index.indexing.work_counts import current_counts, measure_reindex

# UTF-8 stdio so human-facing status glyphs render safely (Rule B, #769).
_utf8_sys = __import__("sys")
_utf8_sys.stdout.reconfigure(encoding="utf-8")
_utf8_sys.stderr.reconfigure(encoding="utf-8")

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_index.chunking.base import Chunk
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig, ModelProfile
    from agent_index.indexing.change_hints import ChangeHints
    from agent_index.indexing.embedding_coverage import EmbeddingCoverage
    from agent_index.indexing.embedding_runtime import EmbeddingRuntime
    from agent_index.indexing.gc import SourceRetirementPolicy
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.indexing.state import IndexState
    from agent_index.sources.base import FileEntry, SourceConnector
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
    ref: str | None = None
    auth_gateway: str | None = None
    remote_url: str | None = None
    auth_relay: str | None = None


def _type_from_name(name: str) -> str:
    """Infer a connector type from a source name prefix (``git:``/``github:``/…)."""
    head = name.split(":", 1)[0].strip().lower()
    return head or "git"


def _resolve_explicit_source_spec(
    source: str, by_name: dict[str, SourceSpec]
) -> SourceSpec:
    """Resolve one explicitly-named ``--source``/``{"source": ...}`` request.

    Prefers the matching configured spec so its repo/auth resolve correctly.
    ``git:<name>:commits`` and ``github:<owner>/<repo>:issues``/``:pulls`` are
    never themselves configured sources -- each is a BYPRODUCT its connector's
    ``discover()`` emits alongside its sibling content (files+commits in one
    git crawl; issues+pulls in one GitHub issues-API crawl), using whatever
    identity its *base* source resolves. A bare synthesized spec for one of
    these has no repo/repo_path/auth_account, so it used to either silently
    fall back to ``GitRepoConnector``'s cwd/env default (virtually always the
    wrong repo, surfacing only as an opaque ``git ls-files`` exit-128) or, for
    ``github:``, get constructed with the SUFFIX STILL IN THE NAME --
    ``GitHubConnector._parse_source`` naively splits on the first ``/``, so
    ``github:owner/repo:issues`` parses as repo ``"repo:issues"``, producing a
    malformed API URL and a 404 on every request (confirmed identically across
    all of ``:issues``/``:pulls`` for every configured github source).

    Resolve the request against its BASE source's spec instead (returned
    as-is, so the connector is constructed with the correct bare name and
    inherits the base's real repo/repo_path/auth_account/ref) -- a full
    reindex of the base naturally refreshes all of its byproducts together,
    same as requesting the base name directly. Any other unresolvable name
    synthesizes a bare spec, which ``_connector_kwargs`` now raises loudly on
    (see its docstring) rather than silently defaulting.
    """
    spec = by_name.get(source)
    if spec is None:
        for suffix in (":commits", ":issues", ":pulls"):
            if source.endswith(suffix):
                spec = by_name.get(source[: -len(suffix)])
                break
    return spec or SourceSpec(name=source, type=_type_from_name(source))


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
        if isinstance(auth, dict):
            from agent_index.sources.credentials import validate_auth_selection
            validate_auth_selection(auth)
        specs.append(
            SourceSpec(
                name=name,
                type=str(entry.get("type") or _type_from_name(name)),
                repo=entry.get("repo"),
                auth_account=(auth or {}).get("account") if isinstance(auth, dict) else None,
                trust_domain=entry.get("trust_domain"),
                repo_path=entry.get("_repo_path"),
                ref=entry.get("ref"),
                auth_gateway=(auth or {}).get("gateway") if isinstance(auth, dict) else None,
                remote_url=entry.get("remote"),
                auth_relay=(auth or {}).get("relay") if isinstance(auth, dict) else None,
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
    if spec.auth_gateway is not None or spec.auth_relay is not None:
        from agent_index.sources.credentials import gateway_connector_kwargs

        return gateway_connector_kwargs(spec)
    if spec.type == "git":
        kwargs: dict[str, object] = {}
        path = _resolve_repo_path(spec)
        if path:
            kwargs["repo_path"] = path
        elif spec.name != "git":
            # Any OTHER explicitly-named git source (including one synthesized
            # for a name that doesn't match a configured spec) that still can't
            # resolve a checkout path must fail loudly here -- never silently
            # fall through to GitRepoConnector's cwd/env default, which is
            # virtually always the WRONG repo (or no repo at all) for a named
            # request and previously surfaced only as an opaque `git ls-files`
            # exit-128 deep in a subprocess traceback (see the ``:commits``
            # case this guards against, just below).
            raise RuntimeError(
                f"git source {spec.name!r}: could not resolve a checkout path "
                f"(repo={spec.repo!r}) via the agent-worktrees registry"
            )
        if spec.ref:
            kwargs["ref"] = spec.ref
        if spec.auth_account:
            # Authenticate the fetch step so the checkout is actively pulled
            # forward as the remote's tracked branch moves, rather than
            # depending on whichever account happens to be ambient-active in
            # the system's `gh`-backed git credential helper (which may not be
            # the account THIS repo needs -- e.g. an EMU org repo vs. a
            # personal one). Unlike the github: (issues/PRs) connector, a
            # resolution failure here is non-fatal: GitRepoConnector's own
            # fetch already falls back gracefully to stale-but-canonical
            # remote-tracking state or the local HEAD (see its module
            # docstring), so proceed unauthenticated rather than failing the
            # whole source.
            token = _resolve_gh_token(spec.auth_account)
            if token:
                kwargs["token"] = token
        return kwargs
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


@measure_reindex
def run_reindex(
    *,
    full: bool = False,
    source: str | None = None,
    progress_cb: ProgressCallback | None = None,
    resume_since: float | None = None,
    config: IndexConfig | None = None,
    source_specs: list[SourceSpec] | None = None,
    embedding_runtime: EmbeddingRuntime | None = None,
    change_hints: ChangeHints | None = None,
    retirement_policy: SourceRetirementPolicy | None = None,
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
            (a fresh incremental run) also reuses compatible file checkpoints.
            ``full=True`` forces rebuilding files outside a resumed task window.
        config: Optional consumer configuration; ordinary callers keep defaults.
        source_specs: Optional complete consumer source selection. Connectors
            still use the supported registry and existing recovery identity.
        embedding_runtime: Optional owned client/lifecycle policy. Clients are
            closed on exit and readiness remains deferred until embedding.
        change_hints: Required exact-source acquisition identities; never a poll fallback.
        retirement_policy: Optional source inventory and legacy naming policy.
            Defaults retain configured-source and registered-source collection.

    Returns:
        Dict with keys ``chunks_total``, ``chunks_deleted``, ``files_crawled``,
        and ``duration_seconds`` (plus ``clusters`` when the post-index cluster
        pass runs). If one or more sources fail to index, the run continues past
        them and a ``sources_failed`` list of ``{"source", "error"}`` entries is
        also included (see #1350).
        ``work_counts`` contains versioned round-local measurements, not coverage.
    """
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.embedding_runtime import (
        default_embedding_runtime,
        open_embedding_clients,
    )
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.gc import SourceRetirementPolicy
    from agent_index.indexing.pending_crawl import PendingCrawlConflict
    from agent_index.indexing.runner import IndexingCancelled
    from agent_index.indexing.state import IndexState
    from agent_index.store.multi_model_store import MultiModelStore

    config = config if config is not None else IndexConfig()
    runtime = embedding_runtime if embedding_runtime is not None else default_embedding_runtime()
    retirement = retirement_policy if retirement_policy is not None else SourceRetirementPolicy()
    configured = list(source_specs) if source_specs is not None else configured_source_specs()
    sources_to_index = (
        configured if source is None else
        [_resolve_explicit_source_spec(source, {spec.name: spec for spec in configured})]
    )
    hinted_connector = None
    if change_hints is not None:
        from agent_index.indexing.hinted_crawl import prepare_hint_connector

        if progress_cb:
            progress_cb.check_cancelled()
        hinted_connector = prepare_hint_connector(
            source, full, change_hints, configured, _connector_kwargs,
        )
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
    from agent_index.store.source_layout import initialize_source_store

    multi_store = (
        initialize_source_store(config) if selected_layout() else
        MultiModelStore(config.lance_dir, content_table=config.content_table)
    )
    multi_clients, client_cleanup = open_embedding_clients(
        multi_store, config.model_profiles, runtime,
    )

    # Track which on-demand engines we start so we can stop them afterwards.
    # Initialized before the try so the finally can always see it, even if
    # ensuring engines (or the migration) fails partway.
    started_engines: list[ModelProfile] = []
    engines_ready = False

    def prepare_embedding() -> None:
        nonlocal engines_ready
        if engines_ready or not runtime.manage_engines:
            return
        for profile in config.model_profiles.values():
            if runtime.ensure_engine(profile, multi_clients[profile.model_id]):
                started_engines.append(profile)
        engines_ready = True

    total_chunks = 0
    total_deleted = 0
    total_files_crawled = 0
    failed_sources: list[dict[str, str]] = []
    sources_purged: list[str] = []
    gc_deferred: list[str] = []
    start = time.monotonic()

    try:
        for spec in sources_to_index:
            src_name = spec.name
            if progress_cb:
                progress_cb.check_cancelled()
                progress_cb.source_started(src_name)

            try:
                connector_kwargs = {} if hinted_connector is not None else _connector_kwargs(spec)
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
                    before_store=prepare_embedding,
                    source_scope=hashlib.sha256(
                        json.dumps(asdict(spec), sort_keys=True).encode(),
                    ).hexdigest(),
                    connector=hinted_connector, change_hints=change_hints,
                )
                total_chunks += stored
                total_deleted += deleted
                total_files_crawled += crawled
            except IndexingCancelled:
                raise
            except Exception as exc:
                if change_hints is not None:
                    raise
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
        try:
            client_cleanup.close()
        finally:
            # Lifecycle cleanup still runs if an injected client's close fails.
            for profile in started_engines:
                try:
                    runtime.stop_engine(profile)
                except Exception:
                    logger.warning(
                        "Failed to stop on-demand engine '%s'",
                        profile.model_id, exc_info=True,
                    )

    # Purge sources removed from the CURRENT corpus config (a harness's own
    # checked-in defaults, a knowledge-repo overlay, or a personal/machine
    # override) -- distinct from `gc_stale_sources`'s abandoned-naming-scheme
    # cleanup below, and unlike it, cheap and safe on EVERY reindex (not just
    # `--full`). Runs only when `source` names the whole configured set (an
    # explicit single `--source` invocation doesn't see the full set, so it
    # would otherwise purge every OTHER configured source). This is what lets
    # a config change -- e.g. removing a source from `.agent-index/config.yaml`
    # -- take effect on the very next routine reindex tick, with no full
    # reindex and no service restart required.
    if (source is None and retirement.configured_snapshot
            and os.environ.get("AGENT_INDEX_REINDEX_GC", "1") != "0"):
        try:
            from agent_index.indexing.gc import gc_configured_snapshot

            configured_names = frozenset(spec.name for spec in sources_to_index)
            gc_summary = gc_configured_snapshot(
                multi_store, path_index, state, configured_names=configured_names,
                source_aware=bool(selected_layout()),
            )
            gc_deferred = gc_summary.get("deferred", [])
            for pending in gc_summary.get("pending_crawls", []):
                if not any(failure["source"] == pending for failure in failed_sources):
                    failed_sources.append({
                        "source": pending,
                        "error": "Complete pending crawl with its original configuration "
                                 "before source retirement",
                    })
            purged = gc_summary["purged"]
            if purged:
                print("\nPurging sources removed from config...")
                for src, cnt in sorted(purged.items()):
                    print(f"  purged {src}: {cnt} chunks")
                total_deleted += gc_summary["chunks_deleted"]
                sources_purged = sorted(purged)
        except PendingCrawlConflict:
            raise
        except Exception:
            if selected_layout():
                raise
            logger.warning("Unconfigured-source GC failed", exc_info=True)
            print("  WARNING: unconfigured-source GC failed (see logs)")

    if full and not failed_sources:
        state.last_full_reindex = time.time()

        # Purge stale source generations (abandoned naming schemes) BEFORE
        # compaction so the reclaimed space reflects the de-duplicated
        # baseline (#879). Keyed off the naming pattern, so a live per-repo
        # source that produced no rows this run is never purged. Opt-out via
        # AGENT_INDEX_REINDEX_GC=0 -- GC purges any source not matching the current
        # scheme, so if remote /api/ingest producers (e.g. a revived
        # agent-index-push-daemon) write machine-scoped sources, either disable this
        # or add their names to AGENT_INDEX_GC_KEEP_SOURCES.
        if (retirement.stale_generations and not gc_deferred
                and os.environ.get("AGENT_INDEX_REINDEX_GC", "1") != "0"):
            print("\nGarbage-collecting stale source generations...")
            try:
                from agent_index.indexing.gc import gc_stale_sources

                if retirement_policy is None:
                    gc_summary = gc_stale_sources(multi_store, path_index, state)
                else:
                    gc_summary = gc_stale_sources(
                        multi_store, path_index, state, policy=retirement,
                    )
                purged = gc_summary["purged"]
                if purged:
                    for src, cnt in sorted(purged.items()):
                        print(f"  purged {src}: {cnt} chunks")
                    print(f"  GC removed {gc_summary['chunks_deleted']} stale chunks")
                else:
                    print("  No stale sources found")
            except PendingCrawlConflict:
                raise
            except Exception:
                if selected_layout():
                    raise
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
            if selected_layout():
                raise
            logger.warning("Post-reindex compaction failed", exc_info=True)
            print("  WARNING: compaction failed (see logs)")

    if selected_layout() and not multi_store.content_store.fts_current():
        # Cover the final content version, including all post-index GC and compaction.
        counts = current_counts()
        if counts is not None:
            counts.fts_ensure_attempts += 1
        multi_store.ensure_fts_index()
        if counts is not None:
            counts.fts_ensure_completed += 1
    state.save(config.state_file)

    elapsed = time.monotonic() - start

    result: dict[str, object] = {
        "chunks_total": total_chunks,
        "chunks_deleted": total_deleted,
        "files_crawled": total_files_crawled,
        "duration_seconds": round(elapsed, 1),
    }
    if failed_sources:
        result["sources_failed"] = failed_sources
    if sources_purged:
        result["sources_purged"] = sources_purged
    if gc_deferred:
        result["source_gc_deferred"] = gc_deferred

    # Refresh the similarity-cluster artifact from the just-updated vectors.
    # Reuses stored embeddings (no re-embedding), so it runs post-index in the
    # same pipeline. Legacy clustering remains optional; selected failures propagate.
    cluster_stats = _refresh_clusters(multi_store, config, force=full)
    if cluster_stats is not None:
        result["clusters"] = cluster_stats["clusters"]

    if selected_layout():
        with multi_store._source_domain.read(multi_store.content_store.db):
            pass
    print(f"\nIndexing complete: {total_chunks} chunks in {elapsed:.1f}s")
    return result


setattr(run_reindex, "change_hints_versions", (1,))


def _refresh_clusters(
    multi_store: MultiModelStore, config: IndexConfig, *, force: bool = False,
) -> dict[str, int | float] | None:
    """Recompute clusters; selected-domain failures must fail the indexing task.

    Sweeps stored centroids and replaces ``clusters.db``; never re-embeds.
    Returns the pass stats dict, or ``None`` when clustering is disabled or
    fails in legacy mode. Selected mode propagates failures without recovery.
    """
    from agent_index.indexing.derived_maintenance import refresh_clusters

    return refresh_clusters(multi_store, config, force=force)


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
    before_store: Callable[[], None] | None = None,
    source_scope: str | None = None,
    connector: SourceConnector | None = None,
    change_hints: ChangeHints | None = None,
) -> tuple[int, int, int]:
    """Replay pinned partial acquisition before observing newer source changes."""
    from agent_index.indexing.pending_crawl import recover_source, recovery_scope
    from agent_index.sources import get_connector

    if connector is None:
        connector = get_connector(source_name, **(connector_kwargs or {}))
    return recover_source(
        source_name, scope=recovery_scope(source_scope or source_name, connector), state=state,
        state_file=config.state_file, paths=path_index, full=full, family=connector.source_name,
        acquire=partial(
            _crawl_source, source_name, config=config, state=state, path_index=path_index,
            full=full, progress_cb=progress_cb, connector_kwargs=connector_kwargs,
            connector=connector, source_scope=source_scope, change_hints=change_hints,
        ),
        apply=partial(
            _apply_source_manifest, source_name, config=config, state=state,
            path_index=path_index, multi_store=multi_store, multi_clients=multi_clients,
            progress_cb=progress_cb, resume_since=resume_since, before_store=before_store,
        ),
    )


def _apply_source_manifest(
    source_name: str,
    *,
    manifest: CrawlManifest,
    config: IndexConfig,
    state: IndexState,
    path_index: PathIndex,
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    full: bool,
    progress_cb: ProgressCallback | None = None,
    resume_since: float | None = None,
    before_store: Callable[[], None] | None = None,
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
    counts = current_counts()
    if manifest.is_empty:
        print(f"  No changes detected for {source_name}")
        # Still update commit marker so we don't re-crawl next time
        src_state = state.sources.get(source_name)
        state.mark_source_indexed(
            source_name,
            manifest.commit,
            src_state.chunk_count if src_state else 0,
        )
        if manifest.stored_family is not None:
            state.stored_families[source_name] = manifest.stored_family
        if counts is not None:
            counts.manifests_applied += 1
        return (0, 0, 0)

    stats = manifest.stats
    print(f"  Crawl: {stats.files_changed} to index, {stats.files_deleted} to delete")

    if progress_cb:
        progress_cb.file_discovered(stats.files_changed, total=stats.files_changed)

    # ── Phase 2: Embed/Store upserts ────────────────────────
    total_stored = 0
    stale_cleaned = 0

    def reconcile_file(src: str, path: str, keep_ids: set[str]) -> None:
        nonlocal stale_cleaned
        removed = multi_store.reconcile_file_chunks(src, path, keep_ids)
        stale_cleaned += removed
        if counts is not None:
            counts.stale_chunk_ids_deleted += removed

    if manifest.upserts:
        if progress_cb:
            progress_cb.phase("chunking", msg=f"Chunking {len(manifest.upserts)} files")

        total_stored, _stored_files = _embed_and_store_files(
            manifest.upserts,
            multi_store=multi_store,
            multi_clients=multi_clients,
            model_profiles=config.model_profiles,
            path_index=path_index,
            reconcile_file=reconcile_file,
            stream_batch_size=config.stream_batch_size,
            progress_cb=progress_cb,
            resume_since=resume_since,
            force=full,
            before_store=before_store,
            chunker_revision=getattr(config, "chunker_revision", "1"),
        )

    # ── Phase 3: Reconcile ──────────────────────────────────
    if progress_cb:
        progress_cb.phase("reconciling", msg="Cleaning up deletions and stale chunks")

    total_deleted = 0

    # 3a. Explicit deletions (files removed from source)
    for deleted in manifest.deletions:
        if progress_cb:
            progress_cb.check_cancelled()
        try:
            path_index.invalidate_batch([(deleted.source, deleted.path)])
            # Multi-model store (content + vectors)
            removed_ids = multi_store.delete_by_file(deleted.source, deleted.path)
            if counts is not None:
                counts.file_chunk_ids_deleted += len(removed_ids)
            # Path index
            path_index.remove(deleted.source, deleted.path)
            total_deleted += 1
        except Exception:
            logger.warning(
                "Failed to delete %s:%s", deleted.source, deleted.path,
                exc_info=True,
            )
            raise

    if total_deleted or stale_cleaned:
        print(f"  Reconciled: {total_deleted} files deleted, {stale_cleaned} stale chunks cleaned")

    # ── Phase 4: Commit state ───────────────────────────────
    indexed_count = path_index.chunk_count_by_family(
        manifest.accounting_family or manifest.stored_family or source_name,
    )
    state.mark_source_indexed(source_name, manifest.commit, indexed_count)
    if manifest.stored_family is not None:
        state.stored_families[source_name] = manifest.stored_family

    total_removed = total_deleted + stale_cleaned
    if counts is not None:
        counts.manifests_applied += 1
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
    connector: SourceConnector | None = None,
    source_scope: str | None = None,
    change_hints: ChangeHints | None = None,
) -> CrawlManifest:
    """Compatibility entrypoint for the separately owned crawl phase."""
    from agent_index.indexing.crawl import collect_source_changes

    if progress_cb:
        progress_cb.phase("crawling", msg=f"Crawling {source_name}")
    manifest = collect_source_changes(
        source_name, state=state, path_index=path_index, full=full,
        source_aware=bool(selected_layout()), progress_cb=progress_cb,
        connector_kwargs=connector_kwargs,
        connector=connector, source_scope=source_scope, change_hints=change_hints,
    )
    counts = current_counts()
    if counts is not None:
        counts.manifests_acquired += 1
        counts.identities_scanned += manifest.stats.files_scanned
        counts.upserts_acquired += len(manifest.upserts)
        counts.tombstones_acquired += len(manifest.deletions)
    return manifest


def _embed_and_store_files(
    files: list[FileEntry],
    *,
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    model_profiles: dict[str, ModelProfile] | None = None,
    path_index: PathIndex,
    reconcile_file: Callable[[str, str, set[str]], None],
    stream_batch_size: int = STREAM_BATCH_SIZE,
    progress_cb: ProgressCallback | None = None,
    resume_since: float | None = None,
    force: bool = False,
    before_store: Callable[[], None] | None = None,
    chunker_revision: str = "1",
) -> tuple[int, set[tuple[str, str]]]:
    """Phase 2: Chunk files, embed via the engine, upsert to stores.

    Reconciles stale chunks before checkpointing EACH completed batch, so an
    interruption never publishes reusable work with unfinished cleanup. Compatible
    file checkpoints are reused on normal rounds. An explicit full rebuild
    forces writes, except for compatible work committed in its retry window.

    Returns ``(total_chunks_stored, stored_files)`` where ``stored_files`` is the
    set of ``(source, path)`` this run actually (re)stored — excluding skipped
    files, so Phase 3 stale-cleanup does not delete their still-valid chunks.
    """
    from agent_index.chunking import get_chunker
    from agent_index.indexing.embedding_coverage import EmbeddingCoverage
    from agent_index.indexing.fingerprints import file_fingerprint
    from agent_index.store.content_mutations import coalesce_chunks

    coverage = EmbeddingCoverage(path_index)
    batch: list[Chunk] = []
    total_chunks = 0
    total_stored = 0
    stored_files: set[tuple[str, str]] = set()
    skipped = 0
    counts = current_counts()
    resume_index = path_index.get_entries([(entry.source, entry.path) for entry in files])
    if counts is not None:
        counts.checkpoint_lookup_keys += len(files)
        counts.checkpoint_rows_found += len(resume_index)
    # Files whose chunks are in the current (not-yet-flushed) batch, with their
    # content hash + chunk count — checkpointed to path_index on each flush. A
    # file's chunks never straddle a flush (a whole file is added before the
    # size check), so every pending file is fully persisted once the batch stores.
    pending: list[tuple[str, str, str, int]] = []

    def _checkpoint() -> None:
        nonlocal pending
        retained: dict[tuple[str, str], set[str]] = {}
        for chunk in batch:
            retained.setdefault((chunk.source, chunk.file_path), set()).add(chunk.chunk_id)
        for source, path, _hash, _count in pending:
            keep_ids = retained.get((source, path), set())
            reconcile_file(source, path, keep_ids)
            coverage.retain(source, path, keep_ids)
        path_index.mark_indexed_batch(pending)
        if counts is not None:
            counts.checkpoint_rows_written += len(pending)
        pending = []

    def _flush() -> None:
        nonlocal batch, total_stored, pending
        if not batch:
            if pending:
                path_index.invalidate_batch([(s, p) for s, p, _h, _n in pending])
                _checkpoint()
            return
        if progress_cb:
            progress_cb.check_cancelled()
        path_index.invalidate_batch([(s, p) for s, p, _h, _n in pending])
        total_stored += _embed_and_store_batch(
            batch, multi_store, multi_clients,
            model_profiles,
            stream_batch_size=stream_batch_size,
            coverage=coverage, force=force, before_embed=before_store,
            skip_identical_content=not force,
        )
        if pending:
            _checkpoint()
        batch = []
        if progress_cb:
            progress_cb.batch_complete(total_stored, total_chunks)

    for entry in files:
        if progress_cb:
            progress_cb.check_cancelled()

        chash = file_fingerprint(entry, model_profiles, chunker_revision=chunker_revision)

        prior = resume_index.get((entry.source, entry.path))
        if prior is not None and prior[0] == chash and (
            not force or (resume_since is not None and prior[1] >= resume_since)
        ):
            skipped += 1
            if counts is not None:
                counts.files_reused += 1
            continue

        chunker, _lang = get_chunker(entry.path)
        try:
            chunks = chunker.chunk(entry.content, entry.path, source=entry.source)
        except Exception:
            logger.warning("Failed to chunk %s", entry.path, exc_info=True)
            raise

        if getattr(entry, "metadata", None):
            chunks = [replace(c, metadata=entry.metadata) for c in chunks]
        chunks = coalesce_chunks(chunks)
        if counts is not None:
            counts.files_chunked += 1
            counts.chunks_generated += len(chunks)

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
        msg += f" ({skipped} compatible file checkpoints reused)"
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
    coverage: EmbeddingCoverage | None = None,
    force: bool = False,
    before_embed: Callable[[], None] | None = None,
    skip_identical_content: bool = False,
) -> int:
    """Compatibility seam for the routed batch writer."""
    from agent_index.indexing.embedding_batch import embed_and_store_batch

    return embed_and_store_batch(
        chunks, multi_store, multi_clients, model_profiles,
        stream_batch_size=stream_batch_size, coverage=coverage, force=force,
        before_embed=before_embed, skip_identical_content=skip_identical_content,
    )
