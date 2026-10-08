"""A deterministic, in-process stand-in for the real embedding ``EngineClient``.

Used by tests that need a genuine index -> embed -> store -> search round
trip without the heavy ``torch``/``jinaai`` model or a real engine subprocess
(see ``test_indexer_assignment_workarounds.py``). Embeds via a simple hashed
bag-of-words vector: a query sharing vocabulary with an indexed chunk scores
highly via cosine similarity, which is enough to prove search genuinely works
end to end -- it is not a quality stand-in for the real model.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np


def hashing_embed(text: str, dim: int = 768) -> np.ndarray:
    """Deterministic, unit-norm bag-of-words embedding (no ML model)."""
    vec = np.zeros(dim, dtype=np.float32)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for tok in tokens:
        digest = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
        idx = digest % dim
        sign = 1.0 if (digest // dim) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    else:
        # Degenerate (empty/non-alphanumeric) text still returns a unit-norm
        # vector, matching the real engine's contract.
        vec[0] = 1.0
    return vec


class FakeEmbeddingEngineClient:
    """Matches the ``EngineClient`` interface actually used by the indexing
    and search paths (``embed_texts``, ``embed_query``, ``dimension``,
    ``health``, ``is_ready``) -- a drop-in replacement wherever an
    ``EngineClient`` is injected, with no network call and no model load."""

    def __init__(self, *, dim: int = 768, query_prefix: str = "", model_id: str = "default") -> None:
        self._dim = dim
        self._query_prefix = query_prefix
        self.model_id = model_id

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            raise ValueError("Cannot embed an empty list of texts")
        return np.stack([hashing_embed(t, self._dim) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        prefixed = f"{self._query_prefix}{text}" if self._query_prefix else text
        return hashing_embed(prefixed, self._dim)

    @property
    def dimension(self) -> int:
        return self._dim

    def health(self) -> dict:
        return {
            "status": "ok",
            "generation": "fake-engine-v1",
            "gpu_deps_installed": False,
            "model_loaded": True,
            "model_name": "fake-hashing-embedder",
            "device": "cpu",
            "cuda_available": False,
            "python_executable": None,
            "detail": "deterministic test double, no real model",
        }

    def is_ready(self) -> bool:
        return True
