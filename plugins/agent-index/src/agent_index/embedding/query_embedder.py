"""In-process CPU query embedder.

The search/read path embeds queries *in-process on the CPU* instead of calling
the GPU engine subprocess over HTTP. This keeps search responsive and fully
decoupled from the GPU: a query is one short forward pass (sub-second on CPU),
and search never blocks on a cold or idled-out engine, never returns a
``spinning_up`` placeholder, and works even while the GPU is busy indexing or
spun down entirely.

The GPU embedding engine is thereby reserved for *indexing* (bulk-embedding
thousands of chunks, where batch throughput matters), which already spins the
engine up per run and tears it down afterwards to free VRAM.

``InProcessQueryEmbedder`` mirrors the read+lifecycle surface of
``agent_index.engine.client.EngineClient`` so it is a drop-in replacement in the search
path (``embed_query`` / ``embed_texts`` / ``dimension`` plus the
``is_ready`` / ``spinup`` / ``spindown`` / ``health`` / ``close`` lifecycle the
server endpoints poke at). Lifecycle calls operate on the local CPU model rather
than a remote subprocess.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_index.embedding.pipeline import EmbeddingPipeline

if TYPE_CHECKING:
    import numpy as np

    from agent_index.index_config import ModelProfile, IndexConfig

logger = logging.getLogger(__name__)


class InProcessQueryEmbedder:
    """CPU, in-process query embedder for a single model profile.

    Drop-in for ``EngineClient`` on the search path. Applies the profile's
    ``query_prefix`` (e.g. BGE's retrieval prompt) before embedding, exactly as
    ``EngineClient.embed_query`` does, so query vectors match the indexed space.
    """

    def __init__(
        self,
        profile: ModelProfile,
        *,
        device: str = "cpu",
        config: IndexConfig | None = None,
    ) -> None:
        self.model_id = profile.model_id
        self._query_prefix = profile.query_prefix
        self._dim = profile.dim
        self._device = device
        # Descriptive pseudo-URL so status/diagnostics can tell at a glance that
        # this model embeds in-process rather than via an engine subprocess.
        self._base_url = f"in-process://{device}/{profile.model_id}"
        self._pipeline = EmbeddingPipeline(
            config,
            model_name=profile.model_name,
            device=device,
            batch_size=profile.batch_size,
            max_seq_length=profile.max_seq_length,
        )

    # -- embedding interface -------------------------------------------------

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query string (with the model's query prefix)."""
        prefixed = f"{self._query_prefix}{text}" if self._query_prefix else text
        return self._pipeline.embed_query(prefixed)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts (no query prefix; matches indexing semantics)."""
        return self._pipeline.embed_texts(texts)

    @property
    def dimension(self) -> int:
        """Embedding dimensionality."""
        return self._dim

    @dimension.setter
    def dimension(self, value: int) -> None:
        self._dim = value

    # -- lifecycle -----------------------------------------------------------

    def warm_up(self) -> None:
        """Pre-load the CPU model so the first query is fast."""
        self._pipeline.warm_up()

    def is_ready(self) -> bool:
        """True once the CPU model is resident.

        The server warms in-process embedders at startup, so this is true before
        any query arrives and the cold-engine ``spinning_up`` path never fires.
        """
        return self._pipeline.is_loaded

    def spinup(self) -> dict[str, object]:
        """Load the model into (CPU) memory."""
        self.warm_up()
        return {"status": "ready", "model_loaded": True}

    def spindown(self) -> dict[str, object]:
        """Release the model. (Rarely useful on CPU; provided for parity.)"""
        self._pipeline.unload()
        return {"status": "unloaded", "model_loaded": False}

    def health(self) -> dict[str, object]:
        """Health snapshot shaped like ``EngineClient.health()``."""
        return {
            "status": "ok",
            "gpu_deps_installed": True,
            "model_loaded": self._pipeline.is_loaded,
            "model_name": self._pipeline._model_name,
            "device": self._device,
            "cuda_available": False,
            "detail": None,
        }

    def close(self) -> None:
        """Release the model on shutdown."""
        self._pipeline.unload()
