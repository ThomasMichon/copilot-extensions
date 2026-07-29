"""Similarity clustering — group item centroids into near-duplicate clusters.

A clustering run operates on a single ``(bucket, model_id)`` slice: items that
share a source bucket (e.g. all ``forge:issues`` across repos) and live in the
same embedding space.  Items become nodes; an edge joins two items whose
centroid cosine meets the slice's threshold *or* whose content is byte-identical
(same exact-dup key).  Connected components of size >= ``min_size`` are clusters.

This module is pure and store-free — it takes centroids and returns ``Cluster``
objects — so the algorithm is unit-testable without LanceDB or SQLite.  The
data-access sweep that feeds it lives in ``agent_index.indexing.cluster_pass``.

Cost note: edges are found from the per-slice cosine matrix (``M @ M.T``), which
is O(n^2) in the slice size.  An *item* count is far smaller than the chunk
count agent-index is sized for (~25K chunks), and clustering is an offline post-index
pass, so this is tractable today; swapping edge discovery for per-item ANN
top-k is the future optimization if a single bucket ever grows large.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ClusterEntry:
    """One item's input to clustering, within a single embedding space."""

    source: str
    file_path: str
    centroid: np.ndarray  # L2-normalized item centroid for this model
    content_hash: str  # item-level exact-dup key


@dataclass(frozen=True)
class ClusterMember:
    """A clustered item plus its relation to the cluster representative."""

    source: str
    file_path: str
    score: float  # cosine to the representative (the rep itself is 1.0)
    is_exact_dupe: bool  # shares its content hash with another member


@dataclass(frozen=True)
class Cluster:
    """A group of similar items within one ``(bucket, model_id)`` slice."""

    bucket: str
    model_id: str
    members: tuple[ClusterMember, ...]  # representative first, then by score
    has_exact_dupes: bool

    @property
    def representative(self) -> ClusterMember:
        return self.members[0]

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def avg_score(self) -> float:
        """Mean member-to-representative cosine (excluding the rep itself)."""
        if len(self.members) <= 1:
            return 1.0
        return float(
            sum(m.score for m in self.members[1:]) / (len(self.members) - 1)
        )


def source_bucket(source: str) -> str:
    """Collapse a source string to its clustering bucket.

    Takes the first two colon segments, so:
      ``forge:issues:owner/repo`` -> ``forge:issues`` (issues cluster across
      repos), ``service-feed:clips`` -> ``service-feed:clips``,
      ``analysis-feed:videos`` -> ``analysis-feed:videos``.
    """
    if not source:
        return source
    return ":".join(source.split(":")[:2])


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def build_clusters(
    entries: list[ClusterEntry],
    *,
    bucket: str,
    model_id: str,
    threshold: float,
    min_size: int = 2,
) -> list[Cluster]:
    """Cluster one ``(bucket, model_id)`` slice into near-duplicate groups.

    Args:
        entries: Items in this slice (all centroids same dim, L2-normalized).
        bucket / model_id: Slice identity, stamped onto each ``Cluster``.
        threshold: Minimum centroid cosine for a similarity edge.
        min_size: Smallest component reported as a cluster (default 2).

    Returns:
        Clusters sorted largest/tightest first; the representative (cluster
        medoid) is always ``members[0]``.
    """
    n = len(entries)
    if n < min_size:
        return []

    matrix = np.vstack([e.centroid for e in entries]).astype(np.float32)
    sims = matrix @ matrix.T  # cosine, since centroids are normalized
    # Clamp float rounding so a self/identical pair reads exactly 1.0.
    sims = np.clip(sims, -1.0, 1.0)

    uf = _UnionFind(n)

    # Similarity edges (vectorized upper triangle).
    iu, ju = np.triu_indices(n, k=1)
    if iu.size:
        edge_mask = sims[iu, ju] >= threshold
        for a, b in zip(iu[edge_mask], ju[edge_mask], strict=True):
            uf.union(int(a), int(b))

    # Exact-duplicate edges: identical content always lands in one cluster,
    # even if a (mis)tuned threshold would otherwise separate it.
    hashes = [e.content_hash for e in entries]
    by_hash: dict[str, list[int]] = defaultdict(list)
    for i, h in enumerate(hashes):
        by_hash[h].append(i)
    for idxs in by_hash.values():
        for k in range(1, len(idxs)):
            uf.union(idxs[0], idxs[k])

    components: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        components[uf.find(i)].append(i)

    clusters: list[Cluster] = []
    for member_idxs in components.values():
        if len(member_idxs) < min_size:
            continue

        sub = sims[np.ix_(member_idxs, member_idxs)]
        rep_local = int(np.argmax(sub.sum(axis=1)))  # medoid: most central
        rep_global = member_idxs[rep_local]

        hash_counts = Counter(hashes[i] for i in member_idxs)
        members = [
            ClusterMember(
                source=entries[gi].source,
                file_path=entries[gi].file_path,
                score=float(sims[gi, rep_global]),
                is_exact_dupe=hash_counts[hashes[gi]] > 1,
            )
            for gi in member_idxs
        ]
        # Representative first, then by descending score.
        members.sort(
            key=lambda m: (
                m.source == entries[rep_global].source
                and m.file_path == entries[rep_global].file_path,
                m.score,
            ),
            reverse=True,
        )
        has_exact = any(c > 1 for c in hash_counts.values())
        clusters.append(
            Cluster(
                bucket=bucket,
                model_id=model_id,
                members=tuple(members),
                has_exact_dupes=has_exact,
            )
        )

    clusters.sort(key=lambda c: (c.size, c.avg_score), reverse=True)
    return clusters
