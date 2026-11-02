"""Bounded artifact receipts; source generations remain owned by SourceDomain."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass

from .projection_snapshot import ClusterCoverage

MAX_CLUSTER_SLICES = 256
MAX_CLUSTER_COVERAGE_BYTES = 65536


def _signature(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _count(value: int) -> bool:
    return type(value) is int and 0 <= value < 2**63


@dataclass(frozen=True)
class ClusterSliceCoverage:
    bucket: str
    model_id: str
    signature: str
    items: int
    clusters: int

    def __post_init__(self) -> None:
        if (not isinstance(self.bucket, str) or not self.bucket
                or not isinstance(self.model_id, str) or not self.model_id
                or not _signature(self.signature)
                or not _count(self.items) or not _count(self.clusters)
                or self.clusters > self.items):
            raise ValueError("Invalid cluster slice coverage")

    @property
    def key(self) -> tuple[str, str]:
        return self.bucket, self.model_id


@dataclass(frozen=True)
class ClusterSliceManifest:
    """Complete bounded roster, including covered slices with no clusters.

    ``basis`` binds policy, domain and coarse table identity. Per-slice
    signatures bind witnessed input generations. Neither is source authority.
    ``bucket_items`` counts the union of eligible items across models, not the
    sum of model memberships.
    """

    basis: str
    coverage: ClusterCoverage
    slices: tuple[ClusterSliceCoverage, ...]
    bucket_items: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if (not _signature(self.basis) or not isinstance(self.coverage, ClusterCoverage)
                or not _signature(self.coverage.signature)
                or any(not _count(value) for value in (
                    self.coverage.items, self.coverage.slices, self.coverage.clusters,
                ))
                or type(self.slices) is not tuple
                or len(self.slices) > MAX_CLUSTER_SLICES
                or any(not isinstance(entry, ClusterSliceCoverage) for entry in self.slices)
                or len({entry.key for entry in self.slices}) != len(self.slices)
                or type(self.bucket_items) is not tuple
                or len(self.bucket_items) > MAX_CLUSTER_SLICES):
            raise ValueError("Invalid cluster slice manifest")
        for pair in self.bucket_items:
            if (type(pair) is not tuple or len(pair) != 2
                    or not isinstance(pair[0], str) or not pair[0] or not _count(pair[1])):
                raise ValueError("Invalid cluster bucket item count")
        items = dict(self.bucket_items)
        if (len(items) != len(self.bucket_items)
                or set(items) != {entry.bucket for entry in self.slices}
                or self.coverage.items != sum(items.values())
                or self.coverage.slices != sum(entry.items > 0 for entry in self.slices)
                or self.coverage.clusters != sum(entry.clusters for entry in self.slices)):
            raise ValueError("Cluster manifest totals do not match its slices")
        for bucket, count in items.items():
            counts = [entry.items for entry in self.slices if entry.bucket == bucket]
            if not max(counts) <= count <= sum(counts):
                raise ValueError("Cluster bucket count is not an item union")
        object.__setattr__(self, "slices", tuple(sorted(self.slices, key=lambda entry: entry.key)))
        object.__setattr__(self, "bucket_items", tuple(sorted(self.bucket_items)))
        self.encode()

    def encode(self) -> str:
        value = {
            "version": 1,
            "basis": self.basis,
            "coverage": asdict(self.coverage),
            "slices": [asdict(entry) for entry in self.slices],
            "bucket_items": self.bucket_items,
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(raw.encode("utf-8")) > MAX_CLUSTER_COVERAGE_BYTES:
            raise ValueError("Cluster slice coverage exceeds its byte budget")
        return raw

    @classmethod
    def decode(cls, raw: str) -> ClusterSliceManifest:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CLUSTER_COVERAGE_BYTES:
            raise ValueError("Invalid cluster slice coverage size")
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict)
                    or set(value) != {"version", "basis", "coverage", "slices", "bucket_items"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or not isinstance(value["slices"], list)
                    or len(value["slices"]) > MAX_CLUSTER_SLICES
                    or not isinstance(value["bucket_items"], list)
                    or len(value["bucket_items"]) > MAX_CLUSTER_SLICES):
                raise ValueError("Invalid cluster slice coverage schema")
            return cls(
                basis=value["basis"], coverage=ClusterCoverage(**value["coverage"]),
                slices=tuple(ClusterSliceCoverage(**entry) for entry in value["slices"]),
                bucket_items=tuple(tuple(pair) for pair in value["bucket_items"]),
            )
        except (TypeError, KeyError, RecursionError) as exc:
            raise ValueError("Invalid cluster slice coverage schema") from exc

    def validate_replacement(
        self, previous: ClusterSliceManifest, changed: tuple[tuple[str, str], ...],
    ) -> None:
        if (not changed or len(changed) > MAX_CLUSTER_SLICES
                or any(type(key) is not tuple or len(key) != 2
                       or any(not isinstance(part, str) or not part for part in key)
                       for key in changed)
                or len(set(changed)) != len(changed)):
            raise ValueError("Invalid cluster replacement slice selection")
        before = {entry.key: entry for entry in previous.slices}
        after = {entry.key: entry for entry in self.slices}
        selected = set(changed)
        if (self.basis != previous.basis or not selected <= before.keys() | after.keys()
                or {key: entry for key, entry in before.items() if key not in selected}
                != {key: entry for key, entry in after.items() if key not in selected}):
            raise ValueError("Cluster replacement changes unselected coverage or its basis")
        buckets = {bucket for bucket, _model in changed}
        if ({key: count for key, count in previous.bucket_items if key not in buckets}
                != {key: count for key, count in self.bucket_items if key not in buckets}):
            raise ValueError("Cluster replacement changes unselected bucket counts")

    def validate_clusters(
        self, keys: list[tuple[str, str]], *, selected: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        counts = Counter(keys)
        allowed = {entry.key for entry in self.slices} if selected is None else set(selected)
        expected = {
            entry.key: entry.clusters for entry in self.slices
            if entry.key in allowed and entry.clusters
        }
        if not counts.keys() <= allowed or dict(counts) != expected:
            raise ValueError("Cluster slice coverage does not describe the replacement artifact")
