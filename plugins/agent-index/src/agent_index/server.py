"""FastAPI service shell for agent-index."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import socket
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import Condition, Lock
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from . import __version__
from .config import Config, data_dir, install_dir, load_config, routing_dir, run_dir
from .query_surface import format_error, hit_to_dict, stored_cluster_to_dict
from .rendezvous import clear_endpoint, write_endpoint
from .runtime_version import current_runtime_version

log = logging.getLogger("agent-index.server")
INSTALLATION_HEADER = "X-Agent-Index-Installation-Id"
INSTANCE_HEADER = "X-Agent-Index-Instance-Token"
TRANSACTION_HEADER = "X-Agent-Index-Transaction-Token"


class DrainAdmissionClosed(RuntimeError):
    """New read work is closed while the service drains."""


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
    """Process-wide drain state plus atomic in-flight read admission."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._draining = False
        self._reads = 0

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

    @property
    def reads(self) -> int:
        with self._condition:
            return self._reads

    @property
    def searches(self) -> int:
        """Compatibility alias for older drain diagnostics."""
        return self.reads

    def set_draining(self, value: bool) -> None:
        with self._condition:
            self._draining = value
            self._condition.notify_all()

    @contextmanager
    def track_read(self) -> Iterator[None]:
        with self._condition:
            if self._draining:
                raise DrainAdmissionClosed("service is draining")
            self._reads += 1
        try:
            yield
        finally:
            with self._condition:
                self._reads = max(0, self._reads - 1)
                self._condition.notify_all()

    def wait_for_reads(self, *, timeout: float, poll: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._reads > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(max(poll, 0.05), remaining))
            return True

    def wait_for_searches(self, *, timeout: float, poll: float) -> bool:
        """Compatibility alias for older callers."""
        return self.wait_for_reads(timeout=timeout, poll=poll)


class _EventBus:
    """Minimal event bus for TaskRunner; SSE fan-out is added by later surfaces."""

    def publish(self, _event: str, _payload: dict[str, Any]) -> None:
        return


def _create_task_runner() -> Any:
    from agent_index.indexing.runner import TaskRunner
    from agent_index.indexing.task_store import TaskStore

    store = TaskStore(data_dir() / "tasks.db")
    runner = TaskRunner(store, _EventBus())

    def _run_reindex(**kwargs: Any) -> dict[str, float]:
        from agent_index.indexing import engine as indexing_engine

        return indexing_engine.run_reindex(**kwargs)

    runner.set_index_fn(_run_reindex)
    return store, runner


async def _start_task_runner(app: FastAPI) -> None:
    if getattr(app.state, "task_runner", None) is not None:
        return
    async with app.state.task_runner_lock:
        if getattr(app.state, "task_runner", None) is not None:
            return
        store, runner = _create_task_runner()
        try:
            await runner.start()
        except Exception:
            with contextlib.suppress(Exception):
                await runner.stop()
            raise
        app.state.task_store = store
        app.state.task_runner = runner


async def _stop_task_runner(app: FastAPI) -> None:
    runner = getattr(app.state, "task_runner", None)
    if runner is None:
        return
    with contextlib.suppress(Exception):
        await runner.stop()
    app.state.task_runner = None
    app.state.task_store = None


def _instance_receipt_path(app: FastAPI) -> Path | None:
    if not app.state.installation_id:
        return None
    return run_dir() / "instances" / f"{os.getpid()}.json"


def _write_instance_receipt(app: FastAPI, state: str) -> None:
    path = _instance_receipt_path(app)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "copilot-extensions.agent-index.service-instance",
        "version": 1,
        "installationId": app.state.installation_id,
        "runtimeVersion": app.state.runtime_version,
        "pid": os.getpid(),
        "instanceToken": app.state.instance_token,
        "host": app.state.bound_host,
        "port": app.state.bound_port,
        "state": state,
        "transactionId": app.state.transaction_id,
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if os.name != "nt":
        with contextlib.suppress(OSError):
            temporary.chmod(0o600)
    os.replace(temporary, path)


def _clear_instance_receipt(app: FastAPI) -> None:
    path = _instance_receipt_path(app)
    if path is None:
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if (
        isinstance(record, dict)
        and record.get("pid") == os.getpid()
        and record.get("instanceToken") == app.state.instance_token
    ):
        with contextlib.suppress(OSError):
            path.unlink()


def _publish_active_evidence(app: FastAPI, *, strict: bool) -> None:
    try:
        write_endpoint(
            run_dir(),
            "tcp",
            f"{app.state.bound_host}:{app.state.bound_port}",
        )
        from .runtime_version import write_running_version

        write_running_version(strict=strict)
    except Exception:
        if strict:
            raise
        log.warning("Failed to publish active service evidence", exc_info=True)


def build_app(*, passive: bool = False) -> FastAPI:
    """Build the agent-index service application."""
    cached_search_engine: Any | None = None
    search_engine_lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        data_dir().mkdir(parents=True, exist_ok=True)
        if app.state.promoted:
            try:
                await _start_task_runner(app)
            except Exception:
                log.warning("Task runner startup skipped", exc_info=True)
        try:
            yield
        finally:
            await _stop_task_runner(app)

    app = FastAPI(title="agent-index", version=__version__, lifespan=lifespan)
    app.state.drain_gate = DrainGate()
    app.state.installation_id = os.environ.get("AGENT_INDEX_INSTALLATION_ID", "")
    app.state.instance_token = (
        os.environ.get("AGENT_INDEX_INSTANCE_TOKEN") or uuid.uuid4().hex
    )
    app.state.transaction_token = os.environ.get(
        "AGENT_INDEX_CELL_TRANSACTION_TOKEN", ""
    )
    app.state.transaction_id = os.environ.get(
        "AGENT_INDEX_CELL_TRANSACTION_ID", ""
    ) or None
    app.state.runtime_version = current_runtime_version()
    app.state.passive = passive
    app.state.promoted = not passive
    app.state.bound_host = None
    app.state.bound_port = None
    app.state.task_store = None
    app.state.task_runner = None
    app.state.task_runner_lock = asyncio.Lock()

    def service_identity(request: Request) -> dict[str, Any]:
        return {
            "installationId": request.app.state.installation_id,
            "instanceToken": request.app.state.instance_token,
            "pid": os.getpid(),
        }

    def authorize_control(request: Request) -> None:
        expected_installation = request.headers.get(INSTALLATION_HEADER, "")
        expected_instance = request.headers.get(INSTANCE_HEADER)
        if expected_installation != request.app.state.installation_id:
            raise HTTPException(status_code=409, detail="installation ownership mismatch")
        if request.app.state.installation_id and not expected_instance:
            raise HTTPException(status_code=409, detail="service instance token required")
        if (
            expected_instance is not None
            and expected_instance != request.app.state.instance_token
        ):
            raise HTTPException(status_code=409, detail="service instance mismatch")

    def authorize_transaction(request: Request) -> None:
        if not request.app.state.installation_id:
            return
        expected = request.app.state.transaction_token
        supplied = request.headers.get(TRANSACTION_HEADER, "")
        path_value = os.environ.get("AGENT_INDEX_CELL_TRANSACTION", "")
        transaction_id = os.environ.get("AGENT_INDEX_CELL_TRANSACTION_ID", "")
        valid_receipt = False
        try:
            path = Path(path_value)
            expected_path = install_dir() / "selection-transaction.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            target = receipt.get("target") if isinstance(receipt, dict) else None
            valid_receipt = (
                path.resolve(strict=True) == expected_path.resolve(strict=True)
                and isinstance(receipt, dict)
                and receipt.get("schema")
                == "copilot-extensions.agent-index.selection-transaction"
                and receipt.get("version") == 1
                and receipt.get("id") == transaction_id
                and receipt.get("installationId")
                == request.app.state.installation_id
                and receipt.get("token") == expected
                and receipt.get("state")
                in {
                    "prepared",
                    "marker-published",
                    "manifest-published",
                    "reconciling",
                }
                and isinstance(target, dict)
                and target.get("runtimeVersion") == request.app.state.runtime_version
            )
        except (OSError, ValueError, TypeError):
            valid_receipt = False
        if not expected or supplied != expected or not valid_receipt:
            raise HTTPException(
                status_code=409,
                detail="installation transaction ownership mismatch",
            )

    @contextmanager
    def track_read(request: Request) -> Iterator[None]:
        if not request.app.state.promoted:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "service_passive",
                    "retryable": True,
                    "message": "service is awaiting promotion",
                },
                headers={"Retry-After": "1"},
            )
        gate: DrainGate = request.app.state.drain_gate
        try:
            with gate.track_read():
                yield
        except DrainAdmissionClosed as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "service_draining",
                    "retryable": True,
                    "message": str(exc),
                },
                headers={"Retry-After": "1"},
            ) from exc

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
    def health(request: Request) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        return {
            "status": (
                "draining"
                if gate.draining
                else ("ok" if request.app.state.promoted else "passive")
            ),
            "plugin": "agent-index",
            "version": request.app.state.runtime_version,
            "passive": request.app.state.passive,
            "promoted": request.app.state.promoted,
            **service_identity(request),
        }

    @app.get("/status")
    def status(request: Request, sources: bool = False) -> dict[str, Any]:
        gate: DrainGate = request.app.state.drain_gate
        payload: dict[str, Any] = {
            "plugin": "agent-index",
            "version": request.app.state.runtime_version,
            "draining": gate.draining,
            "passive": request.app.state.passive,
            "promoted": request.app.state.promoted,
            "inflightReads": gate.reads,
            "index": _index_status(include_sources=sources),
            **service_identity(request),
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
        with track_read(request):
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
        with track_read(request):
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
        with track_read(request):
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
                return {
                    "available": False,
                    "error": format_error(exc),
                    "count": 0,
                    "clusters": [],
                }

    @app.post("/reindex")
    def reindex(request: Request, body: ReindexRequest | None = None) -> dict[str, Any]:
        with track_read(request):
            missing = _missing_indexing_dependencies()
            if missing:
                return {"accepted": False, "error": missing}
            full = body.full if body else False
            source = body.source if body else None
            store = getattr(request.app.state, "task_store", None)
            runner = getattr(request.app.state, "task_runner", None)
            if store is None or runner is None or not request.app.state.promoted:
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
        authorize_control(request)
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
        reads_clean = await asyncio.to_thread(
            gate.wait_for_reads,
            timeout=remaining,
            poll=poll,
        )
        clean = runner_clean and reads_clean
        forced = bool(opts.force and not clean)
        drained = clean or forced
        return {
            "drained": drained,
            "clean": clean,
            "forced": forced,
            "busy_reads": gate.reads,
            "busy_searches": gate.reads,
            "active_task_id": getattr(runner, "active_task_id", None),
        }

    @app.post("/undrain")
    async def undrain(request: Request) -> dict[str, Any]:
        authorize_control(request)
        gate: DrainGate = request.app.state.drain_gate
        gate.set_draining(False)
        runner = getattr(request.app.state, "task_runner", None)
        if runner is not None:
            await runner.resume()
        return {"draining": False}

    @app.post("/promote")
    async def promote(request: Request) -> dict[str, Any]:
        authorize_control(request)
        if request.app.state.promoted:
            return {"promoted": True, **service_identity(request)}
        authorize_transaction(request)
        transaction_environment = {
            name: os.environ.get(name)
            for name in (
                "AGENT_INDEX_CELL_TRANSACTION",
                "AGENT_INDEX_CELL_TRANSACTION_TOKEN",
                "AGENT_INDEX_CELL_TRANSACTION_ID",
            )
        }
        try:
            for name in transaction_environment:
                os.environ.pop(name, None)
            await _start_task_runner(request.app)
            request.app.state.passive = False
            request.app.state.promoted = True
            _write_instance_receipt(request.app, "active")
            _publish_active_evidence(request.app, strict=True)
        except Exception as exc:
            request.app.state.passive = True
            request.app.state.promoted = False
            with contextlib.suppress(Exception):
                _write_instance_receipt(request.app, "passive")
            await _stop_task_runner(request.app)
            clear_endpoint(run_dir(), owner_pid=os.getpid())
            from .runtime_version import clear_running_version

            clear_running_version(owner_pid=os.getpid())
            for name, value in transaction_environment.items():
                if value is not None:
                    os.environ[name] = value
            raise HTTPException(
                status_code=503,
                detail="passive service promotion failed",
            ) from exc
        return {"promoted": True, **service_identity(request)}

    @app.post("/shutdown")
    def shutdown(request: Request) -> dict[str, Any]:
        authorize_control(request)
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

    # The store was read successfully, so an absent content table means a
    # measurably-empty index (0), NOT "unknown". Only a failed count_rows()
    # (recorded as None above) is genuinely unknown.
    if cfg.content_table in tables:
        chunks = tables[cfg.content_table]
    else:
        chunks = 0
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


def _publish_routing(
    cfg: Config,
    bound_port: int,
    *,
    passive: bool = False,
    strict: bool = False,
) -> None:
    """Publish this process into the shared zdd routing table."""
    if passive:
        return
    try:
        from zdd import routing

        routing.publish_active(
            routing_dir(),
            bind=cfg.host,
            port=bound_port,
            pid=os.getpid(),
            version=current_runtime_version(),
            demote_existing=True,
        )
    except Exception:
        if strict:
            raise
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
    app = build_app(passive=passive)
    if not passive:
        for name in (
            "AGENT_INDEX_CELL_TRANSACTION",
            "AGENT_INDEX_CELL_TRANSACTION_TOKEN",
            "AGENT_INDEX_CELL_TRANSACTION_ID",
        ):
            os.environ.pop(name, None)
    app.state.bound_host = cfg.host
    app.state.bound_port = bound_port
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level=os.environ.get("AGENT_INDEX_LOG_LEVEL", "info"),
        )
    )
    app.state.uvicorn_server = server
    try:
        _write_instance_receipt(app, "passive" if passive else "active")
        if not passive:
            namespaced = bool(app.state.installation_id)
            _publish_active_evidence(app, strict=namespaced)
            _publish_routing(cfg, bound_port, strict=namespaced)
        server.run(sockets=[sock])
    finally:
        if app.state.promoted:
            _clear_routing()
            clear_endpoint(run_dir(), owner_pid=os.getpid())
            from .runtime_version import clear_running_version

            clear_running_version(owner_pid=os.getpid())
        _clear_instance_receipt(app)
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
