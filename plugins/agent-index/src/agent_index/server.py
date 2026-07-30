"""FastAPI service shell for agent-index."""

from __future__ import annotations

import importlib.util
import logging
import os
import socket
import sys
from threading import Lock, Thread
from typing import Annotated, Any

from fastapi import FastAPI, Query
from pydantic import BaseModel

from . import __version__
from .config import Config, data_dir, load_config, run_dir
from .query_surface import format_error, hit_to_dict
from .rendezvous import clear_endpoint, write_endpoint

log = logging.getLogger("agent-index.server")


class ReindexRequest(BaseModel):
    """Request body for kicking a best-effort reindex."""

    full: bool = False
    source: str | None = None


def build_app() -> FastAPI:
    """Build the agent-index service application."""
    app = FastAPI(title="agent-index", version=__version__)
    cached_search_engine: Any | None = None
    search_engine_lock = Lock()

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
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict:
        return {
            "plugin": "agent-index",
            "version": __version__,
            "index": _index_status(),
        }

    @app.get("/search")
    def search(
        q: str,
        source: str | None = None,
        language: str | None = None,
        repo: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        try:
            engine = get_search_engine()
            hits = engine.search(
                q,
                limit=limit,
                source=source,
                language=language,
                repo=repo,
            )
            return {"query": q, "available": True, "hits": [hit_to_dict(hit) for hit in hits]}
        except Exception as exc:
            mark_search_engine_failed()
            log.debug("Search unavailable", exc_info=True)
            return {"query": q, "available": False, "error": format_error(exc), "hits": []}

    @app.get("/similar")
    def similar(
        chunk_id: Annotated[str, Query(alias="id")],
        limit: int = 10,
        source: str | None = None,
    ) -> dict[str, Any]:
        try:
            engine = get_search_engine()
            hits = engine.find_similar(chunk_id, limit=limit, source=source)
            return {"id": chunk_id, "available": True, "hits": [hit_to_dict(hit) for hit in hits]}
        except Exception as exc:
            mark_search_engine_failed()
            log.debug("Find-similar unavailable", exc_info=True)
            return {"id": chunk_id, "available": False, "error": format_error(exc), "hits": []}

    @app.post("/reindex")
    def reindex(request: ReindexRequest | None = None) -> dict[str, Any]:
        missing = _missing_indexing_dependencies()
        if missing:
            return {"accepted": False, "error": missing}
        try:
            from agent_index.indexing import engine as indexing_engine
        except Exception as exc:
            return {"accepted": False, "error": format_error(exc)}

        full = request.full if request else False
        source = request.source if request else None

        def run() -> None:
            try:
                indexing_engine.run_reindex(full=full, source=source)
            except Exception:
                log.exception("Background reindex failed")

        Thread(target=run, name="agent-index-reindex", daemon=True).start()
        return {"accepted": True}

    return app


def _missing_indexing_dependencies() -> str | None:
    """Return a short missing-dependency message, or None when indexing can start."""
    missing = [name for name in ("lancedb", "torch") if importlib.util.find_spec(name) is None]
    if missing:
        return f"missing optional indexing dependencies: {', '.join(missing)}"
    return None


def _index_status() -> dict[str, Any]:
    """Return real index counts when the optional store is available."""
    try:
        import lancedb

        from .index_config import IndexConfig

        cfg = IndexConfig(data_dir=data_dir())
        lance_dir = cfg.lance_dir
        if not lance_dir.exists():
            return {"chunks": 0, "available": False, "tables": {}, "sources": {}}

        db = lancedb.connect(str(lance_dir))
        table_names = sorted(db.table_names())
        tables: dict[str, int] = {}
        sources: dict[str, int] = {}
        for table_name in table_names:
            try:
                table = db.open_table(table_name)
                tables[table_name] = int(table.count_rows())
                if table_name == cfg.content_table and tables[table_name] > 0:
                    rows = table.search().select(["source"]).limit(tables[table_name]).to_list()
                    for row in rows:
                        source = row.get("source")
                        if source:
                            sources[source] = sources.get(source, 0) + 1
            except Exception:
                log.debug("Failed to read index table %s", table_name, exc_info=True)
                tables[table_name] = 0
        chunks = tables.get(cfg.content_table, 0)
        return {
            "chunks": chunks,
            "available": chunks > 0,
            "tables": tables,
            "sources": sources,
        }
    except Exception:
        log.debug("Index status unavailable", exc_info=True)
        return {"chunks": 0, "available": False, "tables": {}, "sources": {}}


def serve(cfg: Config | None = None) -> None:
    """Bind an OS-assigned local endpoint and run the service."""
    import uvicorn

    cfg = cfg or load_config()
    data_dir().mkdir(parents=True, exist_ok=True)
    sock = _bind_listen_socket(cfg.host, cfg.port)
    bound_port = sock.getsockname()[1]
    write_endpoint(run_dir(), "tcp", f"{cfg.host}:{bound_port}")
    from .runtime_version import write_running_version

    write_running_version()
    try:
        uvicorn.Server(
            uvicorn.Config(build_app(), log_level=os.environ.get("AGENT_INDEX_LOG_LEVEL", "info"))
        ).run(sockets=[sock])
    finally:
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
        return sock
    except OSError as exc:
        print(f"agent-index: failed to bind {host}:{port}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
