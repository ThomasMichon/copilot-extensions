"""Witnessed slice keys over existing source-domain generations, not new authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .bucket_projection import bucket_tables
from .cluster_slice_coverage import ClusterSliceCoverage, ClusterSliceManifest
from .clustering import validate_bucket
from .projection_snapshot import ClusterCoverage, cluster_projection_signature, witnessed_projection

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from .multi_model_store import MultiModelStore


class ClusterSliceRepairRequired(RuntimeError):
    """Bounded clustering cannot prove a complete update; request explicit full repair."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ClusterSliceInputs:
    basis: str
    signature: str
    models: tuple[str, ...]
    # Each entry is (label, sparse bucket overrides); coarse baselines are in basis.
    generations: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]

    @property
    def buckets(self) -> set[str]:
        return {bucket for _label, overrides in self.generations for bucket, _value in overrides}

    def slice_signature(self, bucket: str, model: str) -> str:
        validate_bucket(bucket)
        if model not in self.models:
            raise ValueError("Cluster slice requires a registered model")
        states = dict(self.generations)
        return _digest([
            self.basis, bucket, model,
            dict(states["content"]).get(bucket, 0),
            dict(states[f"vector:{model}"]).get(bucket, 0),
        ])


def cluster_slice_inputs(store: MultiModelStore, config: IndexConfig) -> ClusterSliceInputs:
    """Caller holds SourceDomain.operation across witness, reads and publication."""
    if store._source_domain is None:
        raise ClusterSliceRepairRequired("Bounded clustering requires a participating source domain")
    snapshot, logical = witnessed_projection(store)
    if logical is None:
        raise ClusterSliceRepairRequired("Logical projection is unproven; explicit full repair required")
    states = bucket_tables(logical)
    names = {"content": store.content_store._table_name, **{
        f"vector:{model}": owner._table_name for model, owner in store._vector_tables.items()
    }}
    if any(name not in states for name in names.values()):
        raise ClusterSliceRepairRequired("Bucket generations are unavailable; explicit full repair required")
    basis = {
        "version": 1, "root": snapshot["root"], "domain": snapshot["domain"],
        "epoch": logical["epoch"],
        "tables": {
            label: {"name": name, "baseline": states[name]["baseline"]}
            for label, name in sorted(names.items())
        },
        "threshold_default": config.cluster_threshold_default,
        "thresholds": config.cluster_thresholds,
        "min_size": config.cluster_min_size,
    }
    return ClusterSliceInputs(
        _digest(basis), cluster_projection_signature(snapshot, config),
        tuple(sorted(store._vector_tables)),
        tuple(
            (label, tuple(sorted(states[name]["buckets"].items())))
            for label, name in sorted(names.items())
        ),
    )


def slice_manifest(
    inputs: ClusterSliceInputs,
    counts: dict[tuple[str, str], tuple[int, int]],
    bucket_items: dict[str, int],
) -> ClusterSliceManifest:
    """Construct complete totals without conflating model memberships with item unions."""
    slices = tuple(
        ClusterSliceCoverage(bucket, model, inputs.slice_signature(bucket, model), items, clusters)
        for (bucket, model), (items, clusters) in sorted(counts.items())
    )
    return ClusterSliceManifest(
        inputs.basis,
        ClusterCoverage(
            inputs.signature, sum(bucket_items.values()),
            sum(entry.items > 0 for entry in slices), sum(entry.clusters for entry in slices),
        ),
        slices, tuple(bucket_items.items()),
    )
