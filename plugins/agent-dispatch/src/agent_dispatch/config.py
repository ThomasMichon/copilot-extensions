"""Runtime configuration for the agent-dispatch coordinator and CLI.

All values come from the environment so the same code runs loopback-only on a
lone dev box or against a designated coordinator host on a shared network:

- ``AGENT_DISPATCH_HOST`` / ``AGENT_DISPATCH_PORT`` -- where the coordinator binds.
- ``AGENT_DISPATCH_DB`` -- the SQLite queue file (server side).
- ``AGENT_DISPATCH_TOKEN`` -- optional bearer token (server validates, client sends).
- ``AGENT_DISPATCH_GC_INTERVAL`` -- seconds between automatic **liveness
  garbage-collection** passes (server side; ``0`` disables). A GC pass requeues a
  held task only when its owner worktree is *confirmed gone* -- not on elapsed
  time. ``AGENT_DISPATCH_SWEEP_INTERVAL`` is a **deprecated alias** kept for one
  release (the recovery mechanism moved from lease expiry to liveness).
- ``AGENT_DISPATCH_URL`` -- the coordinator base URL the CLI talks to (defaults to
  ``http://<host>:<port>``); set this to point the CLI at a remote coordinator.
- ``AGENT_DISPATCH_SHARED_URL`` -- the **shared/elected coordinator** endpoint used
  for cross-machine dispatch (facility binding: the always-on gateway endpoint).
  A client keeps its **local** loopback coordinator for same-machine work and
  reaches this one only when it opts in (``--shared``), so the single-machine /
  works-with-no-service property is preserved (hybrid topology).
- ``AGENT_DISPATCH_SHARED_TOKEN`` -- bearer token for the shared coordinator
  (independent of the local ``AGENT_DISPATCH_TOKEN``; per-client, as the shared
  endpoint is exposed only through the gateway/secured mesh).
- ``AGENT_DISPATCH_NO_AUTOSTART`` -- set to any value to disable the CLI's
  lazy on-demand coordinator start (a client command that finds no live local
  coordinator otherwise starts one detached, then proceeds).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9847
DEFAULT_DB = Path.home() / ".agent-dispatch" / "tasks.db"
DEFAULT_SWEEP_INTERVAL = 60.0

# Discovery: the coordinator advertises its bound endpoint in a rendezvous file
# under this runtime dir; clients resolve it there (env override -> file -> the
# legacy fixed port). Honors overrides so a branded/side-by-side deployment keeps
# its own namespace. See docs/patterns/local-endpoint-discovery.md.
RUN_DIR_ENV = "AGENT_DISPATCH_RUN_DIR"
ENDPOINT_ENV = "AGENT_DISPATCH_ENDPOINT"


def run_dir() -> Path:
    """The runtime dir that holds the rendezvous (endpoint) file."""
    return Path(os.environ.get(RUN_DIR_ENV) or (Path.home() / ".agent-dispatch" / "run"))


def routing_dir() -> Path:
    """Stable zdd routing-table directory shared by all installed versions.

    The graceful daemon-cutover (docs/patterns/graceful-daemon-cutover.md) flips a
    file-based routing table (``active.json``) here so a version update stands the
    new coordinator up beside the old and moves clients over without a restart.
    It lives at the install root (never a version slot) so it survives every swap.
    """
    return Path.home() / ".agent-dispatch"

#: Wildcard bind addresses that expose the coordinator on **every** interface
#: (including the LAN). Binding one of these without a bearer token would put the
#: powerful task-control API on the network unauthenticated, so it is guarded
#: (see :func:`requires_token_bind`). A *specific* host-local IP (loopback, a
#: Windows vEthernet(WSL) address, a Docker bridge gateway) is the operator's
#: deliberate choice of a non-LAN interface and is **not** guarded here.
WILDCARD_BIND_HOSTS = frozenset({"0.0.0.0", "::", "[::]"})  # noqa: S104 -- guarded, not bound blindly


def requires_token_bind(host: str) -> bool:
    """True if binding ``host`` exposes the API on all interfaces (the LAN), so a
    bearer token must be present before serving."""
    return (host or "").strip() in WILDCARD_BIND_HOSTS


@dataclass(frozen=True)
class Config:
    """Resolved coordinator configuration."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_path: str = str(DEFAULT_DB)
    token: str | None = None
    sweep_interval: float = DEFAULT_SWEEP_INTERVAL

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def load_config() -> Config:
    """Resolve the coordinator config from the environment."""
    return Config(
        host=os.environ.get("AGENT_DISPATCH_HOST", DEFAULT_HOST),
        port=int(os.environ.get("AGENT_DISPATCH_PORT", str(DEFAULT_PORT))),
        db_path=os.environ.get("AGENT_DISPATCH_DB", str(DEFAULT_DB)),
        token=os.environ.get("AGENT_DISPATCH_TOKEN") or None,
        sweep_interval=float(
            os.environ.get("AGENT_DISPATCH_GC_INTERVAL")
            or os.environ.get("AGENT_DISPATCH_SWEEP_INTERVAL")
            or str(DEFAULT_SWEEP_INTERVAL)
        ),
    )


def _windows_run_dirs() -> list[Path]:
    """Candidate Windows-side agent-dispatch runtime dirs, seen from WSL via ``/mnt/c``.

    A WSL guest has no local coordinator; the Windows host owns it and advertises
    its endpoint under ``%USERPROFILE%\\.agent-dispatch\\run``, visible from WSL at
    ``/mnt/c/Users/<user>/.agent-dispatch/run``. Honors ``AGENT_DISPATCH_WINDOWS_RUN_DIR``;
    else globs the mounted Windows profiles (skipping system profiles), newest
    ``endpoint.json`` first.
    """
    override = os.environ.get("AGENT_DISPATCH_WINDOWS_RUN_DIR")
    if override:
        return [Path(override)]
    mount = os.environ.get("AGENT_DISPATCH_WINDOWS_MOUNT", "/mnt/c")
    users = Path(mount) / "Users"
    skip = {"public", "default", "default user", "all users"}
    candidates: list[tuple[float, Path]] = []
    try:
        for profile in users.iterdir():
            if profile.name.lower() in skip:
                continue
            ep = profile / ".agent-dispatch" / "run" / "endpoint.json"
            try:
                mtime = ep.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, ep.parent))
    except OSError:
        return []
    candidates.sort(reverse=True)
    return [d for _, d in candidates]


def _discovered_wsl_port(default_port: int) -> int:
    """The coordinator port a WSL client should use: the ``AGENT_DISPATCH_ENDPOINT``
    override, else the Windows-side rendezvous file, else ``default_port``. The host
    is resolved separately by ``netinfo`` (mirrored -> 127.0.0.1, NAT -> gateway)."""
    from . import rendezvous

    override = os.environ.get(ENDPOINT_ENV)
    if override:
        try:
            ep = rendezvous.Endpoint.parse(override)
            if ep.transport == "tcp":
                return ep.tcp_host_port[1]
        except ValueError:
            pass
    for d in _windows_run_dirs():
        ep = rendezvous.read_endpoint(d)
        if ep is not None and ep.transport == "tcp":
            try:
                return ep.tcp_host_port[1]
            except ValueError:
                continue
    return default_port


def _routing_url() -> str | None:
    """The zdd routing-table active coordinator URL, or ``None``.

    The graceful daemon-cutover flips ``active.json`` (routing_dir) so clients
    follow a new coordinator generation without a restart. This is the authority
    on the coordinator *host*; it self-heals a dead ``active`` to ``previous``.
    Defensive: any failure (zdd absent, table missing/corrupt) returns ``None`` so
    resolution falls through to the legacy rendezvous ladder.
    """
    try:
        from zdd.routing import read_active_endpoint

        ep = read_active_endpoint(routing_dir())
    except Exception:
        return None
    return ep.base_url if ep is not None else None


def _discover_local_endpoint():
    """The coordinator endpoint from the local discovery ladder, or ``None``.

    ``AGENT_DISPATCH_ENDPOINT`` override -> the local rendezvous file (this host's
    coordinator). Returns ``None`` when nothing is discovered so the caller uses
    the fixed default.
    """
    from . import rendezvous

    override = os.environ.get(ENDPOINT_ENV)
    try:
        return rendezvous.resolve(run_dir(), override=override, probe=rendezvous.connect_probe)
    except rendezvous.EndpointUnavailable:
        return None


def has_live_local_coordinator() -> bool:
    """True if a local coordinator is discoverable **and** answering its probe.

    Consults the zdd routing table first (authoritative on the host; it self-heals
    a dead ``active`` to ``previous`` and verifies a live listener), then the
    legacy discovery ladder (``AGENT_DISPATCH_ENDPOINT`` -> the rendezvous file,
    probed for a live listener). The CLI's lazy-start uses this to decide whether
    it must spawn a local coordinator before a client command runs -- so honoring
    the routing table here prevents a spurious second coordinator during/after a
    graceful cutover (when the rendezvous file may momentarily lag the flip).
    """
    if _routing_url() is not None:
        return True
    return _discover_local_endpoint() is not None


def client_url() -> str:
    """The base URL the CLI should talk to.

    Resolution order:

    1. ``AGENT_DISPATCH_URL`` -- explicit operator override.
    2. On a **WSL guest**, resolve the Windows-owned coordinator dynamically
       (probe ``127.0.0.1`` for mirrored, then the default gateway for NAT;
       cached best-effort), taking the **port from the rendezvous file** (the
       Windows-side ``endpoint.json``) when present, else the fixed default. A WSL
       guest depends on the Windows host, which owns the coordinator.
    3. Otherwise (standalone Linux, or the Windows host itself), the **discovered**
       local endpoint (``AGENT_DISPATCH_ENDPOINT`` -> rendezvous file), falling
       back to the fixed ``http://127.0.0.1:9847``.
    """
    override = os.environ.get("AGENT_DISPATCH_URL")
    if override:
        return override
    cfg = load_config()
    try:
        from .netinfo import is_wsl, resolve_wsl_client_url

        if is_wsl():
            return resolve_wsl_client_url(_discovered_wsl_port(cfg.port))
        # On the coordinator host, the zdd routing table is the authority: a
        # graceful cutover flips it to the new coordinator generation, so clients
        # follow the live port without a restart. Fall back to the rendezvous file
        # (legacy discovery) when no routing table is published yet.
        routed = _routing_url()
        if routed:
            return routed
        ep = _discover_local_endpoint()
        if ep is not None and ep.transport == "tcp":
            host, port = ep.tcp_host_port
            return f"http://{host}:{port}"
    except Exception:
        # Detection/probe/discovery failure must never break the CLI -- fall back
        # to the local default and let the actual request fail loud if unreachable.
        return cfg.url
    return cfg.url


def client_token() -> str | None:
    """The bearer token the CLI should send, if any."""
    return os.environ.get("AGENT_DISPATCH_TOKEN") or None


def shared_url() -> str | None:
    """The **shared/elected coordinator** base URL for cross-machine dispatch.

    ``AGENT_DISPATCH_SHARED_URL`` (facility binding: the always-on gateway
    endpoint). ``None`` when no shared coordinator is configured -- the client is
    then local-only and a ``--shared`` command errors loudly rather than silently
    falling back to the local queue (which would strand a cross-machine task).
    """
    return os.environ.get("AGENT_DISPATCH_SHARED_URL") or None


def shared_token() -> str | None:
    """The bearer token for the shared coordinator, if any.

    Independent of the local ``AGENT_DISPATCH_TOKEN`` (``AGENT_DISPATCH_SHARED_TOKEN``):
    the two coordinators authenticate separately -- the shared one is exposed only
    through the gateway/secured mesh atop its own per-client bearer.
    """
    return os.environ.get("AGENT_DISPATCH_SHARED_TOKEN") or None


# -- federation (relay-rendezvous directory + fenced-epoch lease) -------------
#
# Federation lets the operator's coordinators federate across machines over a
# rendezvous directory (the fleet directory served by the shared/Gateway
# coordinator). A node opts in by declaring a *role*; the runtime
# (:class:`~agent_dispatch.federation_runner.FederationRunner`) then keeps it
# present in the directory and -- for a lease-eligible role -- drives the
# fenced-epoch coordinator lease. See the ``agent-dispatch-federation`` effort.

#: The federation roles a node may declare. ``coordinator``/``standby`` are
#: **lease-eligible** (they run the fenced lease; which one is *active* is decided
#: by the lease, not this hint); ``peer``/``satellite`` are presence-only.
FEDERATION_ROLES = frozenset({"peer", "coordinator", "standby", "satellite"})

#: Roles that participate in the fenced-epoch coordinator lease.
FEDERATION_LEASE_ROLES = frozenset({"coordinator", "standby"})

DEFAULT_FEDERATION_INTERVAL = 15.0


def federation_role() -> str | None:
    """This node's declared federation role (``AGENT_DISPATCH_FEDERATION_ROLE``),
    or ``None`` when federation is not enabled. An unrecognized value is treated
    as unset so a typo fails closed rather than silently mis-registering."""
    role = (os.environ.get("AGENT_DISPATCH_FEDERATION_ROLE") or "").strip().lower()
    return role if role in FEDERATION_ROLES else None


def federation_enabled() -> bool:
    """Whether this node participates in federation (a valid role is declared)."""
    return federation_role() is not None


def federation_instance() -> str | None:
    """The stable directory id this node registers under.

    ``AGENT_DISPATCH_FEDERATION_INSTANCE`` when set; otherwise the machine id
    resolved from context (``identity.resolve_identity``), falling back to the
    hostname. ``None`` only when nothing can be resolved (the caller must then
    supply one explicitly)."""
    explicit = (os.environ.get("AGENT_DISPATCH_FEDERATION_INSTANCE") or "").strip()
    if explicit:
        return explicit
    try:
        from .identity import resolve_identity

        machine, _worktree = resolve_identity()
        if machine:
            return machine
    except Exception:
        pass
    import socket

    return socket.gethostname() or None


def federation_interval() -> float:
    """Seconds between federation ticks (``AGENT_DISPATCH_FEDERATION_INTERVAL``).

    A tick must fire comfortably inside both the lease staleness threshold and the
    directory presence TTL; the default is well under both."""
    raw = os.environ.get("AGENT_DISPATCH_FEDERATION_INTERVAL")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_FEDERATION_INTERVAL
