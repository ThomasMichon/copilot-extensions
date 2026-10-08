"""agent-index Engine -- GPU embedding subprocess.

Standalone FastAPI application that owns the embedding model and GPU memory.
Runs as a separate process from the main agent-index service, communicating via
localhost HTTP.

Endpoints:
    GET  /health     -- Process and dependency status (never loads model)
    GET  /ready      -- True only when model is loaded and usable
    POST /embed      -- Embed a single text string
    POST /embed/batch -- Embed a list of texts (returns vectors as nested lists)
    POST /spinup     -- Pre-load model into GPU memory
    POST /spindown   -- Unload model and free VRAM
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent_index.engine.generation import current_engine_generation

logger = logging.getLogger(__name__)

# ── GPU lock — serializes model lifecycle and embedding calls ──────────

_gpu_lock = threading.Lock()


# ── Request/Response models ───────────────────────────────────────────

class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    vector: list[float]
    dimension: int


class BatchEmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=500)


class BatchEmbedResponse(BaseModel):
    vectors: list[list[float]]
    dimension: int
    count: int


class HealthResponse(BaseModel):
    status: str
    generation: str
    gpu_deps_installed: bool
    model_loaded: bool
    model_name: str | None
    device: str | None
    cuda_available: bool | None
    python_executable: str
    detail: str | None = None


class ReadyResponse(BaseModel):
    ready: bool


class SpinResponse(BaseModel):
    status: str
    model_loaded: bool


# ── Engine state ──────────────────────────────────────────────────────

_pipeline: Any = None  # EmbeddingPipeline | None -- avoid import at module level
_config: Any = None




def _check_gpu_deps() -> bool:
    """Check if GPU dependencies (torch, sentence-transformers) are installed."""
    try:
        import sentence_transformers  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _get_pipeline():
    """Get or lazily create the EmbeddingPipeline, ensuring its model is
    loaded before returning.

    On Windows, model loading happens here at whatever priority this
    process already inherited (normal, unless an operator explicitly
    started it niced) -- the host good-citizen throttle is applied only
    AFTER a successful first load (``_ensure_priority_lowered_once``,
    Windows-only path), so the one-time load itself is never throttled,
    only the ongoing embedding work that follows it. On POSIX the throttle
    is instead applied eagerly at process startup (see ``main()``) because
    ``os.nice()`` only affects the calling thread, inherited by threads
    created *afterward* -- applying it here, inside a request-handling
    thread, would miss every other worker/inference thread already created
    during startup.
    """
    global _pipeline, _config
    if _pipeline is not None and _pipeline.is_loaded:
        return _pipeline

    if not _check_gpu_deps():
        raise HTTPException(
            status_code=503,
            detail="GPU runtime not installed. Run: agent-index bootstrap",
        )

    from agent_index.index_config import IndexConfig
    from agent_index_engine.pipeline import EmbeddingPipeline

    if _config is None:
        _config = IndexConfig()
    if _pipeline is None:
        _pipeline = EmbeddingPipeline(_config)

    _pipeline.warm_up()
    if sys.platform.startswith("win"):
        _ensure_priority_lowered_once()
    return _pipeline


# ── Lifespan ──────────────────────────────────────────────────────────

# Idle auto-stop. When AGENT_INDEX_ENGINE_IDLE_TIMEOUT > 0, the engine process exits
# cleanly after that many seconds without an embed/spinup request, so a
# socket-activated unit sheds its ~1 GB Python/torch/CUDA process baseline (the
# part /spindown can't reclaim). systemd re-activates it on the next connection.
# Default 0 = disabled, preserving the legacy always-on launch path unchanged.
# Activity is driven by embed/spinup only -- liveness probes (/health, /ready)
# deliberately do NOT count, so polling can't pin the engine awake. An in-flight
# counter guards against exiting while a request (e.g. a long batch embed) is
# still running, even if it has outlived the idle window.
_activity_lock = threading.Lock()
_last_activity: float = time.monotonic()
_inflight: int = 0
_idle_task: asyncio.Task[None] | None = None


def _activity_begin() -> None:
    """Mark the start of an embed/spinup request (keeps the engine alive)."""
    global _inflight
    with _activity_lock:
        _inflight += 1


def _activity_end() -> None:
    """Mark request completion and reset the idle timer from completion time."""
    global _inflight, _last_activity
    with _activity_lock:
        _inflight = max(0, _inflight - 1)
        _last_activity = time.monotonic()


def _is_idle(timeout: float) -> bool:
    """True only when nothing is in flight and the idle window has elapsed."""
    with _activity_lock:
        return _inflight == 0 and (time.monotonic() - _last_activity) >= timeout


def _idle_timeout_seconds() -> float:
    try:
        return float(os.environ.get("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "0"))
    except ValueError:
        return 0.0


async def _idle_monitor(timeout: float) -> None:
    """Exit the process cleanly once idle (no in-flight + no recent activity)."""
    poll = min(timeout, 30.0)
    while True:
        await asyncio.sleep(poll)
        if _is_idle(timeout):
            logger.info(
                "Engine idle for >= %.0fs with no in-flight requests -- "
                "shutting down to free memory", timeout,
            )
            # Graceful SIGTERM: uvicorn runs lifespan shutdown (unloads the
            # model) and exits 0, so Restart=on-failure does not restart it.
            signal.raise_signal(signal.SIGTERM)
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Engine lifespan -- load config and clean up on shutdown."""
    global _config, _idle_task
    from agent_index.index_config import IndexConfig
    _config = IndexConfig()


    logger.info("agent-index engine starting (model will load on first embed)")

    idle_timeout = _idle_timeout_seconds()
    if idle_timeout > 0:
        _idle_task = asyncio.create_task(_idle_monitor(idle_timeout))
        logger.info("Idle auto-stop enabled: %.0fs", idle_timeout)

    yield

    # Stop the idle monitor before teardown
    if _idle_task is not None:
        _idle_task.cancel()
        _idle_task = None

    # Unload model on shutdown
    if _pipeline is not None:
        with _gpu_lock:
            _pipeline.unload()
    logger.info("agent-index engine shut down")


# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="agent-index Engine",
    description="GPU embedding engine for agent-index",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Process and dependency status. Never loads the model."""
    gpu_ok = _check_gpu_deps()

    cuda_available = None
    if gpu_ok:
        try:
            import torch
            cuda_available = torch.cuda.is_available()
        except Exception:
            cuda_available = False

    model_loaded = _pipeline is not None and _pipeline.is_loaded

    detail = None
    if not gpu_ok:
        detail = "GPU runtime not installed. Run: agent-index bootstrap"
    elif cuda_available is False:
        detail = "torch installed but CUDA not available"

    return HealthResponse(
        status="ok" if gpu_ok else "degraded",
        generation=current_engine_generation(),
        gpu_deps_installed=gpu_ok,
        model_loaded=model_loaded,
        model_name=_config.model_name if _config else None,
        device=_config.device if _config else None,
        cuda_available=cuda_available,
        python_executable=sys.executable,
        detail=detail,
    )


@app.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
    """True only when model is loaded and ready for embedding."""
    is_ready = _pipeline is not None and _pipeline.is_loaded
    return ReadyResponse(ready=is_ready)


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    """Embed a single text string. Auto-loads model on first call."""
    _activity_begin()
    try:
        with _gpu_lock:
            pipeline = _get_pipeline()
            vector = pipeline.embed_query(req.text)
        return EmbedResponse(
            vector=vector.tolist(),
            dimension=len(vector),
        )
    finally:
        _activity_end()


@app.post("/embed/batch", response_model=BatchEmbedResponse)
def embed_batch(req: BatchEmbedRequest) -> BatchEmbedResponse:
    """Embed a batch of texts. Auto-loads model on first call."""
    _activity_begin()
    try:
        with _gpu_lock:
            pipeline = _get_pipeline()
            vectors = pipeline.embed_texts(req.texts)
        return BatchEmbedResponse(
            vectors=vectors.tolist(),
            dimension=vectors.shape[1],
            count=vectors.shape[0],
        )
    finally:
        _activity_end()


@app.post("/spinup", response_model=SpinResponse)
async def spinup() -> SpinResponse:
    """Pre-load model into GPU memory."""
    _activity_begin()
    try:
        with _gpu_lock:
            pipeline = _get_pipeline()
            pipeline.warm_up()

        return SpinResponse(status="ok", model_loaded=True)
    finally:
        _activity_end()


@app.post("/spindown", response_model=SpinResponse)
async def spindown() -> SpinResponse:
    """Unload model and free VRAM."""
    global _pipeline
    with _gpu_lock:
        if _pipeline is not None:
            _pipeline.unload()
            _pipeline = None
        _reset_priority_for_reload()

    return SpinResponse(status="ok", model_loaded=False)


# ── Server runner ─────────────────────────────────────────────────────

# systemd passes activation sockets starting at this fd (SD_LISTEN_FDS_START).
_SD_LISTEN_FDS_START = 3


def _systemd_socket_fd() -> int | None:
    """Return the inherited systemd activation socket fd, or None.

    Implements the sd_listen_fds(3) contract: a socket-activated unit receives
    ``LISTEN_PID`` (which must match this process) and ``LISTEN_FDS`` (the count
    of passed fds, starting at fd 3). Returns the first fd when exactly one
    socket was passed, else None so the caller falls back to host/port binding.
    """
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        n_fds = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    if n_fds < 1:
        return None
    if n_fds > 1:
        logger.warning(
            "Socket activation passed %d fds; expected 1 -- using the first", n_fds,
        )
    return _SD_LISTEN_FDS_START


def run_engine(*, host: str = "127.0.0.1", port: int = 8421) -> None:
    """Start the agent-index engine server with uvicorn.

    When launched under systemd socket activation (``LISTEN_FDS`` set), bind the
    inherited listening socket instead of ``host``/``port`` -- this lets a
    non-root caller trigger an on-demand start by connecting to the systemd-held
    socket, with no privilege to ``systemctl start`` the unit itself. Falls back
    to ordinary host/port binding when not socket-activated, so the legacy
    always-on launch path is unchanged.
    """
    import uvicorn

    fd = _systemd_socket_fd()
    if fd is not None:
        print(f"Starting agent-index engine on inherited systemd socket (fd={fd})")
        uvicorn.run(app, fd=fd, log_level="info")
        return

    print(f"Starting agent-index engine on {host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


def _build_parser():
    """Build the engine worker command-line parser."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agent_index_engine.app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8421)
    return parser


def _lower_own_priority() -> None:
    """Host good-citizen throttle: lower this process's scheduling priority.

    On a CPU-only device this engine is the actual CPU-bound consumer during
    a large reindex (continuous model inference) -- distinct from the index
    worker's own ``indexer_nice`` throttle, which does not touch this
    process.

    Never call this directly outside ``_ensure_priority_lowered_once`` /
    ``main()``'s POSIX startup path -- see their own docstrings for *when*
    each platform applies it and why the timing differs.

    Best-effort and never raises; see ``IndexConfig.engine_nice`` for the
    env var and default.
    """
    try:
        from agent_index.index_config import IndexConfig
        from agent_index.indexing.priority import lower_current_process_priority

        lower_current_process_priority(IndexConfig().engine_nice)
    except Exception:  # pragma: no cover - defensive; must never block startup
        logger.debug("could not apply engine host-politeness throttle", exc_info=True)


# Tracks whether the throttle has been applied this process lifetime. Kept
# separate from `_pipeline`/`is_loaded` (which `/spindown` resets) so a later
# reload never re-lowers (POSIX's os.nice() is a RELATIVE increment that
# would otherwise compound on every spinup/spindown/spinup cycle: 5, 10, 15,
# 19, ...) -- see `_reset_priority_for_reload`.
_priority_lowered = False
# Windows-only: the priority class captured just before the throttle first
# lowers it, so a later restore can return to that EXACT baseline (an engine
# may have deliberately been started at some other non-normal class) rather
# than assuming NORMAL_PRIORITY_CLASS. ``None`` until captured, and whenever
# the throttle is disabled (``engine_nice <= 0``) -- in which case nothing
# was ever changed, so there is nothing to restore either.
_priority_baseline: int | None = None


def _ensure_priority_lowered_once() -> None:
    """Apply the host-politeness throttle at most once per process lifetime.

    Windows-only call site (see ``_get_pipeline``): deferred until after a
    successful model load so the one-time, latency-sensitive load itself
    runs at normal priority (an eagerly-applied throttle was observed to
    stall it indefinitely at Windows IDLE_PRIORITY_CLASS under concurrent
    host load). SetPriorityClass is process-wide regardless of which thread
    calls it or when, so deferring costs nothing on Windows.
    """
    global _priority_lowered, _priority_baseline
    if _priority_lowered:
        return
    _priority_lowered = True
    if sys.platform.startswith("win"):
        from agent_index.index_config import IndexConfig

        if IndexConfig().engine_nice > 0:
            from agent_index.indexing.priority import get_current_priority_class

            _priority_baseline = get_current_priority_class()
    _lower_own_priority()


def _reset_priority_for_reload() -> None:
    """Called from ``/spindown`` so a LATER reload (``/spinup`` or the next
    ``/embed``) isn't throttled before its own ``warm_up()`` completes --
    otherwise every reload after the first would reproduce the Windows
    load-stall bug ``_ensure_priority_lowered_once`` exists to avoid.

    Windows-only: restores the EXACT class captured before the throttle
    first ran (not a hardcoded ``NORMAL_PRIORITY_CLASS``), so an engine
    deliberately started at some other class isn't clobbered to normal.
    Nothing to do when the throttle was disabled (``_priority_baseline`` is
    ``None`` in that case -- see ``_ensure_priority_lowered_once``) or never
    applied. POSIX's ``os.nice()`` cannot be raised back without
    ``CAP_SYS_NICE``/root for an unprivileged process, so there is nothing
    to restore there -- and nothing further to protect against either:
    POSIX's throttle is applied once, eagerly, at process startup (see
    ``main()``), never re-applied per reload, so it never compounds and a
    reload was never throttled-before-loading in the first place.
    """
    global _priority_lowered, _priority_baseline
    if sys.platform.startswith("win") and _priority_lowered and _priority_baseline is not None:
        from agent_index.indexing.priority import set_priority_class

        set_priority_class(_priority_baseline)
    _priority_lowered = False
    _priority_baseline = None


def main(argv: list[str] | None = None) -> int:
    """Run the engine worker from the command line."""
    if not sys.platform.startswith("win"):
        # POSIX: os.nice() only affects the calling thread, inherited by
        # threads created *afterward* -- applying it here, before uvicorn
        # creates its worker/event-loop threads, is what makes it cover the
        # whole running server rather than just whichever thread happens to
        # handle the first /embed request (see _get_pipeline's Windows-only
        # deferred path for the opposite platform's reasoning).
        _ensure_priority_lowered_once()
    parser = _build_parser()
    args = parser.parse_args(argv)
    run_engine(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

