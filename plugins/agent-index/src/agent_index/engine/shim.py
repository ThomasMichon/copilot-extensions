"""agent-index Engine Shim -- always-on activator for the ephemeral GPU worker.

Keeps the engine *container* always up while the heavy torch *worker* process
inside it starts on demand and stops on idle. The shim holds the public port,
supervises the worker subprocess (``python -m agent_index.engine.app`` on an internal loopback
port), reverse-proxies embed traffic to it, and answers liveness locally so the
container stays healthy while the model is cold.

This is the in-container reimplementation of systemd socket activation: the
container is a vanilla always-on service (any orchestrator runs it with no
bring-up logic), while VRAM + the ~1 GB torch process baseline are reclaimed
whenever the worker idles out. See effort service-containerization (#601) and
issue #1662 -- agent-index is the reference implementation of this reusable pattern.

Division of responsibility (single idle authority): the SHIM owns lifecycle. It
launches the worker with ``AGENT_INDEX_ENGINE_IDLE_TIMEOUT=0`` so the worker never
self-exits; the shim's own idle monitor stops the worker. Stopping the worker (SIGTERM) cleanly frees VRAM.

Activity semantics mirror the worker: ``/embed``, ``/embed/batch``, ``/spinup``
(and any other proxied path) count as activity and wake the worker; ``/health``
and ``/ready`` deliberately do NOT, so liveness polling can't pin the worker
awake. ``/spindown`` stops the worker outright (full reclaim) rather than only
unloading the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that must never wake the worker (liveness only). Everything else that is
# proxied is treated as activity.
_LIVENESS_PATHS = frozenset({"/health", "/ready"})

# Hop-by-hop / length headers we must not forward verbatim through the proxy.
# content-encoding is stripped because httpx auto-decompresses upstream.content,
# so relaying the original Content-Encoding would make the client try to
# decompress already-decompressed bytes (matters once a compressing worker
# exists; this is the reusable #1662 proxy).
_SKIP_REQUEST_HEADERS = frozenset({"host", "content-length"})
_SKIP_RESPONSE_HEADERS = frozenset(
    {"content-length", "content-encoding", "transfer-encoding", "connection"}
)


def _worker_command(worker_host: str, worker_port: int) -> list[str]:
    """Command that launches the GPU worker subprocess (bound to host/port).

    The command *prefix* is overridable via ``AGENT_INDEX_ENGINE_WORKER_CMD`` (a JSON
    list) -- used by tests to substitute a lightweight fake worker so the shim's
    spawn/proxy/stop path is exercised without torch/GPU. ``--host``/``--port``
    are always appended (so the override cannot omit them). The default prefix
    runs ``python -m agent_index.engine.app`` in this same interpreter/venv.
    """
    prefix: list[str] | None = None
    raw = os.environ.get("AGENT_INDEX_ENGINE_WORKER_CMD")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(a, str) for a in parsed):
                prefix = parsed
            else:
                logger.warning("AGENT_INDEX_ENGINE_WORKER_CMD is not a JSON list of strings; ignoring")
        except (ValueError, TypeError):
            logger.warning("AGENT_INDEX_ENGINE_WORKER_CMD is not valid JSON; ignoring")
    if prefix is None:
        prefix = [sys.executable, "-m", "agent_index.engine.app"]
    return [*prefix, "--host", worker_host, "--port", str(worker_port)]


class WorkerSupervisor:
    """Owns the lifecycle of the single GPU worker subprocess.

    The worker is spawned on demand and terminated on idle/shutdown. ``ensure``
    and ``stop`` are serialized by an asyncio lock so concurrent embed requests
    trigger exactly one spawn.
    """

    def __init__(
        self,
        *,
        worker_host: str = "127.0.0.1",
        worker_port: int = 9421,
        start_timeout: float = 60.0,
        stop_timeout: float = 15.0,
    ) -> None:
        self._host = worker_host
        self._port = worker_port
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def running(self) -> bool:
        """True if the worker process is alive (not yet exited)."""
        return self._proc is not None and self._proc.poll() is None

    def _spawn_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Single idle authority: the shim owns idle, so the worker must not
        # self-exit underneath it.
        env["AGENT_INDEX_ENGINE_IDLE_TIMEOUT"] = "0"
        return env

    async def ensure(self, probe: httpx.AsyncClient) -> None:
        """Ensure the worker process is up and its HTTP server answers /health.

        Spawns the worker if needed and polls ``/health`` until it responds or
        ``start_timeout`` elapses. The model itself loads lazily on the first
        embed, so this only waits for the server to bind -- not for the model.
        """
        async with self._lock:
            if self.running():
                return
            cmd = _worker_command(self._host, self._port)
            logger.info("Shim: starting GPU worker: %s", " ".join(cmd))
            try:
                self._proc = subprocess.Popen(cmd, env=self._spawn_env())  # noqa: ASYNC220 - non-blocking spawn; worker driven over HTTP, blocking waits offloaded via to_thread
            except OSError as exc:
                # Misconfiguration (bad AGENT_INDEX_ENGINE_WORKER_CMD, missing
                # interpreter) -- normalize to RuntimeError so the proxy maps it
                # to 503 (the "worker unavailable" contract) rather than a 500.
                self._proc = None
                raise RuntimeError(f"Failed to launch GPU worker: {exc}") from exc

            deadline = time.monotonic() + self._start_timeout
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    code = self._proc.returncode
                    self._proc = None
                    raise RuntimeError(f"GPU worker exited during startup (code {code})")
                try:
                    resp = await probe.get(f"{self.base_url}/health", timeout=2.0)
                    if resp.status_code == 200:
                        logger.info("Shim: GPU worker up at %s", self.base_url)
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.5)

            await self._terminate()
            raise RuntimeError(
                f"GPU worker did not answer /health within {self._start_timeout:.0f}s"
            )

    async def stop(self) -> None:
        """Terminate the worker process (frees VRAM + process RAM), if running."""
        async with self._lock:
            await self._terminate()

    async def _terminate(self) -> None:
        """SIGTERM the worker and reap it. Caller holds the lock."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            logger.info("Shim: stopping GPU worker (idle/shutdown)")
            proc.terminate()  # SIGTERM -> uvicorn graceful shutdown
            try:
                await asyncio.to_thread(proc.wait, self._stop_timeout)
            except subprocess.TimeoutExpired:
                logger.warning("Shim: GPU worker did not exit in time; killing")
                proc.kill()
                await asyncio.to_thread(proc.wait)
        self._proc = None


class ActivityTracker:
    """Tracks in-flight requests + last activity for the idle monitor."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight = 0
        self._last = time.monotonic()

    async def begin(self) -> None:
        async with self._lock:
            self._inflight += 1

    async def end(self) -> None:
        async with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._last = time.monotonic()

    async def is_idle(self, timeout: float) -> bool:
        async with self._lock:
            return self._inflight == 0 and (time.monotonic() - self._last) >= timeout


def _idle_timeout_seconds() -> float:
    try:
        return float(os.environ.get("AGENT_INDEX_ENGINE_IDLE_TIMEOUT", "0"))
    except ValueError:
        return 0.0


def _synth_health() -> dict[str, object]:
    """Health payload for when the worker is down -- reported by the shim itself.

    Kept torch-free (the shim must stay lightweight): GPU-dependent fields are
    null because they require the worker. ``status`` stays ``ok`` so the
    container healthcheck passes and EngineClient sees the shim as reachable.
    """
    return {
        "status": "ok",
        "gpu_deps_installed": None,
        "model_loaded": False,
        "model_name": os.environ.get("AGENT_INDEX_MODEL"),
        "device": os.environ.get("AGENT_INDEX_DEVICE", "cuda"),
        "cuda_available": None,
        "detail": "worker idle (not running); starts on the next embed",
        "worker_running": False,
    }


def create_app(
    supervisor: WorkerSupervisor | None = None,
    *,
    idle_timeout: float | None = None,
) -> FastAPI:
    """Build the shim FastAPI app.

    ``supervisor`` is injectable for tests; ``idle_timeout`` defaults to
    ``AGENT_INDEX_ENGINE_IDLE_TIMEOUT``. A value <= 0 keeps the worker resident once
    started (always-on-when-warm), still behind the always-on container.
    """
    sup = supervisor or WorkerSupervisor(
        worker_host=os.environ.get("AGENT_INDEX_ENGINE_WORKER_HOST", "127.0.0.1"),
        worker_port=int(os.environ.get("AGENT_INDEX_ENGINE_WORKER_PORT", "9421")),
    )
    timeout = idle_timeout if idle_timeout is not None else _idle_timeout_seconds()
    activity = ActivityTracker()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
        )
        app.state.client = client
        idle_task: asyncio.Task[None] | None = None

        if timeout > 0:
            async def _idle_monitor() -> None:
                poll = min(timeout, 30.0)
                while True:
                    await asyncio.sleep(poll)
                    # There is a benign TOCTOU here: a request can pass
                    # activity.begin()/ensure() and enter _forward in the window
                    # between this is_idle() check and stop() acquiring the
                    # worker lock, so stop() could terminate a worker mid-request.
                    # This is rendered harmless -- not merely rare -- by the
                    # proxy catching the resulting httpx.TransportError and
                    # returning 503 (fail-loud, never a silent vectorization
                    # skip). We deliberately do NOT nest the activity + worker
                    # locks to close it, as proxy acquires them in the opposite
                    # order (activity then worker) and nesting here would risk a
                    # deadlock.
                    if sup.running() and await activity.is_idle(timeout):
                        logger.info(
                            "Shim: worker idle >= %.0fs -- stopping to free VRAM", timeout
                        )
                        try:
                            await sup.stop()
                        except Exception:
                            logger.exception("Shim: error stopping idle worker")
            idle_task = asyncio.create_task(_idle_monitor())
            logger.info("Shim: idle worker-stop enabled: %.0fs", timeout)

        logger.info("agent-index engine shim ready (worker starts on first embed)")
        try:
            yield
        finally:
            if idle_task is not None:
                idle_task.cancel()
            await sup.stop()
            await client.aclose()
            logger.info("agent-index engine shim shut down")

    app = FastAPI(
        title="agent-index Engine Shim",
        description="Always-on activator for the on-demand GPU embedding worker",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> JSONResponse:
        """Liveness -- answered by the shim; never wakes the worker."""
        if sup.running():
            try:
                resp = await app.state.client.get(f"{sup.base_url}/health", timeout=5.0)
                payload = resp.json()
                payload["worker_running"] = True
                return JSONResponse(payload, status_code=200)
            except (httpx.TransportError, ValueError):
                pass
        return JSONResponse(_synth_health(), status_code=200)

    @app.get("/ready")
    async def ready() -> JSONResponse:
        """Readiness -- false when the worker is down; never wakes it."""
        if sup.running():
            try:
                resp = await app.state.client.get(f"{sup.base_url}/ready", timeout=5.0)
                return JSONResponse(resp.json(), status_code=resp.status_code)
            except (httpx.TransportError, ValueError):
                pass
        return JSONResponse({"ready": False}, status_code=200)

    @app.post("/spindown")
    async def spindown() -> JSONResponse:
        """Stop the worker entirely (frees VRAM + process RAM). No-op if down."""
        await sup.stop()
        return JSONResponse({"status": "ok", "model_loaded": False}, status_code=200)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy(path: str, request: Request) -> Response:
        """Activity path: ensure the worker is up, then reverse-proxy to it."""
        await activity.begin()
        try:
            await sup.ensure(app.state.client)
            return await _forward(app.state.client, sup.base_url, request)
        except (RuntimeError, httpx.TransportError) as exc:
            # Worker could not be started (RuntimeError from ensure) or died
            # mid-request (httpx.TransportError from _forward -- a worker crash,
            # e.g. GPU OOM, or the idle monitor stopping it in the narrow race
            # between is_idle() and stop()). Reap the dead worker so the next
            # request respawns it, and return 503 -- NOT a bare 500.
            #
            # The status code is load-bearing for the #775 fail-loud guarantee:
            # EngineClient raises EngineUnavailableError only on a TransportError
            # or HTTP 503, which agent_index/indexing/engine.py re-raises to fail the
            # source loud; a 500 becomes an HTTPStatusError that indexing
            # SWALLOWS as a warning, advancing the source commit while the batch
            # goes unvectorized (silent-skip, invisible to search until a full
            # reindex). 503 routes worker-death into the fail-loud/retry path.
            logger.warning("Shim: worker unavailable (%s); reaping", type(exc).__name__)
            await sup.stop()
            return JSONResponse(
                {"detail": f"GPU worker unavailable: {exc!r}"}, status_code=503
            )
        finally:
            await activity.end()

    return app


async def _forward(client: httpx.AsyncClient, base_url: str, request: Request) -> Response:
    """Reverse-proxy the incoming request to the worker and relay its response."""
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _SKIP_REQUEST_HEADERS
    }
    url = f"{base_url}/{request.path_params['path']}"
    upstream = await client.request(
        request.method,
        url,
        content=body,
        params=request.query_params,
        headers=headers,
    )
    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _SKIP_RESPONSE_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


def run_shim(*, host: str = "127.0.0.1", port: int = 8421, worker_port: int | None = None) -> None:
    """Start the shim server with uvicorn.

    The worker binds an internal loopback port (default ``port + 1000``); the
    shim owns the public ``host``/``port``. A ``docker stop`` (SIGTERM) runs the
    lifespan shutdown, which stops the worker.
    """
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    wport = worker_port if worker_port is not None else port + 1000
    supervisor = WorkerSupervisor(
        worker_host=os.environ.get("AGENT_INDEX_ENGINE_WORKER_HOST", "127.0.0.1"),
        worker_port=int(os.environ.get("AGENT_INDEX_ENGINE_WORKER_PORT", str(wport))),
    )
    app = create_app(supervisor)

    print(f"Starting agent-index engine shim on {host}:{port} (worker :{supervisor._port})")
    uvicorn.run(app, host=host, port=port, log_level="info")
