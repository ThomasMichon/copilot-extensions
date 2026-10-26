"""Conservative physical-version coverage keys, not a new source authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.store.content_store import ContentStore
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.vector_table import VectorTable


class ProjectionSnapshotUnavailable(RuntimeError):
    """The backend cannot prove a reusable physical-version coverage key."""


@dataclass(frozen=True)
class ClusterCoverage:
    signature: str
    items: int
    slices: int
    clusters: int

    def __post_init__(self) -> None:
        if not isinstance(self.signature, str) or len(self.signature) != 64:
            raise ValueError("Cluster coverage has an invalid signature")
        if any(type(value) is not int or value < 0 for value in (
            self.items, self.slices, self.clusters,
        )):
            raise ValueError("Cluster coverage counts must be nonnegative integers")

    def stats(self) -> dict[str, int | float]:
        return {
            "items": self.items, "slices": self.slices, "clusters": self.clusters,
            "elapsed_ms": 0, "maintenance_skipped": 1,
        }


def projection_snapshot(store: MultiModelStore) -> dict[str, object]:
    """Read only configured table metadata, refreshing handles used by readers."""
    if "://" in str(store._db_path):
        raise ProjectionSnapshotUnavailable("Remote projection identity is not qualified")
    root = Path(store._db_path).resolve()
    owners: dict[str, ContentStore | VectorTable] = {"content": store.content_store, **{
        f"vector:{model}": table for model, table in store._vector_tables.items()
    }}
    tables: dict[str, object] = {}
    for label, owner in sorted(owners.items()):
        table = owner._get_or_create_table()
        table.checkout_latest()
        try:
            identity = (root / f"{owner._table_name}.lance").stat()
        except OSError as exc:
            raise ProjectionSnapshotUnavailable("Local projection identity is unavailable") from exc
        if not identity.st_ino:
            raise ProjectionSnapshotUnavailable("Projection storage lacks a stable local file identity")
        tables[label] = {
            "name": owner._table_name, "version": table.version,
            "storage": [identity.st_dev, identity.st_ino, identity.st_ctime_ns],
        }
    domain = store._source_domain
    return {
        "root": str(root), "tables": tables,
        "domain": domain.receipt()["domain"] if domain is not None else None,
    }


def cluster_signature(store: MultiModelStore, config: IndexConfig) -> str:
    """Bind physical input versions and the clustering policy to one artifact."""
    payload = {
        "version": 1,
        "projection": store.projection_snapshot(),
        "threshold_default": config.cluster_threshold_default,
        "thresholds": config.cluster_thresholds,
        "min_size": config.cluster_min_size,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
