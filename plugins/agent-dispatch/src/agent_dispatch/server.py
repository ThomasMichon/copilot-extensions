"""Run the coordinator with uvicorn (the ``agent-dispatch serve`` command)."""

from __future__ import annotations

import logging
import os
import socket
import sys
from dataclasses import replace
from pathlib import Path

from . import telemetry
from . import __version__
from .config import Config, load_config, requires_token_bind, routing_dir, run_dir
from .coordinator import create_app
from .queue import TaskQueue
from .rendezvous import clear_endpoint, write_endpoint

log = logging.getLogger("agent-dispatch.server")

# Set once this coordinator process has logged a START lifecycle record, so the
# shutdown path emits a matching STOP even after a cutover demotes us. One
# coordinator per process, so a module-level flag is sufficient.
_lifecycle_started = False


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


def _publish_routing(cfg: Config, bound_port: int, *, passive: bool = False) -> None:
    """Publish this coordinator into the shared zdd routing table (best-effort).

    A passive cutover instance must NOT seize the active route until the
    orchestrator flips it on promotion (invariant #5), so it publishes nothing.
    """
    if passive:
        return
    try:
        from zdd import routing

        try:
            # Dead-port watchdog: retire any advertised-but-dead endpoint a prior
            # crashed coordinator/cutover left before we announce ourselves.
            routing.reap_stale_active(routing_dir(), service="agent-dispatch")
        except Exception:
            log.debug("startup dead-port sweep skipped", exc_info=True)

        routing.publish_active(
            routing_dir(),
            bind=cfg.host,
            port=bound_port,
            pid=os.getpid(),
            version=__version__,
            demote_existing=True,
        )
        try:
            from zdd import lifecycle

            rec = lifecycle.record(
                routing_dir(), lifecycle.START, service="agent-dispatch",
                outcome=lifecycle.OK, version=__version__, port=bound_port,
            )
            # Remember we logged START -- only if it was actually written (record
            # is fail-open and returns None on failure) -- so shutdown emits a
            # matching STOP even if a later cutover demotes us, and never a STOP
            # without a START.
            if rec is not None:
                global _lifecycle_started
                _lifecycle_started = True
        except Exception:
            log.debug("start lifecycle record skipped", exc_info=True)
    except Exception as exc:  # noqa: BLE001 -- routing is additive, never fatal
        log.warning("could not publish zdd routing table (%s); discovery degraded", exc)


def _clear_routing() -> None:
    """Retract our active entry from the zdd routing table on shutdown (best-effort).

    Only clears when we are still the recorded active (a successor that already
    flipped the table is left untouched), so a clean exit never blanks a newer
    coordinator's route.
    """
    try:
        from zdd import routing

        # STOP iff this process logged START -- so start/stop pair up regardless
        # of whether a later cutover demoted us (active -> previous). A passive
        # instance that never published logged no START and emits no STOP.
        if _lifecycle_started:
            try:
                from zdd import lifecycle

                lifecycle.record(
                    routing_dir(), lifecycle.STOP, service="agent-dispatch",
                    outcome=lifecycle.OK,
                )
            except Exception:
                log.debug("stop lifecycle record skipped", exc_info=True)
        # Retract our active entry (no-op if a successor already flipped the
        # table -- clear_if_owner only clears when we are still the active).
        routing.clear_if_owner(routing_dir(), os.getpid())
    except Exception:
        log.debug("zdd routing clear-on-shutdown skipped", exc_info=True)


def serve(cfg: Config | None = None, *, passive: bool = False) -> None:
    """Bind and serve the coordinator (blocking).

    Stage C: the coordinator binds an **OS-assigned** ephemeral port
    (``127.0.0.1:0``) unless ``AGENT_DISPATCH_PORT`` pins one, reads the *actual*
    bound port back off the listening socket, and advertises **that** in the
    rendezvous file -- so no fixed loopback port is reserved and discovery-capable
    clients follow the real port. ``Config.port`` remains the legacy client
    fallback (fixed 9847) until Stage D retires it.

    When ``passive`` is set (a graceful-cutover passive instance, spawned by the
    installer's in-process cutover), the coordinator serves the full app on a
    fresh port but does **not** publish the zdd routing table -- the cutover
    orchestrator flips the route to it only after it health-gates, so it never
    seizes the active route from the live coordinator (invariant #5).
    """
    import uvicorn

    # A long-lived daemon must never hold the Copilot plugin payload dir as its
    # CWD (on Windows that locks it against `copilot plugin update`, os error 32).
    # It is lazy-started from a session-start hook and inherits that session's CWD,
    # so relocate to the runtime root before we block. See procutil.relocate_off_payload.
    from . import procutil
    procutil.relocate_off_payload()

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
    # Publish the zdd routing table (skipped while passive) so clients follow this
    # generation across a graceful cutover (docs/patterns/graceful-daemon-cutover.md).
    _publish_routing(cfg, bound_port, passive=passive)
    # Record the *actually-running* version so the launch-path reconciler can tell
    # a lagging live coordinator from an up-to-date on-disk manifest (dotfiles
    # #533). Best-effort; a dead-pid/missing file is treated as absent by readers.
    from .runtime_version import write_running_version

    write_running_version()
    fed_runner = _maybe_start_federation()
    app = build_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, log_level="info"))
    # Expose the uvicorn server so the app's /shutdown drain-seam can request a
    # clean exit when the cutover orchestrator retires this daemon.
    app.state.uvicorn_server = server
    try:
        server.run(sockets=[sock])
    finally:
        if fed_runner is not None:
            fed_runner.stop()
        _clear_routing()
        clear_endpoint(run_dir())
        sock.close()


#: (reserved) -- the live uvicorn server is attached to ``app.state.uvicorn_server``
#: in :func:`serve` so the coordinator's /shutdown route can request a clean exit.


def _maybe_start_federation():
    """Start the federation runner alongside the coordinator when a federation
    role is configured (``AGENT_DISPATCH_FEDERATION_ROLE``); return it (so
    :func:`serve` can stop it) or ``None``.

    **Fail-soft:** a misconfiguration (role set but no hosted coordinator / no resolvable
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
