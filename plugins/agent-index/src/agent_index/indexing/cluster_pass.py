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
from collections import defaultdict
from typing import TYPE_CHECKING

from agent_index.store.clustering import Cluster, ClusterEntry, build_clusters, source_bucket

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.store.cluster_store import ClusterStore
    from agent_index.store.multi_model_store import MultiModelStore

logger = logging.getLogger(__name__)


def run_clustering_pass(
    multi_store: MultiModelStore,
    cluster_store: ClusterStore,
    config: IndexConfig,
) -> dict[str, int | float]:
    """Recompute all similarity clusters and replace the stored artifact.

    Returns a small stats dict (items, slices, clusters, elapsed_ms) for logs.
    """
    started = time.monotonic()

    # Group each item's per-space centroid into its (bucket, model_id) slice.
    slices: dict[tuple[str, str], list[ClusterEntry]] = defaultdict(list)
    item_count = 0
    for rep in multi_store.iter_item_representations():
        item_count += 1
        bucket = source_bucket(rep.source)
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

    cluster_store.replace_all(all_clusters)

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
