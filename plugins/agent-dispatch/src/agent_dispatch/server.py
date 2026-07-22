"""Run the coordinator with uvicorn (the ``agent-dispatch serve`` command)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import Config, load_config, requires_token_bind, run_dir
from .coordinator import create_app
from .queue import TaskQueue
from .rendezvous import clear_endpoint, write_endpoint

log = logging.getLogger("agent-dispatch.server")


class UnsafeBindError(RuntimeError):
    """Raised when the coordinator would bind the LAN without a bearer token."""


def check_bind_safety(cfg: Config) -> None:
    """Refuse to expose the task-control API on all interfaces unauthenticated.

    Binding a wildcard host (``0.0.0.0``/``::``) puts the coordinator on the LAN;
    without a bearer token that is an open remote-control surface. A **token is
    mandatory** in that mode. (A specific host-local bind -- loopback, a Windows
    vEthernet(WSL) IP, or a Docker bridge gateway -- is a deliberate non-LAN
    interface choice and is allowed without this guard; scope it off the LAN with
    a firewall as appropriate.)
    """
    if requires_token_bind(cfg.host) and not cfg.token:
        raise UnsafeBindError(
            f"refusing to bind {cfg.host}:{cfg.port} without a bearer token: the "
            "agent-dispatch task-control API must not be exposed on the LAN "
            "unauthenticated. Set AGENT_DISPATCH_TOKEN (and firewall the port off "
            "the LAN), or bind a specific host-local interface instead."
        )


def build_app(cfg: Config | None = None):
    """Construct the coordinator app, ensuring the queue DB directory exists."""
    cfg = cfg or load_config()
    Path(cfg.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    queue = TaskQueue(Path(cfg.db_path).expanduser())
    return create_app(queue, token=cfg.token, sweep_interval=cfg.sweep_interval)


def advertise_endpoint(cfg: Config):
    """Write the rendezvous file advertising the coordinator's bound endpoint.

    Additive discovery: clients resolve the coordinator here instead of assuming
    the fixed port (they still fall back to it). Best-effort -- a write failure
    only degrades discovery, never the server. Returns the file path or ``None``.
    """
    try:
        return write_endpoint(run_dir(), "tcp", f"{cfg.host}:{cfg.port}")
    except OSError as exc:
        log.warning("could not write rendezvous file (%s); discovery degraded", exc)
        return None


def serve(cfg: Config | None = None) -> None:
    """Bind and serve the coordinator (blocking)."""
    import uvicorn

    cfg = cfg or load_config()
    try:
        check_bind_safety(cfg)
    except UnsafeBindError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if requires_token_bind(cfg.host):
        log.warning(
            "binding %s exposes the coordinator on all interfaces; a token is set, "
            "but ensure the port is firewalled off the LAN (allow loopback + the "
            "Docker bridge subnets only)",
            cfg.host,
        )
    # Advertise the bound endpoint for discovery (see the endpoint-rendezvous lib
    # and docs/patterns/local-endpoint-discovery.md). Additive: un-updated clients
    # still reach the fixed port.
    advertise_endpoint(cfg)
    try:
        uvicorn.run(build_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
    finally:
        clear_endpoint(run_dir())
