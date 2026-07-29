"""Item-level representations — pooling chunk vectors into a content item.

A "content item" is a ``(source, file_path)`` pair: one issue, one quip, one
doc, one source file.  Its chunks may live in more than one embedding-model
table (e.g. a Markdown doc with fenced code → some prose chunks, some code
chunks).  This module turns those per-chunk vectors into **per-model item
centroids** plus an **exact-duplicate key**, the inputs the clustering pass
(Phase 3) compares item-to-item.

Pure, store-free math lives here so it is unit-testable without LanceDB; the
data-access orchestration lives on ``MultiModelStore``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ItemRepresentation:
    """A content item's pooled embedding(s) and exact-dup key.

    Attributes:
        source: The item's source (e.g. ``forge:issues:owner/repo``).
        file_path: The item's path within that source.
        centroids: ``{model_id: centroid}`` — one L2-normalized centroid per
            embedding space the item has chunks in.  An item whose chunks span
            both code and prose tables carries two centroids (see ``is_mixed``);
            never compare across keys.
        content_hash: Item-level exact-duplicate key — a deterministic hash over
            the item's chunk content hashes in line order.  Two items with the
            same value are byte-identical content (the cosine layer catches the
            near-duplicates this misses).
        chunk_count: Number of chunks that compose the item.
    """

    source: str
    file_path: str
    centroids: dict[str, np.ndarray]
    content_hash: str
    chunk_count: int

    @property
    def model_ids(self) -> list[str]:
        """Embedding spaces this item has a centroid in (sorted)."""
        return sorted(self.centroids)

    @property
    def is_mixed(self) -> bool:
        """True if the item spans more than one embedding space."""
        return len(self.centroids) > 1


def pool_vectors(
    vectors: np.ndarray,
    weights: np.ndarray | None = None,
    *,
    normalize: bool = True,
) -> np.ndarray:
    """Length-weighted mean-pool of chunk vectors into one item centroid.

    Args:
        vectors: ``(n, dim)`` float array of chunk embeddings (n >= 1).
        weights: Optional ``(n,)`` non-negative weights (e.g. chunk line spans),
            so longer chunks contribute more.  ``None`` → uniform.  All-zero or
            negative weights fall back to uniform.
        normalize: L2-normalize the centroid so it is cosine-comparable to
            other normalized centroids (the operating metric).

    Returns:
        A ``(dim,)`` float32 centroid.

    Raises:
        ValueError: If *vectors* is not a non-empty 2-D array, or *weights*
            (when given) does not have shape ``(n,)``.
    """
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0:
        raise ValueError("pool_vectors requires a non-empty (n, dim) array")

    if weights is None:
        w = np.ones(arr.shape[0], dtype=np.float32)
    else:
        w = np.asarray(weights, dtype=np.float32)
        if w.shape != (arr.shape[0],):
            raise ValueError(
                f"weights shape {w.shape} != ({arr.shape[0]},)"
            )
        w = np.clip(w, 0.0, None)
        if not np.any(w > 0):
            w = np.ones(arr.shape[0], dtype=np.float32)

    centroid = (arr * w[:, None]).sum(axis=0) / w.sum()
    out: np.ndarray = centroid.astype(np.float32)

    if normalize:
        norm = float(np.linalg.norm(out))
        if norm > 0:
            out = out / norm

    return out


def item_content_hash(content_hashes: list[str]) -> str:
    """Deterministic item-level exact-dup key over line-ordered chunk hashes.

    Order-sensitive on purpose: two items are exact duplicates only if they
    carry the same chunk content in the same order.  Because each chunk's
    ``content_hash`` is over normalized content alone (not its source/path),
    identical content under different path prefixes hashes the same — exactly
    the cross-source duplicate case naive path matching misses.
    """
    h = hashlib.sha256()
    for ch in content_hashes:
        h.update((ch or "").encode())
        h.update(b"\n")
    return h.hexdigest()
