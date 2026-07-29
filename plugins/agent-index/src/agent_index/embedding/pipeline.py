"""Embedding pipeline — lazy-loaded GPU inference with jina-v2-base-code.

FP32 only on GTX 1080 (GP104 runs FP16 at 1/64th speed).
Uses sentence-transformers for model loading and inference.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

    from agent_index.index_config import IndexConfig

logger = logging.getLogger(__name__)


class EmbeddingPipeline:
    """GPU-accelerated embedding pipeline with lazy model loading.

    The model is loaded on first use (or via ``warm_up()``) and can be
    freed with ``unload()``.  All vectors are returned as normalized
    float32 arrays suitable for cosine similarity search.
    """

    def __init__(
        self,
        config: IndexConfig | None = None,
        *,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        max_seq_length: int | None = None,
    ) -> None:
        """Build an embedding pipeline.

        By default every parameter comes from ``config`` (the single-model
        path used by the GPU engine subprocess). The keyword overrides let a
        caller pin a specific model on a specific device -- e.g. the in-process
        CPU query embedder loads each ``ModelProfile`` on the CPU regardless of
        ``config.device`` (which targets the GPU engine).
        """
        if config is None:
            from agent_index.index_config import IndexConfig

            config = IndexConfig()

        self._model_name = model_name if model_name is not None else config.model_name
        self._device = device if device is not None else config.device
        self._batch_size = batch_size if batch_size is not None else config.batch_size
        self._max_seq_length = (
            max_seq_length if max_seq_length is not None else config.max_seq_length
        )
        self._model: SentenceTransformer | None = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """True if the model weights are resident in memory."""
        return self._model is not None

    def warm_up(self) -> None:
        """Pre-load model weights so the first embed call is fast."""
        _ = self._get_model()

    def unload(self) -> None:
        """Release model weights and free VRAM."""
        if self._model is not None:
            del self._model
            self._model = None
            # nudge PyTorch to release VRAM
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            logger.info("Model unloaded, VRAM freed")

    # -- embedding -----------------------------------------------------------

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts, returning normalized float32 vectors.

        Args:
            texts: List of text strings to embed.

        Returns:
            Array of shape ``(len(texts), 768)`` with unit-norm rows.

        Raises:
            ValueError: If *texts* is empty.
        """
        if not texts:
            raise ValueError("Cannot embed an empty list of texts")

        model = self._get_model()
        vectors: np.ndarray = model.encode(
            texts,
            batch_size=self._batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return self._validate_output(vectors, len(texts))

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query string.

        Returns:
            1-D array of shape ``(768,)`` with unit norm.
        """
        vectors = self.embed_texts([text])
        return vectors[0]

    @property
    def dimension(self) -> int:
        """Embedding dimensionality (768 for jina-v2-base-code)."""
        return 768

    # -- internals -----------------------------------------------------------

    def _get_model(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "GPU runtime not installed. Run: agent-index bootstrap"
            ) from None

        logger.info(
            "Loading model %s on %s (FP32)",
            self._model_name,
            self._device,
        )
        self._model = SentenceTransformer(
            self._model_name,
            device=self._device,
            trust_remote_code=True,
        )

        # Cap sequence length to stay within VRAM budget on GTX 1080
        if self._max_seq_length and self._model.max_seq_length > self._max_seq_length:
            logger.info(
                "Capping max_seq_length from %d to %d for VRAM safety",
                self._model.max_seq_length,
                self._max_seq_length,
            )
            self._model.max_seq_length = self._max_seq_length

        # Verify expected dimension
        dim = self._model.get_sentence_embedding_dimension()
        if dim != 768:
            logger.warning(
                "Expected 768-dim embeddings, got %d — results may be inconsistent",
                dim,
            )

        logger.info("Model loaded: %s (%d-dim)", self._model_name, dim)
        return self._model

    @staticmethod
    def _validate_output(vectors: np.ndarray, expected_rows: int) -> np.ndarray:
        """Validate embedding output shape and dtype."""
        if vectors.ndim != 2:
            raise RuntimeError(f"Expected 2-D array, got shape {vectors.shape}")
        if vectors.shape[0] != expected_rows:
            raise RuntimeError(
                f"Row count mismatch: expected {expected_rows}, got {vectors.shape[0]}"
            )
        return vectors.astype(np.float32, copy=False)
