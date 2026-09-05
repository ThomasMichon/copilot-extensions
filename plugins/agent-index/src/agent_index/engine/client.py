"""Engine client — httpx wrapper for communicating with the agent-index engine.

Provides the same interface shape as EmbeddingPipeline so the main service
and indexing engine can use it as a drop-in replacement.
"""

from __future__ import annotations

import logging
import os

import httpx
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_ENGINE_URL = "http://127.0.0.1:8421"

# Generous timeout for embedding — model loading can take 20+ seconds on
# first call, and a full batch takes time on a slow/CPU embed path. The read
# timeout is configurable (#115): a large CPU batch legitimately exceeds a short
# timeout, and aborting mid-embed silently empties the index. A longer read
# timeout never slows a fast GPU embed -- it only avoids a premature abort -- so
# the generous default is universal; override with AGENT_INDEX_EMBED_READ_TIMEOUT.
_READ_TIMEOUT_S = float(os.environ.get("AGENT_INDEX_EMBED_READ_TIMEOUT", "300"))
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=_READ_TIMEOUT_S, write=10.0, pool=5.0)

# Transient errors worth one retry: a socket-activated engine that idle-exited
# leaves a stale pooled keep-alive connection, so the first request on it fails
# with a protocol/read/write error. The retry opens a fresh connection, which
# re-triggers systemd socket activation and reaches the freshly started engine.
_RETRYABLE_POST_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)


class EngineClient:
    """HTTP client for the agent-index engine subprocess.

    Matches the EmbeddingPipeline interface for embed operations, plus
    lifecycle control (spinup/spindown/health).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        *,
        query_prefix: str = "",
        model_id: str = "default",
    ) -> None:
        self._base_url = base_url or os.environ.get(
            "AGENT_INDEX_ENGINE_URL", DEFAULT_ENGINE_URL
        )
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._query_prefix = query_prefix
        self.model_id = model_id
        self._dim = 768
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _post_with_retry(self, path: str, payload: dict) -> httpx.Response:
        """POST to the engine with one retry on a stale-keepalive failure.

        Lets ``httpx.ConnectError`` propagate (genuinely unreachable -> the
        caller maps it to ``EngineUnavailableError``); retries once on a
        protocol/read error, which is the signature of a pooled connection
        whose engine idle-exited under socket activation.
        """
        try:
            return self._client.post(path, json=payload)
        except _RETRYABLE_POST_ERRORS as e:
            logger.debug(
                "engine %s post failed (%s); retrying on a fresh connection",
                path, type(e).__name__,
            )
            return self._client.post(path, json=payload)

    # ── Embedding interface ──────────────────────────────────────────

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts, returning float32 vectors.

        Returns:
            Array of shape (len(texts), dimension) with unit-norm rows.

        Raises:
            EngineUnavailableError: If the engine is not reachable.
            EngineError: If the engine returns an error.
        """
        if not texts:
            raise ValueError("Cannot embed an empty list of texts")

        try:
            resp = self._post_with_retry("/embed/batch", {"texts": texts})
        except httpx.TransportError as e:
            # Covers connect/read/write/timeout/protocol errors after the one
            # in-flight retry -- map to EngineUnavailable so the search read
            # path degrades intentionally instead of surfacing a raw 500.
            raise EngineUnavailableError(
                f"agent-index engine not reachable at {self._base_url}: {e!r}"
            ) from e

        if resp.status_code == 503:
            raise EngineUnavailableError(
                "agent-index engine: GPU runtime not installed. Run: agent-index bootstrap"
            )
        resp.raise_for_status()

        data = resp.json()
        vectors = np.array(data["vectors"], dtype=np.float32)
        return vectors

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query string.

        Applies ``query_prefix`` (e.g. BGE's retrieval prompt) before
        sending to the engine.

        Returns:
            1-D array of shape (dimension,) with unit norm.
        """
        prefixed = f"{self._query_prefix}{text}" if self._query_prefix else text
        try:
            resp = self._post_with_retry("/embed", {"text": prefixed})
        except httpx.TransportError as e:
            raise EngineUnavailableError(
                f"agent-index engine not reachable at {self._base_url}: {e!r}"
            ) from e

        if resp.status_code == 503:
            raise EngineUnavailableError(
                "agent-index engine: GPU runtime not installed. Run: agent-index bootstrap"
            )
        resp.raise_for_status()

        data = resp.json()
        return np.array(data["vector"], dtype=np.float32)

    @property
    def dimension(self) -> int:
        """Embedding dimensionality (default 768)."""
        return self._dim

    @dimension.setter
    def dimension(self, value: int) -> None:
        self._dim = value

    # ── Lifecycle ────────────────────────────────────────────────────

    def health(self) -> dict:
        """Get engine health status."""
        try:
            resp = self._client.get("/health")
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {
                "status": "unreachable",
                "generation": None,
                "gpu_deps_installed": False,
                "model_loaded": False,
                "model_name": None,
                "device": None,
                "cuda_available": None,
                "python_executable": None,
                "detail": f"Engine not reachable at {self._base_url}",
            }

    def is_ready(self) -> bool:
        """Check if the engine model is loaded and ready."""
        try:
            resp = self._client.get("/ready")
            resp.raise_for_status()
            return resp.json().get("ready", False)
        except httpx.ConnectError:
            return False

    def spinup(self) -> dict:
        """Pre-load model into GPU memory."""
        resp = self._client.post("/spinup")
        resp.raise_for_status()
        return resp.json()

    def spindown(self) -> dict:
        """Unload model and free VRAM."""
        resp = self._client.post("/spindown")
        resp.raise_for_status()
        return resp.json()


class EngineUnavailableError(RuntimeError):
    """Raised when the agent-index engine is not reachable or not ready."""


class EngineError(RuntimeError):
    """Raised when the agent-index engine returns an error."""
