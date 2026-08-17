"""FastAPI service shell for agent-index."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from threading import Condition, Lock
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel

from . import __version__
from .config import Config, data_dir, load_config, routing_dir, run_dir
from .query_surface import format_error, hit_to_dict, stored_cluster_to_dict
from .rendezvous import clear_endpoint, write_endpoint

log = logging.getLogger("agent-index.server")


class ReindexRequest(BaseModel):
    """Request body for kicking a best-effort reindex."""

    full: bool = False
    source: str | None = None


class DrainRequest(BaseModel):
    """Request body for the zdd drain endpoint."""

    timeout: float = 300.0
    poll: float = 1.0
    force: bool = False


class DrainGate:
    """Process-wide drain state plus in-flight search tracking."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._draining = False
        self._searches = 0

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

    @property
    def searches(self) -> int:
        with self._condition:
            return self._searches

    def set_draining(self, value: bool) -> None:
        with self._condition:
            self._draining = value
            self._condition.notify_all()

    @contextmanager
    def track_search(self) -> Iterator[None]:
        with self._condition:
            self._searches += 1
        try:
            yield
        finally:
            with self._condition:
                self._searches = max(0, self._searches - 1)
                self._condition.notify_all()

    def wait_for_searches(self, *, timeout: float, poll: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._searches > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(max(poll, 0.05), remaining))
            return True


class _EventBus:
    """Minimal event bus for TaskRunner; SSE fan-out is added by later surfaces."""

    def publish(self, _event: str, _payload: dict[str, Any]) -> None:
        return


def build_app() -> FastAPI:
    """Build the agent-index service application."""
    cached_search_engine: Any | None = None
    search_engine_lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        data_dir().mkdir(parents=True, exist_ok=True)
        runner = None
        try:
            from agent_index.indexing.runner import TaskRunner
            from agent_index.indexing.task_store import TaskStore

            store = TaskStore(data_dir() / "tasks.db")
            runner = TaskRunner(store, _EventBus())

            def _run_reindex(**kwargs: Any) -> dict[str, float]:
                from agent_index.indexing import engine as indexing_engine

                return indexing_engine.run_reindex(**kwargs)

            runner.set_index_fn(_run_reindex)
            app.state.task_store = store
            app.state.task_runner = runner
            await runner.start()
        except Exception:
            log.warning("Task runner startup skipped", exc_info=True)
        try:
            yield
        finally:
            if runner is not None:
                with contextlib.suppress(Exception):
                    await runner.stop()

    app = FastAPI(title="agent-index", version=__version__, lifespan=lifespan)
    app.state.drain_gate = DrainGate()

    def get_search_engine() -> Any:
        nonlocal cached_search_engine
        if cached_search_engine is not None:
            return cached_search_engine
        with search_engine_lock:
            if cached_search_engine is not None:
                return cached_search_engine
            from agent_index.search import engine as search_engine

            cached_search_engine = search_engine.create_search_engine()
            return cached_search_engine

    def mark_search_engine_failed() -> None:
        nonlocal cached_search_engine
        with search_engine_lock:
            cached_search_engine = None

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        gate: DrainGate = request.app.state.drain_gate
        return {"status": "draining" if gate.draining else "ok"}

    @app.get("/status")
    def status(request: Request, sources: bool = False) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        payload: dict[str, Any] = {
            "plugin": "agent-index",
            "version": __version__,
            "draining": gate.draining,
            "index": _index_status(include_sources=sources),
        }
        runner = getattr(request.app.state, "task_runner", None)
        if runner is not None:
            payload["indexing"] = runner.status()
        return payload

    @app.get("/search")
    def search(
        request: Request,
        q: str,
        source: str | None = None,
        language: str | None = None,
        repo: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        with gate.track_search():
            try:
                engine = get_search_engine()
                hits = engine.search(q, limit=limit, source=source, language=language, repo=repo)
                return {
                    "query": q,
                    "available": True,
                    "hits": [hit_to_dict(hit) for hit in hits],
                }
            except Exception as exc:
                mark_search_engine_failed()
                log.debug("Search unavailable", exc_info=True)
                return {"query": q, "available": False, "error": format_error(exc), "hits": []}

    @app.get("/similar")
    def similar(
        request: Request,
        chunk_id: Annotated[str, Query(alias="id")],
        limit: int = 10,
        source: str | None = None,
    ) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        with gate.track_search():
            try:
                engine = get_search_engine()
                hits = engine.find_similar(chunk_id, limit=limit, source=source)
                return {
                    "id": chunk_id,
                    "available": True,
                    "hits": [hit_to_dict(hit) for hit in hits],
                }
            except Exception as exc:
                mark_search_engine_failed()
                log.debug("Find-similar unavailable", exc_info=True)
                return {"id": chunk_id, "available": False, "error": format_error(exc), "hits": []}

    @app.get("/clusters")
    def clusters(
        request: Request,
        source: str | None = None,
        bucket: str | None = None,
        model: str | None = None,
        exact_dupes_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        try:
            from .index_config import IndexConfig
            from .store.cluster_store import ClusterStore
            from .store.clustering import source_bucket

            if source and not bucket:
                bucket = source_bucket(source)
            limit = max(1, min(limit, 200))
            cfg = IndexConfig(data_dir=data_dir())
            store = ClusterStore(cfg.clusters_db)
            stored = store.list_clusters(
                bucket=bucket,
                model_id=model,
                has_exact_dupes=True if exact_dupes_only else None,
                limit=limit,
                offset=max(0, offset),
            )
            return {
                "available": True,
                "count": len(stored),
                "clusters": [stored_cluster_to_dict(c) for c in stored],
            }
        except Exception as exc:
            log.debug("Clusters unavailable", exc_info=True)
            return {"available": False, "error": format_error(exc), "count": 0, "clusters": []}

    @app.post("/reindex")
    def reindex(request: Request, body: ReindexRequest | None = None) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        if gate.draining:
            return {"accepted": False, "error": "service is draining"}
        missing = _missing_indexing_dependencies()
        if missing:
            return {"accepted": False, "error": missing}
        full = body.full if body else False
        source = body.source if body else None
        store = getattr(request.app.state, "task_store", None)
        runner = getattr(request.app.state, "task_runner", None)
        if store is None or runner is None:
            return {"accepted": False, "error": "indexing task runner unavailable"}
        task = store.enqueue(
            source=source or "all",
            full=full,
            trigger_source="api:agent_index_reindex",
        )
        runner.notify()
        return {"accepted": True, "task": task.to_dict()}

    @app.post("/drain")
    async def drain(request: Request, body: DrainRequest | None = None) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        opts = body or DrainRequest()
        timeout = max(0.0, float(opts.timeout))
        poll = max(0.05, float(opts.poll))
        start = time.monotonic()
        gate.set_draining(True)
        runner = getattr(request.app.state, "task_runner", None)
        runner_clean = True
        if runner is not None:
            runner_clean = await runner.drain(timeout=timeout, poll=poll)
        remaining = max(0.0, timeout - (time.monotonic() - start))
        searches_clean = await asyncio.to_thread(
            gate.wait_for_searches,
            timeout=remaining,
            poll=poll,
        )
        clean = runner_clean and searches_clean
        forced = bool(opts.force and not clean)
        drained = clean or forced
        return {
            "drained": drained,
            "clean": clean,
            "forced": forced,
            "busy_searches": gate.searches,
            "active_task_id": getattr(runner, "active_task_id", None),
        }

    @app.post("/undrain")
    async def undrain(request: Request) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        gate.set_draining(False)
        runner = getattr(request.app.state, "task_runner", None)
        if runner is not None:
            await runner.resume()
        return {"draining": False}

    @app.post("/shutdown")
    def shutdown(request: Request) -> dict[str, Any]:
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
        return {"shutdown": True}

    @app.post("/adopt-relay")
    def adopt_relay() -> dict[str, Any]:
        return {"adopted": False, "reason": "agent-index has no relay"}

    return app


def _missing_indexing_dependencies() -> str | None:
    """Return a short missing-dependency message, or None when indexing can start.

    Embedding is delegated to the durable engine daemon (over HTTP), and in the
    worker-delegation model (model A) the actual crawl/store runs in a detached
    worker subprocess — so the SERVICE only needs the store library (``lancedb``)
    to accept a reindex. ``torch`` lives in the engine's own venv, not here, and
    must not gate the API on a torch-free service box.
    """
    missing = [name for name in ("lancedb",) if importlib.util.find_spec(name) is None]
    if missing:
        return f"missing optional indexing dependencies: {', '.join(missing)}"
    return None


def _index_status(include_sources: bool = False) -> dict[str, Any]:
    """Return real index counts when the optional store is available.

    A ``chunks`` value of ``0`` always means "measured empty"; when the store
    cannot be read at all, ``chunks``/``available`` are ``None`` ("unknown"),
    never a fabricated ``0`` (see dotfiles issue #1531 — a slow/unreachable
    service must not masquerade as a wiped index).

    The per-source histogram is O(n) over the content table, so it is computed
    only when ``include_sources`` is set — keeping ``/status`` fast on a large
    index — and is decoupled from the count so a histogram failure can never
    zero a valid ``count_rows()``.
    """
    try:
        import lancedb

        from .index_config import IndexConfig
    except Exception:
        log.debug("Index store library unavailable", exc_info=True)
        return {"chunks": None, "available": None, "tables": {}, "sources": {}}

    try:
        cfg = IndexConfig(data_dir=data_dir())
        lance_dir = cfg.lance_dir
        if not lance_dir.exists():
            return {"chunks": 0, "available": False, "tables": {}, "sources": {}}
        db = lancedb.connect(str(lance_dir))
        table_names = sorted(db.table_names())
    except Exception:
        log.debug("Index status unavailable", exc_info=True)
        return {"chunks": None, "available": None, "tables": {}, "sources": {}}

    tables: dict[str, int | None] = {}
    for table_name in table_names:
        try:
            table = db.open_table(table_name)
            tables[table_name] = int(table.count_rows())
        except Exception:
            # A count failure is "unknown," never a fabricated 0.
            log.debug("Failed to count index table %s", table_name, exc_info=True)
            tables[table_name] = None

    chunks = tables.get(cfg.content_table)
    available = chunks > 0 if isinstance(chunks, int) else None

    sources: dict[str, int] = {}
    if include_sources and isinstance(chunks, int) and chunks > 0:
        # O(n) histogram, kept separate from the count above so a failure here
        # leaves the valid chunk count intact.
        try:
            table = db.open_table(cfg.content_table)
            rows = table.search().select(["source"]).limit(chunks).to_list()
            for row in rows:
                source = row.get("source")
                if source:
                    sources[source] = sources.get(source, 0) + 1
        except Exception:
            log.debug("Failed to build source histogram", exc_info=True)

    return {"chunks": chunks, "available": available, "tables": tables, "sources": sources}


def _publish_routing(cfg: Config, bound_port: int, *, passive: bool = False) -> None:
    """Publish this process into the shared zdd routing table, best-effort."""
    if passive:
        return
    try:
        from zdd import routing

        routing.publish_active(
            routing_dir(),
            bind=cfg.host,
            port=bound_port,
            pid=os.getpid(),
            version=__version__,
            demote_existing=True,
        )
    except Exception:
        log.warning("Failed to publish routing table", exc_info=True)


def _clear_routing() -> None:
    """Clear this process from the shared zdd routing table, best-effort."""
    try:
        from zdd import routing

        routing.clear_if_owner(routing_dir(), os.getpid())
    except Exception:
        log.debug("Routing-table clear-on-shutdown skipped", exc_info=True)


def serve(cfg: Config | None = None, *, passive: bool = False) -> None:
    """Bind an OS-assigned local endpoint and run the service."""
    import uvicorn

    cfg = cfg or load_config()
    data_dir().mkdir(parents=True, exist_ok=True)
    sock = _bind_listen_socket(cfg.host, cfg.port)
    bound_port = sock.getsockname()[1]
    write_endpoint(run_dir(), "tcp", f"{cfg.host}:{bound_port}")
    _publish_routing(cfg, bound_port, passive=passive)
    from .runtime_version import write_running_version

    write_running_version()
    app = build_app()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level=os.environ.get("AGENT_INDEX_LOG_LEVEL", "info"),
        )
    )
    app.state.uvicorn_server = server
    try:
        server.run(sockets=[sock])
    finally:
        _clear_routing()
        clear_endpoint(run_dir())
        sock.close()


def _bind_listen_socket(host: str, port: int) -> socket.socket:
    """Bind and return a listening TCP socket for host:port."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        family, socktype, proto, _canon, sockaddr = infos[0]
        sock = socket.socket(family, socktype, proto)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(sockaddr)
        sock.listen(socket.SOMAXCONN)
        return sock
    except OSError as exc:
        print(f"agent-index: failed to bind {host}:{port}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
