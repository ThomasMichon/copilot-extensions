"""FastAPI service shell for agent-index."""

from __future__ import annotations

import logging
import os
import socket
import sys

from fastapi import FastAPI

from . import __version__
from .config import Config, data_dir, load_config, run_dir
from .rendezvous import clear_endpoint, write_endpoint

log = logging.getLogger("agent-index.server")


def build_app() -> FastAPI:
    """Build the Phase 1 service shell application."""
    app = FastAPI(title="agent-index", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict:
        # Phase 2: indexing engine status and ingest queue state land here.
        return {
            "plugin": "agent-index",
            "version": __version__,
            "index": {"chunks": 0},
        }

    return app


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
