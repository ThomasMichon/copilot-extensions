"""Runtime configuration for the agent-dispatch coordinator and CLI.

All values come from the environment so the same code runs loopback-only on a
lone dev box or against a designated coordinator host on a shared network:

- ``AGENT_DISPATCH_HOST`` / ``AGENT_DISPATCH_PORT`` -- where the coordinator binds.
- ``AGENT_DISPATCH_DB`` -- the SQLite queue file (server side).
- ``AGENT_DISPATCH_TOKEN`` -- optional bearer token (server validates, client sends).
- ``AGENT_DISPATCH_CONTROL_TOKEN`` -- separate coordinator control bearer
  required to activate or transition managed producer scopes.
- ``AGENT_DISPATCH_GC_INTERVAL`` -- seconds between automatic **liveness
  garbage-collection** passes (server side; ``0`` disables). A GC pass requeues a
  held task only when its owner worktree is *confirmed gone* -- not on elapsed
  time. ``AGENT_DISPATCH_SWEEP_INTERVAL`` is a **deprecated alias** kept for one
  release (the recovery mechanism moved from lease expiry to liveness).
- ``AGENT_DISPATCH_URL`` -- the coordinator base URL the CLI talks to (defaults to
  ``http://<host>:<port>``); set this to point the CLI at a remote coordinator.
- ``AGENT_DISPATCH_SHARED_URL`` -- the **shared/elected coordinator** endpoint used
  for cross-machine dispatch (multi-machine binding: the always-on hosted-coordinator endpoint).
  A client keeps its **local** loopback coordinator for same-machine work and
  reaches this one only when it opts in (``--shared``), so the single-machine /
  works-with-no-service property is preserved (hybrid topology).
- ``AGENT_DISPATCH_SHARED_TOKEN`` -- bearer token for the shared coordinator
  (independent of the local ``AGENT_DISPATCH_TOKEN``; per-client, as the shared
  endpoint is exposed only through the secured mesh).
- ``AGENT_DISPATCH_SHARED_CONTROL_TOKEN`` -- control bearer for managed producer
  transitions on the shared coordinator.
- ``AGENT_DISPATCH_PRODUCER_CAPABILITY_COMMAND`` -- preferred on-demand command
  that prints the current producer capability; the raw
  ``AGENT_DISPATCH_PRODUCER_CAPABILITY`` remains a fallback.
- ``AGENT_DISPATCH_NO_AUTOSTART`` -- set to any value to disable the CLI's
  lazy on-demand coordinator start (a client command that finds no live local
  coordinator otherwise starts one detached, then proceeds).
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9847
DEFAULT_DB = Path.home() / ".agent-dispatch" / "tasks.db"
DEFAULT_SWEEP_INTERVAL = 60.0

#: Minimum age (seconds) before an UNOWNED proposed/queued task pinned to a
#: no-longer-live target worktree is reaped by the liveness GC (see
#: ``TaskQueue.reap_orphaned_targets``). Generous by default so a freshly-stored
#: handoff whose successor hasn't started is never reaped; ``0`` reaps as soon as
#: the worktree is gone. Env override: ``AGENT_DISPATCH_ORPHAN_GRACE``.
DEFAULT_ORPHAN_GRACE = 86400.0  # 24h

# Discovery: the coordinator advertises its bound endpoint in a rendezvous file
# under this runtime dir; clients resolve it there (env override -> file -> the
# legacy fixed port). Honors overrides so a branded/side-by-side deployment keeps
# its own namespace. See docs/patterns/local-endpoint-discovery.md.
RUN_DIR_ENV = "AGENT_DISPATCH_RUN_DIR"
ROUTING_DIR_ENV = "AGENT_DISPATCH_ROUTING_DIR"
ENDPOINT_ENV = "AGENT_DISPATCH_ENDPOINT"
OVERRIDES_ENV = "AGENT_DISPATCH_OVERRIDES"
# Opt-in: keep a WSL guest a *client* of the Windows host's coordinator (the
# pre-per-environment behavior). Default (unset) means a WSL guest runs and
# resolves its OWN coordinator, coexisting with the Windows one via dynamic ports.
WSL_WINDOWS_CLIENT_ENV = "AGENT_DISPATCH_WSL_WINDOWS_CLIENT"


def wsl_windows_client() -> bool:
    """True when a WSL guest is explicitly opted in to remain a *client* of the
    Windows host's coordinator (via ``AGENT_DISPATCH_WSL_WINDOWS_CLIENT``).

    Per-execution-environment ownership makes each environment run its own
    coordinator: a WSL guest, by default, owns and resolves its **own**
    coordinator (on an OS-assigned dynamic port), coexisting with the Windows
    host's. A box that deliberately wants the old cross-mount client behavior
    sets this opt-in.
    """
    return os.environ.get(WSL_WINDOWS_CLIENT_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def run_dir() -> Path:
    """The runtime dir that holds the rendezvous (endpoint) file."""
    return Path(os.environ.get(RUN_DIR_ENV) or (Path.home() / ".agent-dispatch" / "run"))


def overrides_path() -> Path:
    """The local, out-of-band operator-override store for supervised units.

    A machine-local JSON file (``~/.agent-dispatch/overrides.json``) mapping a
    supervised unit's **registration id** to an override record. It is deliberately
    *not* under any repo, so a repo re-sync can never quietly undo an override; the
    single supervisor daemon subtracts overridden-off ids from its desired set each
    reconcile, and the ``supervise override`` CLI reads/writes it. Honors
    ``AGENT_DISPATCH_OVERRIDES`` for side-by-side / test deployments (kept beside the
    run dir so a branded namespace carries its overrides too)."""
    return Path(
        os.environ.get(OVERRIDES_ENV)
        or (Path.home() / ".agent-dispatch" / "overrides.json")
    )


def routing_dir() -> Path:
    """Stable zdd routing-table directory shared by all installed versions.

    The graceful daemon-cutover (docs/patterns/graceful-daemon-cutover.md) flips a
    file-based routing table (``active.json``) here so a version update stands the
    new coordinator up beside the old and moves clients over without a restart.
    It lives at the install root (never a version slot) so it survives every swap.
    Honors ``AGENT_DISPATCH_ROUTING_DIR`` for side-by-side / test deployments
    (mirrors ``run_dir`` / ``overrides_path``), so an isolated or branded namespace
    does not read the real install's routing table.
    """
    return Path(
        os.environ.get(ROUTING_DIR_ENV) or (Path.home() / ".agent-dispatch")
    )

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
    control_token: str | None = None
    sweep_interval: float = DEFAULT_SWEEP_INTERVAL
    orphan_grace: float = DEFAULT_ORPHAN_GRACE

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
        control_token=os.environ.get("AGENT_DISPATCH_CONTROL_TOKEN") or None,
        sweep_interval=float(
            os.environ.get("AGENT_DISPATCH_GC_INTERVAL")
            or os.environ.get("AGENT_DISPATCH_SWEEP_INTERVAL")
            or str(DEFAULT_SWEEP_INTERVAL)
        ),
        orphan_grace=float(
            os.environ.get("AGENT_DISPATCH_ORPHAN_GRACE") or str(DEFAULT_ORPHAN_GRACE)
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


def _url_listening(base_url: str, *, timeout: float = 0.25) -> bool:
    """True if a TCP listener accepts a connection at ``base_url``'s host:port.

    The zdd routing table deliberately returns a **mid-startup** coordinator's
    endpoint (``read_active_endpoint`` keeps a live-pid/no-listener-yet entry so a
    racing client addresses the NEW generation, not the old). That is correct for
    *addressing* (``client_url``) but not for *liveness*: a client that treats the
    routed endpoint as reachable would connect before the socket is accepting and
    get ``Connection refused``. So liveness must probe the actual socket.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
        host, port = parsed.hostname, parsed.port
    except (ValueError, TypeError):
        return False
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def has_live_local_coordinator() -> bool:
    """True if a local coordinator is discoverable **and** answering its probe.

    Consults the zdd routing table first (authoritative on the host; it self-heals
    a dead ``active`` to ``previous``), then the legacy discovery ladder
    (``AGENT_DISPATCH_ENDPOINT`` -> the rendezvous file, probed for a live
    listener). The CLI's lazy-start uses this to decide whether a coordinator is
    up before a client command runs, so it must mean **actually reachable**: the
    routing table alone returns a mid-startup (live-pid, not-yet-listening)
    endpoint, so the routed URL is additionally socket-probed here -- otherwise
    lazy-start would stop waiting the instant a just-spawned coordinator wrote its
    routing entry and the very next client call would race the bind and get
    ``Connection refused``.
    """
    routed = _routing_url()
    if routed is not None and _url_listening(routed):
        return True
    return _discover_local_endpoint() is not None


def client_url() -> str:
    """The base URL the CLI should talk to.

    Resolution order:

    1. ``AGENT_DISPATCH_URL`` -- explicit operator override.
    2. On a **WSL guest opted in** to Windows-client mode
       (``AGENT_DISPATCH_WSL_WINDOWS_CLIENT``), resolve the Windows-owned
       coordinator dynamically (probe ``127.0.0.1`` for mirrored, then the
       default gateway for NAT; cached best-effort), taking the **port from the
       rendezvous file** (the Windows-side ``endpoint.json``) when present, else
       the fixed default.
    3. Otherwise -- standalone Linux, the Windows host, **or (by default) a WSL
       guest**, each owning its own per-environment coordinator -- the
       **discovered** local endpoint (zdd routing table -> ``AGENT_DISPATCH_ENDPOINT``
       -> rendezvous file), falling back to the fixed ``http://127.0.0.1:9847``.
    """
    override = os.environ.get("AGENT_DISPATCH_URL")
    if override:
        return override
    cfg = load_config()
    try:
        from .netinfo import is_wsl, resolve_wsl_client_url

        if is_wsl() and wsl_windows_client():
            # Opt-in only: this WSL guest is a client of the Windows-owned
            # coordinator (legacy cross-mount discovery). By default a WSL guest
            # falls through and resolves its OWN local coordinator, exactly like a
            # standalone Linux host or the Windows host itself.
            return resolve_wsl_client_url(_discovered_wsl_port(cfg.port))
        # Standalone Linux, the Windows host, AND (by default) a WSL guest: the
        # zdd routing table is the authority -- a graceful cutover flips it to the
        # new coordinator generation, so clients follow the live port without a
        # restart. Fall back to the rendezvous file (legacy discovery) when no
        # routing table is published yet.
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


def client_control_token() -> str | None:
    """The separate control bearer for managed producer transitions."""
    return os.environ.get("AGENT_DISPATCH_CONTROL_TOKEN") or None


def failover_machine() -> str | None:
    """The peer machine (its SSH alias) to fail dispatch over to when this
    environment's local coordinator is down (``AGENT_DISPATCH_FAILOVER_MACHINE``).

    This is the **SSH-transport** failover: rather than a hosted HTTP endpoint
    behind a bearer, the client opens an SSH local port-forward to the peer's
    loopback coordinator (authenticated by the machine's own SSH key -- no token)
    and runs the command against it, keeping the caller's local repo/worktree
    context. ``None`` when unset -- the client is then local-only (or uses the
    hosted ``AGENT_DISPATCH_SHARED_URL`` fallback, if configured). Preferred over
    the hosted HTTP fallback when both are set: per-machine identity, no shared
    secret.
    """
    return os.environ.get("AGENT_DISPATCH_FAILOVER_MACHINE") or None


def shared_url() -> str | None:
    """The **shared/elected coordinator** base URL for cross-machine dispatch.

    ``AGENT_DISPATCH_SHARED_URL`` (multi-machine binding: the always-on hosted-coordinator
    endpoint). ``None`` when no shared coordinator is configured -- the client is
    then local-only and a ``--shared`` command errors loudly rather than silently
    falling back to the local queue (which would strand a cross-machine task).
    """
    return os.environ.get("AGENT_DISPATCH_SHARED_URL") or None


def shared_token() -> str | None:
    """The bearer token for the shared coordinator, if any.

    Independent of the local ``AGENT_DISPATCH_TOKEN`` (``AGENT_DISPATCH_SHARED_TOKEN``):
    the two coordinators authenticate separately -- the shared one is exposed only
    through the secured mesh atop its own per-client bearer.

    Resolved from ``AGENT_DISPATCH_SHARED_TOKEN`` when set; otherwise, if
    ``AGENT_DISPATCH_SHARED_TOKEN_COMMAND`` is set, by running that command and
    using its stdout. The command indirection lets a deployment fetch the secret
    **on demand** from an external store (e.g. a credential/vault CLI) so it is
    never persisted in the environment -- fetch, use, let go. Returns ``None``
    when neither is set, or when the command fails or yields nothing.
    """
    direct = os.environ.get("AGENT_DISPATCH_SHARED_TOKEN")
    if direct:
        return direct
    command = os.environ.get("AGENT_DISPATCH_SHARED_TOKEN_COMMAND")
    if command:
        return _run_token_command(command)
    return None


def shared_control_token() -> str | None:
    """The control bearer for managed transitions on the shared coordinator."""
    direct = os.environ.get("AGENT_DISPATCH_SHARED_CONTROL_TOKEN")
    if direct:
        return direct
    command = os.environ.get("AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND")
    if command:
        return _run_token_command(command)
    return None


def producer_capability() -> str | None:
    """Resolve the current managed-producer capability on demand.

    Command indirection is preferred so the capability need not persist in the
    environment. The raw environment value remains a compatibility fallback,
    including when the configured fetch command fails or returns no value.
    """
    command = os.environ.get("AGENT_DISPATCH_PRODUCER_CAPABILITY_COMMAND")
    if command:
        fetched = _run_token_command(command)
        if fetched:
            return fetched
    return os.environ.get("AGENT_DISPATCH_PRODUCER_CAPABILITY") or None


def _run_token_command(command: str) -> str | None:
    """Run a token-fetch command and return its stdout (stripped), or ``None``.

    ``command`` is parsed with :func:`shlex.split` and run **without a shell**
    (fixed argv), so it handles quoted arguments (e.g. a vault entry name with
    spaces) without exposing shell metacharacters. Any failure -- unparseable
    command, non-zero exit, timeout, empty output -- degrades to ``None`` so a
    missing/broken fetcher never crashes the CLI; the request then proceeds
    tokenless and fails loudly at the coordinator if a token was required.
    """
    import shlex
    import subprocess

    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv (shlex.split), no shell
            argv, check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    token = (proc.stdout or "").strip()
    return token or None


# -- federation (relay-rendezvous directory + fenced-epoch lease) -------------
#
# Federation lets the operator's coordinators federate across machines over a
# rendezvous directory (the fleet directory served by the shared/hosted
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
