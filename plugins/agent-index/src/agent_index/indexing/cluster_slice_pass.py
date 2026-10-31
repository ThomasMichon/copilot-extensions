"""Opt-in bounded maintenance; missing proof requests repair, never an implicit sweep."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from agent_index.indexing.work_counts import current_counts
from agent_index.store.cluster_slice_coverage import (
    MAX_CLUSTER_SLICES, ClusterSliceCoverage, ClusterSliceManifest,
)
from agent_index.store.cluster_slice_inputs import (
    ClusterSliceInputs,
    ClusterSliceRepairRequired,
    cluster_slice_inputs,
)
from agent_index.store.cluster_store import ClusterStore
from agent_index.store.clustering import Cluster, ClusterEntry, build_clusters
from agent_index.store.projection_snapshot import ClusterCoverage

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.store.multi_model_store import MultiModelStore


def _refresh(
    multi_store: MultiModelStore, store: ClusterStore, config: IndexConfig,
    inputs: ClusterSliceInputs, previous: ClusterSliceManifest, row_budget: int,
) -> dict[str, int | float]:
    if inputs.basis != previous.basis:
        raise ClusterSliceRepairRequired("Cluster policy or coarse identity changed; explicit full repair required")
    before = {entry.key: entry for entry in previous.slices}
    buckets = {bucket for bucket, _count in previous.bucket_items} | inputs.buckets
    if len(buckets) * len(inputs.models) > MAX_CLUSTER_SLICES:
        raise ClusterSliceRepairRequired("Cluster slice roster exceeds its metadata budget")
    selected: dict[str, list[str]] = defaultdict(list)
    for bucket in sorted(buckets):
        for model in inputs.models:
            key = bucket, model
            if key not in before or before[key].signature != inputs.slice_signature(*key):
                selected[bucket].append(model)
    if not selected:
        raise ClusterSliceRepairRequired("Changed global coverage has no proven slice delta; explicit full repair required")

    started = time.monotonic()
    after = dict(before)
    bucket_items = dict(previous.bucket_items)
    clusters: list[Cluster] = []
    for bucket, models in selected.items():
        entries: dict[str, list[ClusterEntry]] = {model: [] for model in models}
        canonical = eligible = 0
        for rep in multi_store.iter_bucket_item_representations(
            bucket, model_ids=tuple(models), row_budget=row_budget, include_unembedded=True,
        ):
            canonical += 1
            eligible += bool(rep.centroids)
            for model, centroid in rep.centroids.items():
                entries[model].append(ClusterEntry(
                    rep.source, rep.file_path, centroid, rep.content_hash,
                ))
        unchanged = [model for model in inputs.models if model not in models]
        if not unchanged or all(before[(bucket, model)].items == 0 for model in unchanged):
            union = eligible
        elif (eligible == canonical
                or any(before[(bucket, model)].items == canonical for model in unchanged)):
            union = canonical
        else:
            raise ClusterSliceRepairRequired(
                f"Mixed-model item union for {bucket!r} is unproven; explicit full repair required"
            )
        bucket_items[bucket] = union
        for model, members in entries.items():
            built = build_clusters(
                members, bucket=bucket, model_id=model,
                threshold=config.cluster_threshold_for(bucket), min_size=config.cluster_min_size,
            )
            clusters.extend(built)
            after[(bucket, model)] = ClusterSliceCoverage(
                bucket, model, inputs.slice_signature(bucket, model), len(members), len(built),
            )
    manifest = ClusterSliceManifest(
        inputs.basis,
        ClusterCoverage(
            inputs.signature, sum(bucket_items.values()),
            sum(entry.items > 0 for entry in after.values()),
            sum(entry.clusters for entry in after.values()),
        ),
        tuple(after.values()), tuple(bucket_items.items()),
    )
    if cluster_slice_inputs(multi_store, config) != inputs:
        raise RuntimeError("Projection changed during clustering; slice coverage was not published")
    changed = tuple((bucket, model) for bucket, models in selected.items() for model in models)
    store.replace_slices(clusters, changed=changed, previous=previous, manifest=manifest)
    return {
        "items": manifest.coverage.items, "slices": manifest.coverage.slices,
        "clusters": manifest.coverage.clusters,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "slices_refreshed": len(changed), "buckets_refreshed": len(selected),
    }


def refresh_cluster_slices(
    multi_store: MultiModelStore, config: IndexConfig, *, force: bool,
) -> dict[str, int | float]:
    """Keep source exclusion across witness, computation, final check and SQLite commit."""
    row_budget = config.cluster_slice_row_budget
    if type(row_budget) is not int or row_budget < 0:
        raise ValueError("Cluster slice row budget must be a nonnegative integer")
    domain = multi_store._source_domain
    if domain is None:
        raise ClusterSliceRepairRequired("Bounded clustering requires a participating source domain")
    counts = current_counts()
    with domain.operation(multi_store.content_store.db):
        store = ClusterStore(config.clusters_db)
        inputs = cluster_slice_inputs(multi_store, config)
        previous = store.slice_manifest()
        if not force:
            if previous is None:
                raise ClusterSliceRepairRequired("Cluster slice coverage is missing; explicit full repair required")
            if (previous.basis == inputs.basis and previous.coverage.signature == inputs.signature
                    and cluster_slice_inputs(multi_store, config) == inputs):
                if counts is not None:
                    counts.cluster_cache_hits += 1
                return previous.coverage.stats()
        if counts is not None:
            counts.cluster_pass_attempts += 1
        try:
            if force:
                from agent_index.indexing.cluster_pass import run_clustering_pass

                stats = run_clustering_pass(
                    multi_store, store, config, coverage_signature=inputs.signature, slice_inputs=inputs,
                )
            else:
                assert previous is not None
                stats = _refresh(multi_store, store, config, inputs, previous, row_budget)
        except Exception:
            if counts is not None:
                counts.cluster_pass_failures += 1
            raise
        if counts is not None:
            counts.cluster_pass_completed += 1
        return stats
