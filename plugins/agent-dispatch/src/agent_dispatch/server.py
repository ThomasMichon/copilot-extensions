"""Run the coordinator with uvicorn (the ``agent-dispatch serve`` command)."""

from __future__ import annotations

import logging
import os
import socket
import sys
from dataclasses import replace
from pathlib import Path

from . import telemetry
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
    # Install a telemetry sink if one is configured (generic open hook; a no-op
    # unless a consumer wired a sink). Prefer a convention-located config file
    # (env-free); fall back to the environment so env-wired deploys don't
    # regress. Fail-open either way.
    if not telemetry.load_sink_from_config():
        telemetry.load_sink_from_env()
    Path(cfg.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    queue = TaskQueue(Path(cfg.db_path).expanduser())
    return create_app(queue, token=cfg.token, sweep_interval=cfg.sweep_interval)


def advertise_endpoint(cfg: Config):
    """Write the rendezvous file advertising the coordinator's bound endpoint.

    Discovery: clients resolve the coordinator here (env override -> file ->
    legacy fixed port). Under Stage C the advertised port is the OS-assigned one,
    so this file is how discovery-capable clients find the dynamic port.
    Best-effort -- a write failure only degrades discovery, never the server.
    Returns the file path or ``None``.
    """
    try:
        return write_endpoint(run_dir(), "tcp", f"{cfg.host}:{cfg.port}")
    except OSError as exc:
        log.warning("could not write rendezvous file (%s); discovery degraded", exc)
        return None


def serve(cfg: Config | None = None) -> None:
    """Bind and serve the coordinator (blocking).

    Stage C: the coordinator binds an **OS-assigned** ephemeral port
    (``127.0.0.1:0``) unless ``AGENT_DISPATCH_PORT`` pins one, reads the *actual*
    bound port back off the listening socket, and advertises **that** in the
    rendezvous file -- so no fixed loopback port is reserved and discovery-capable
    clients follow the real port. ``Config.port`` remains the legacy client
    fallback (fixed 9847) until Stage D retires it.
    """
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
    # Stage C: pre-bind the listening socket ourselves so we can capture the
    # OS-assigned port and advertise the *actual* endpoint before serving. Passing
    # the already-bound socket to uvicorn avoids a fixed-port reservation entirely.
    sock = _bind_listen_socket(cfg.host, _server_bind_port())
    bound_port = sock.getsockname()[1]
    # Advertise the bound endpoint for discovery (see the endpoint-rendezvous lib
    # and docs/patterns/local-endpoint-discovery.md). Additive: discovery-capable
    # clients resolve this dynamic port from the rendezvous file.
    advertise_endpoint(replace(cfg, port=bound_port))
    # Record the *actually-running* version so the launch-path reconciler can tell
    # a lagging live coordinator from an up-to-date on-disk manifest (dotfiles
    # #533). Best-effort; a dead-pid/missing file is treated as absent by readers.
    from .runtime_version import write_running_version

    write_running_version()
    fed_runner = _maybe_start_federation()
    try:
        uvicorn.Server(uvicorn.Config(build_app(cfg), log_level="info")).run(sockets=[sock])
    finally:
        if fed_runner is not None:
            fed_runner.stop()
        clear_endpoint(run_dir())
        sock.close()


def _maybe_start_federation():
    """Start the federation runner alongside the coordinator when a federation
    role is configured (``AGENT_DISPATCH_FEDERATION_ROLE``); return it (so
    :func:`serve` can stop it) or ``None``.

    **Fail-soft:** a misconfiguration (role set but no Gateway / no resolvable
    instance) logs a warning and leaves the coordinator serving *without*
    federation -- federation is an overlay, never a reason to fail the queue. The
    runner's own loop tolerates the coordinator not yet being bound on the first
    tick (it retries), so starting it just before uvicorn is safe."""
    from . import config as _cfg

    if not _cfg.federation_enabled():
        return None
    try:
        from .federation_runner import runner_from_config

        runner = runner_from_config()
        if runner is None:
            return None
        runner.start(interval=_cfg.federation_interval())
        log.info(
            "federation runner started: role=%s instance=%s",
            _cfg.federation_role(),
            _cfg.federation_instance(),
        )
        return runner
    except Exception as exc:
        log.warning("federation runner not started (serving without it): %s", exc)
        return None


def _server_bind_port() -> int:
    """The port the coordinator should bind.

    A pinned ``AGENT_DISPATCH_PORT`` binds that exact port; otherwise ``0`` lets
    the OS assign an ephemeral one (Stage C dynamic bind). This is deliberately
    independent of ``Config.port`` (the *client* fallback, still fixed 9847 until
    Stage D) so the server drops the fixed reservation without breaking clients.
    """
    pinned = os.environ.get("AGENT_DISPATCH_PORT")
    if pinned is not None and pinned.strip():
        try:
            return int(pinned)
        except ValueError:
            log.warning(
                "ignoring non-integer AGENT_DISPATCH_PORT=%r; using an OS-assigned port",
                pinned,
            )
    return 0


def _bind_listen_socket(host: str, port: int) -> socket.socket:
    """Bind and return a listening TCP socket for ``host:port``.

    With ``port == 0`` the OS assigns an ephemeral port, read back via
    ``getsockname``. Uses ``getaddrinfo`` so an IPv4 or IPv6 bind host both work.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    family, socktype, proto, _canon, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(sockaddr)
    except OSError:
        sock.close()
        raise
    return sock
