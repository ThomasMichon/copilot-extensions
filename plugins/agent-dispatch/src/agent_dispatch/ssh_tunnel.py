"""SSH port-forward failover transport for the dispatch client.

When this environment's local coordinator is down, a client can reach a **peer**
machine's coordinator over an SSH local port-forward rather than a hosted HTTP
endpoint behind a bearer. The peer's coordinator is loopback-only and tokenless
(it trusts loopback); the **SSH key is the identity** (on-device key auth), so
no shared secret is needed. The caller keeps its own local repo/worktree context
and only the coordinator *transport* is redirected -- the right shape for
failover (contrast :mod:`agent_dispatch.remote_dispatch`, which runs a command
**on** the peer for embody/peer-browse).

Flow (:func:`open_coordinator_tunnel`):

1. Resolve the peer's live coordinator endpoint over SSH
   (``ssh <alias> agent-dispatch print-endpoint`` -> ``host:port``). The peer's
   coordinator binds an OS-assigned dynamic loopback port (Stage C), so it must
   be discovered, not assumed.
2. Open ``ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote> <alias>`` and wait for
   the local end to accept connections.
3. Hand back the loopback base URL + a closer that tears the tunnel down. The
   :class:`~agent_dispatch.client.DispatchClient` owns the closer, so the tunnel
   lives exactly as long as the client (every ``with _client(...)`` command).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

from .procutil import (
    run_ssh_capture,
    ssh_subprocess_kwargs,
    terminate_ssh_process_tree,
)


class TunnelUnavailable(RuntimeError):
    """Raised when the SSH failover tunnel cannot be established."""


def _ssh_alias(machine: str) -> str:
    """The SSH alias for ``machine`` -- lowercased (SSH ``Host`` blocks are
    lowercase by convention; a display-cased name still matches)."""
    return machine.strip().lower()


def _pick_local_port() -> int:
    """An unused loopback TCP port (bind :0, read the assignment, release).

    A small race window between release and the SSH forward's re-bind is
    tolerable: the port is only just-freed and the forward re-binds immediately.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_accepts(port: int, *, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def resolve_peer_endpoint(machine: str, *, timeout: float = 15.0) -> tuple[str, int]:
    """Resolve the peer's live coordinator ``(host, port)`` over SSH.

    Primary: run ``agent-dispatch print-endpoint`` on the peer (its own
    coordinator resolution). Fallback (for a peer on an older build without that
    command): read the peer's zdd routing table (``~/.agent-dispatch/active.json``)
    and take ``active.{bind,port}``. Raises :class:`TunnelUnavailable` when
    neither yields a live endpoint.
    """
    exe = shutil.which("ssh")
    if exe is None:
        raise TunnelUnavailable("ssh not found on PATH")
    alias = _ssh_alias(machine)
    # Primary: the print-endpoint command (clean, version-gated).
    try:
        return _parse_endpoint(_ssh_capture(exe, alias, "agent-dispatch print-endpoint", timeout))
    except TunnelUnavailable:
        pass
    # Fallback: parse the routing table directly (version-agnostic).
    try:
        raw = _ssh_capture(exe, alias, "cat ~/.agent-dispatch/active.json", timeout)
        active = (json.loads(raw) or {}).get("active") or {}
        port = active.get("port")
        if isinstance(port, int) and port > 0:
            return (active.get("bind") or "127.0.0.1"), port
    except (TunnelUnavailable, ValueError, TypeError):
        pass
    raise TunnelUnavailable(
        f"could not resolve {machine!r} coordinator (no print-endpoint, no routing table)"
    )


def _ssh_capture(exe: str, alias: str, remote_cmd: str, timeout: float) -> str:
    """Run ``remote_cmd`` on ``alias`` over SSH (BatchMode) and return stdout.

    Raises :class:`TunnelUnavailable` on ssh error / non-zero exit."""
    cmd = [exe, "-o", "BatchMode=yes", alias, remote_cmd]
    proc = run_ssh_capture(cmd, timeout=timeout)
    if proc is None:
        raise TunnelUnavailable(f"ssh to {alias!r} failed or timed out")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise TunnelUnavailable(f"ssh {remote_cmd!r} on {alias!r} failed: {detail}")
    return proc.stdout


def _parse_endpoint(text: str) -> tuple[str, int]:
    """Parse ``host:port`` (optionally a full ``http://host:port`` URL) from the
    peer's ``print-endpoint`` stdout."""
    token = (text or "").strip().splitlines()[-1].strip() if (text or "").strip() else ""
    if not token:
        raise TunnelUnavailable("peer returned an empty coordinator endpoint")
    hostport = token
    if "://" in hostport:
        hostport = hostport.split("://", 1)[1]
    hostport = hostport.rstrip("/")
    host, sep, port = hostport.rpartition(":")
    if not sep or not port.isdigit():
        raise TunnelUnavailable(f"unparseable coordinator endpoint: {token!r}")
    return (host or "127.0.0.1"), int(port)


@dataclass
class CoordinatorTunnel:
    """A live SSH port-forward to a peer's coordinator + its base URL."""

    base_url: str
    _proc: subprocess.Popen
    _machine: str

    def close(self) -> None:
        """Terminate the SSH forward (idempotent)."""
        proc = self._proc
        if proc.poll() is None:
            terminate_ssh_process_tree(proc)


def open_coordinator_tunnel(
    machine: str, *, ready_timeout: float = 10.0
) -> CoordinatorTunnel:
    """Open an SSH local port-forward to ``machine``'s loopback coordinator.

    Resolves the peer's dynamic coordinator port over SSH, holds an ``ssh -N -L``
    forward open, and **verifies it reaches a live coordinator** (a ``/health``
    probe through the forward) before returning. The peer's coordinator binds an
    OS-assigned port that moves on a zero-downtime cutover, so the port resolved a
    moment ago can be stale by the time the forward is up; on a failed probe the
    resolve+open is retried once with a fresh port. Raises
    :class:`TunnelUnavailable` if ssh is missing, the endpoint can't be resolved,
    or a live coordinator can't be reached within the attempts.
    """
    exe = shutil.which("ssh")
    if exe is None:
        raise TunnelUnavailable("ssh not found on PATH")
    last_err: str = ""
    for _attempt in range(2):
        tunnel = _open_forward_once(exe, machine, ready_timeout=ready_timeout)
        if tunnel is None:
            last_err = "ssh forward did not become ready"
            continue
        if _coordinator_reachable(tunnel.base_url):
            return tunnel
        # The forward is up but no live coordinator answers -- the resolved port
        # went stale (a cutover between resolve and connect). Drop it and retry
        # with a freshly-resolved port.
        last_err = "forward reached no live coordinator (stale port?)"
        tunnel.close()
    raise TunnelUnavailable(f"SSH failover to {machine!r} failed: {last_err}")


def _open_forward_once(
    exe: str, machine: str, *, ready_timeout: float
) -> CoordinatorTunnel | None:
    """Resolve the peer endpoint and open one ``ssh -N -L`` forward.

    Returns a :class:`CoordinatorTunnel` once the local end accepts connections,
    or ``None`` if the forward didn't come up within ``ready_timeout``. Raises
    :class:`TunnelUnavailable` only for a hard resolve/ssh error.
    """
    remote_host, remote_port = resolve_peer_endpoint(machine)
    local_port = _pick_local_port()
    cmd = [
        exe,
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        "-L",
        f"127.0.0.1:{local_port}:{remote_host}:{remote_port}",
        _ssh_alias(machine),
    ]
    proc = subprocess.Popen(  # noqa: S603 -- fixed argv, exe via shutil.which
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        **ssh_subprocess_kwargs(),
    )
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = (proc.stderr.read() if proc.stderr else "").strip()
            raise TunnelUnavailable(
                f"ssh forward to {machine!r} exited early: {err or f'exit {proc.returncode}'}"
            )
        if _port_accepts(local_port):
            return CoordinatorTunnel(
                base_url=f"http://127.0.0.1:{local_port}", _proc=proc, _machine=machine
            )
        time.sleep(0.1)
    # Timed out: tear the half-open forward down before returning None.
    terminate_ssh_process_tree(proc)
    return None


def _coordinator_reachable(base_url: str, *, timeout: float = 4.0) -> bool:
    """True if ``GET {base_url}/health`` returns 2xx through the forward.

    ``_port_accepts`` only proves the *local* forward end accepts a TCP
    connection (ssh binds it regardless of the remote's health); a live-coordinator
    probe confirms the forwarded connection actually reaches a coordinator, which
    is what distinguishes a good tunnel from one pointed at a just-vacated port.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=timeout) as resp:  # noqa: S310 -- fixed loopback URL
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False
