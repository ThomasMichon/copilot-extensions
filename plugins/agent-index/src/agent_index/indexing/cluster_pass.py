"""Offline similarity-clustering pass — the Phase 3 post-index step.

Sweeps every indexed content item, buckets its per-model centroids by
``(source_bucket, model_id)``, clusters each slice, and atomically replaces the
cluster artifact.  Runs after a reindex (vectors already current) so it reuses
stored embeddings and never re-embeds.

Kept thin and store-driven so it is callable from the server's post-index hook
and from a CLI/manual trigger alike.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from agent_index.store.clustering import Cluster, ClusterEntry, build_clusters, source_bucket
from agent_index.store.projection_snapshot import ClusterCoverage, cluster_signature

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.store.cluster_store import ClusterStore
    from agent_index.store.cluster_slice_inputs import ClusterSliceInputs
    from agent_index.store.multi_model_store import MultiModelStore

logger = logging.getLogger(__name__)


def run_clustering_pass(
    multi_store: MultiModelStore,
    cluster_store: ClusterStore,
    config: IndexConfig,
    *,
    coverage_signature: str | None = None,
    slice_inputs: ClusterSliceInputs | None = None,
) -> dict[str, int | float]:
    """Recompute all similarity clusters and replace the stored artifact.

    Returns a small stats dict (items, slices, clusters, elapsed_ms) for logs.
    """
    started = time.monotonic()

    # Group each item's per-space centroid into its (bucket, model_id) slice.
    slices: dict[tuple[str, str], list[ClusterEntry]] = defaultdict(list)
    bucket_items: Counter[str] = Counter()
    item_count = 0
    for rep in multi_store.iter_item_representations(strict=True):
        item_count += 1
        bucket = source_bucket(rep.source)
        bucket_items[bucket] += 1
        for model_id, centroid in rep.centroids.items():
            slices[(bucket, model_id)].append(
                ClusterEntry(
                    source=rep.source,
                    file_path=rep.file_path,
                    centroid=centroid,
                    content_hash=rep.content_hash,
                )
            )

    all_clusters: list[Cluster] = []
    for (bucket, model_id), entries in slices.items():
        threshold = config.cluster_threshold_for(bucket)
        all_clusters.extend(
            build_clusters(
                entries,
                bucket=bucket,
                model_id=model_id,
                threshold=threshold,
                min_size=config.cluster_min_size,
            )
        )

    coverage = None
    if coverage_signature is not None:
        if cluster_signature(multi_store, config) != coverage_signature:
            raise RuntimeError("Projection changed during clustering; artifact coverage was not published")
        coverage = ClusterCoverage(coverage_signature, item_count, len(slices), len(all_clusters))
    manifest = None
    if slice_inputs is not None:
        from agent_index.store.cluster_slice_inputs import cluster_slice_inputs, slice_manifest

        if cluster_slice_inputs(multi_store, config) != slice_inputs:
            raise RuntimeError("Projection changed during clustering; slice coverage was not published")
        cluster_counts = Counter((cluster.bucket, cluster.model_id) for cluster in all_clusters)
        seeded_items = {
            bucket: bucket_items.get(bucket, 0) for bucket in set(bucket_items) | slice_inputs.buckets
        }
        manifest = slice_manifest(slice_inputs, {
            (bucket, model): (len(slices.get((bucket, model), ())), cluster_counts[(bucket, model)])
            for bucket in seeded_items for model in slice_inputs.models
        }, seeded_items)
        if manifest.coverage != coverage:
            raise ValueError("Full cluster coverage does not match the slice seed")
    if manifest is None:
        cluster_store.replace_all(all_clusters, coverage=coverage)
    else:
        cluster_store.replace_all(all_clusters, coverage=coverage, slice_manifest=manifest)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    stats: dict[str, int | float] = {
        "items": item_count,
        "slices": len(slices),
        "clusters": len(all_clusters),
        "elapsed_ms": elapsed_ms,
    }
    logger.info(
        "Clustering pass: %d items -> %d clusters across %d slices (%dms)",
        item_count, len(all_clusters), len(slices), elapsed_ms,
    )
    return stats
