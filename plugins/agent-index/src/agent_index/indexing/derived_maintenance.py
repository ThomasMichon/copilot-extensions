"""Reuse proven derived artifacts while retaining explicit full repair."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_index.store.projection_snapshot import ProjectionSnapshotUnavailable, cluster_signature
from agent_index.store.source_layout import selected_layout
from agent_index.indexing.work_counts import current_counts

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.store.multi_model_store import MultiModelStore

log = logging.getLogger(__name__)


def refresh_clusters(
    multi_store: MultiModelStore, config: IndexConfig, *, force: bool = False,
) -> dict[str, int | float] | None:
    if not config.cluster_enabled:
        return None
    counts = current_counts()
    attempted = False
    try:
        from agent_index.indexing.cluster_pass import run_clustering_pass
        from agent_index.store.cluster_store import ClusterStore

        store = ClusterStore(config.clusters_db)
        try:
            signature = cluster_signature(multi_store, config)
        except ProjectionSnapshotUnavailable:
            log.warning("Cluster coverage cannot be verified; running uncached maintenance", exc_info=True)
            signature = None
        if not force and signature is not None:
            cached = store.covered_stats(signature)
            if cached is not None and cluster_signature(multi_store, config) == signature:
                if counts is not None:
                    counts.cluster_cache_hits += 1
                return cached
        print("\nRefreshing similarity clusters...")
        attempted = True
        if counts is not None:
            counts.cluster_pass_attempts += 1
        stats = run_clustering_pass(multi_store, store, config, coverage_signature=signature)
        if counts is not None:
            counts.cluster_pass_completed += 1
        print(
            f"  {stats['clusters']} clusters across {stats['slices']} slices "
            f"({stats['items']} items, {stats['elapsed_ms']}ms)",
        )
        return stats
    except Exception:
        if attempted and counts is not None:
            counts.cluster_pass_failures += 1
        if selected_layout():
            raise
        log.warning("Clustering pass failed", exc_info=True)
        print("  WARNING: clustering pass failed (see logs)")
        return None
