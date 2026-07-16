"""The **Spawner seam** -- the one per-boundary abstraction of the unified
remote-runner design (see the effort's "one Session Host, two seams, one client").

Every "own copilot in a process we don't directly hold" case -- **local**,
**elevated**, **machine-mesh SSH**, **CodeSpace** -- runs the *same* Session Host
component and is driven by the *same* frontend client. The only per-boundary
difference is captured here:

* **how the Host is bootstrapped** on the far side (this module's ``spawn``), and
* **how a local TCP port is made to point at it** (the ``refresh_endpoint``
  closure on the returned :class:`SpawnedHost`).

Because a port-forward makes a remote endpoint look local, the frontend **always**
dials ``127.0.0.1:<local_port>`` and speaks the seq/ack protocol -- there is no
per-boundary transport in the ACP hot path. ``refresh_endpoint`` is what the
liveness-driven reattach driver calls on ``disconnected`` before it redials: a
no-op for a local Host, a re-establish-the-forward for SSH / CodeSpace.

Phase P2a ships this seam with the **local** implementation only (a refactor of
the shipped ``launch_session_host`` path) plus the connect-auth **nonce**;
``ElevatedSpawner`` / ``SshSpawner`` / ``CodeSpaceSpawner`` are additive later
slices that reuse this exact interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .launcher import launch_session_host

log = logging.getLogger("agent-bridge.session-host.spawner")


@dataclass
class SpawnedHost:
    """A launched Session Host + everything the frontend needs to reach it.

    ``local_port`` is always a loopback port on *this* machine (directly bound
    for a local/elevated Host, or the near end of a forward for a remote one).
    ``nonce`` is the connect-auth token to present on ATTACH. ``refresh_endpoint``
    re-establishes the local port before a reattach redial (no-op for local).
    ``endpoint`` is the durable, JSON-serializable descriptor a restarted
    frontend uses to re-forward from :class:`~..session_host.host_index.HostIndex`
    alone (no live Spawner needed) -- empty for a local Host whose port never
    moves. ``forward`` retains a live forward process so it is not GC'd.
    """

    local_port: int
    host_pid: int
    child_pid: int
    protocol_version: int
    boundary: str = "local"
    nonce: str = ""
    state_file: str = ""
    proc: Any = None
    endpoint: dict = field(default_factory=dict)
    forward: Any = None
    _refresh: Callable[[], Awaitable[None]] | None = None

    async def refresh_endpoint(self) -> None:
        """Re-point ``local_port`` at the Host before a reattach redial.

        No-op for a same-machine (local/elevated) Host whose port never moves;
        for a forwarded (SSH/CodeSpace) Host this re-establishes the ``-L``
        forward after a transport drop.
        """
        if self._refresh is not None:
            await self._refresh()


@runtime_checkable
class HostSpawner(Protocol):
    """Bootstraps a Session Host across one boundary and returns how to reach it.

    Implementations differ only in *where* the Host runs and *how* a local port
    is wired to it; the frontend client that drives the returned host is the same
    for all of them.
    """

    boundary: str

    async def spawn(
        self,
        child_argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str = "",
    ) -> SpawnedHost:
        ...


def new_nonce() -> str:
    """A fresh connect-auth nonce (URL-safe, 32 hex chars)."""
    return secrets.token_hex(16)


class LocalSpawner:
    """Spawn a survivable Session Host on **this** machine (the shipped path).

    Wraps :func:`launch_session_host` (run off the event loop) and mints a
    per-Host connect nonce so a stray same-user process cannot drive the child
    by dialing the loopback port. The endpoint is a direct loopback port, so
    ``refresh_endpoint`` is a no-op.
    """

    boundary = "local"

    def __init__(self, *, awkward_reap_seconds: float = 60.0) -> None:
        # Bound on how long an idle, front-less child lingers after an awkward
        # disconnect before the host self-reaps it (#48). Handed to the launched
        # host process. 0 disables the awkward-grace self-reap.
        self._awkward_reap_seconds = awkward_reap_seconds

    async def spawn(
        self,
        child_argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str = "",
    ) -> SpawnedHost:
        nonce = new_nonce()
        handle = await asyncio.to_thread(
            launch_session_host, child_argv, cwd=cwd, env=env, nonce=nonce,
            awkward_reap_seconds=self._awkward_reap_seconds,
        )
        return SpawnedHost(
            local_port=handle.port,
            host_pid=handle.host_pid,
            child_pid=handle.child_pid,
            protocol_version=handle.protocol_version,
            boundary=self.boundary,
            nonce=nonce,
            state_file=handle.state_file,
            proc=handle.proc,
            _refresh=None,
        )


# Env var carrying the connect-auth nonce to the remote host process (kept off
# the command line so it does not leak to ``ps``). Mirrors launcher._NONCE_ENV.
_NONCE_ENV = "AGENT_BRIDGE_SESSION_HOST_NONCE"


@runtime_checkable
class RemoteTransport(Protocol):
    """Far-side operations a remote (CodeSpace / mesh) Spawner needs.

    A concrete transport knows how to move a file to the far side, run a shell
    command there, and describe the SSH config a local ``-L`` forward should use
    to reach it. Everything boundary-specific (``gh codespace cp`` vs ``scp``,
    ``agent-codespaces ssh`` vs ssh-manager exec) lives behind this seam; the
    Spawner orchestration below is transport-agnostic.
    """

    boundary: str

    async def push_file(self, local_path: str, remote_path: str) -> None:
        """Copy a local file to ``remote_path`` on the far side."""
        ...

    async def path_exists(self, remote_path: str) -> bool:
        """True if ``remote_path`` already exists on the far side."""
        ...

    async def run(
        self, command: str, *, timeout: float = 60.0,
    ) -> tuple[int, str, str]:
        """Run a shell command on the far side; return ``(rc, stdout, stderr)``."""
        ...

    def ssh_config(self) -> Any:
        """The :class:`ssh_manager.SSHConfig` a ``-L`` forward should dial."""
        ...


def build_remote_launch(
    bundle_remote: str,
    state_remote: str,
    log_remote: str,
    child_argv: list[str],
    *,
    nonce: str = "",
    cwd: str | None = None,
) -> str:
    """Assemble the far-side bash command that launches a survivable Host.

    ``setsid nohup … </dev/null &`` detaches the Host from the launch SSH channel
    so it **outlives the channel closing** (the POSIX survival seam, validated in
    the #145 live proof), while ``PR_SET_PDEATHSIG`` inside the Host still ties
    the copilot child's life to the Host. The nonce rides in via the env (off the
    command line). Paths are POSIX (the far side is Linux).
    """
    import posixpath

    dirs = " ".join(shlex.quote(posixpath.dirname(p))
                    for p in (state_remote, log_remote) if posixpath.dirname(p))
    prep = f"mkdir -p {dirs}; " if dirs else ""
    host_cmd = (
        f"python3 {shlex.quote(bundle_remote)} --port 0 "
        f"--state-file {shlex.quote(state_remote)} "
    )
    if cwd:
        host_cmd += f"--cwd {shlex.quote(cwd)} "
    host_cmd += "-- " + " ".join(shlex.quote(a) for a in child_argv)
    env_prefix = f"{_NONCE_ENV}={shlex.quote(nonce)} " if nonce else ""
    launch = (
        f"{env_prefix}setsid nohup {host_cmd} "
        f"</dev/null >{shlex.quote(log_remote)} 2>&1 & echo launched"
    )
    return f"bash -lc {shlex.quote(prep + launch)}"


class CodeSpaceSpawner:
    """Bootstrap a survivable Session Host on the far side of a remote boundary.

    Ships the content-hashed host bundle (cache-hit skips re-shipping), launches
    it detached on the far side, reads back the remote port from the Host's state
    file, and stands up an ``ssh -N -L`` forward so the frontend dials
    ``127.0.0.1:<local_port>`` exactly as for a local Host. ``refresh_endpoint``
    re-establishes the forward after a transport drop; ``endpoint`` captures how
    to rebuild it from the host index alone after a frontend restart.

    Boundary-agnostic given a :class:`RemoteTransport`; named for its first
    consumer (CodeSpaces). The mesh ``SshSpawner`` is the same class with an
    ssh-manager-backed transport.
    """

    def __init__(
        self,
        transport: RemoteTransport,
        *,
        remote_dir: str = "/tmp/agent-bridge",  # noqa: S108 -- remote CS path, not a local temp
        reverse_forwards: list[str] | None = None,
        ready_timeout: float = 90.0,
        launch_timeout: float = 60.0,
    ) -> None:
        self._transport = transport
        self.boundary = getattr(transport, "boundary", "codespace")
        self._remote_dir = remote_dir.rstrip("/")
        self._reverse_forwards = list(reverse_forwards or [])
        self._ready_timeout = ready_timeout
        self._launch_timeout = launch_timeout

    async def spawn(
        self,
        child_argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str = "",
    ) -> SpawnedHost:
        import re

        from ssh_manager import LocalForward

        from . import protocol as proto
        from .bundle import build_session_host_bundle
        from .endpoints import endpoint_from_ssh_config

        nonce = new_nonce()
        bundle_path, _sha = await asyncio.to_thread(build_session_host_bundle)
        remote_bundle = f"{self._remote_dir}/{bundle_path.name}"
        # Cache by content hash: only ship when the far side lacks this bundle.
        if not await self._transport.path_exists(remote_bundle):
            await self._transport.push_file(str(bundle_path), remote_bundle)

        ts = int(time.time() * 1000)
        safe_sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "session")[:48]
        state_remote = f"{self._remote_dir}/host-{safe_sid}-{ts}.json"
        log_remote = f"{self._remote_dir}/host-{safe_sid}-{ts}.log"

        launch = build_remote_launch(
            remote_bundle, state_remote, log_remote, child_argv,
            nonce=nonce, cwd=cwd,
        )
        rc, out, err = await self._transport.run(
            launch, timeout=self._launch_timeout,
        )
        if rc != 0:
            raise RuntimeError(
                f"remote Session Host launch failed (rc={rc}): {err or out}"
            )

        state = await self._poll_state(state_remote, log_remote)
        remote_port = int(state["port"])
        host_pid = int(state["pid"])
        child_pid = int(state["child_pid"])
        protocol_version = int(state.get("protocol_version", proto.PROTOCOL_VERSION))

        config = self._transport.ssh_config()
        # Also allow the transport to contribute reverse-forwards (e.g. the
        # credential relay) so a detached far-side Host that outlives its launch
        # channel keeps a live relay for the whole session (rush build / ADO).
        reverse = list(self._reverse_forwards)
        get_reverse = getattr(self._transport, "reverse_forwards", None)
        if callable(get_reverse):
            reverse += list(get_reverse() or [])
        forward = LocalForward(config, remote_port, reverse_forwards=reverse)
        local_port = await forward.establish()

        extra = {}
        get_extra = getattr(self._transport, "endpoint_extra", None)
        if callable(get_extra):
            extra = get_extra() or {}
        endpoint = endpoint_from_ssh_config(
            config, remote_port, local_port, kind=self.boundary,
            reverse_forwards=reverse, extra=extra,
        )

        async def _refresh() -> None:
            await forward.refresh()

        log.info(
            "CodeSpace Session Host up: session=%s boundary=%s "
            "local=127.0.0.1:%d -> remote:%d (host_pid=%s child_pid=%s)",
            session_id, self.boundary, local_port, remote_port,
            host_pid, child_pid,
        )
        return SpawnedHost(
            local_port=local_port,
            host_pid=host_pid,
            child_pid=child_pid,
            protocol_version=protocol_version,
            boundary=self.boundary,
            nonce=nonce,
            state_file=state_remote,
            endpoint=endpoint,
            forward=forward,
            _refresh=_refresh,
        )

    async def _poll_state(
        self, state_remote: str, log_remote: str,
    ) -> dict[str, Any]:
        """Poll the far-side state file until the Host reports port + child."""
        deadline = time.time() + self._ready_timeout
        cmd = f"cat {shlex.quote(state_remote)} 2>/dev/null || true"
        while time.time() < deadline:
            _rc, out, _err = await self._transport.run(cmd, timeout=15.0)
            out = (out or "").strip()
            if out:
                try:
                    data = json.loads(out)
                except json.JSONDecodeError:
                    data = {}
                if data.get("port") and data.get("child_pid"):
                    return data
            await asyncio.sleep(0.5)
        # Surface the Host's own log to explain a launch that never got ready.
        tail = ""
        try:
            _rc, tail, _e = await self._transport.run(
                f"tail -n 40 {shlex.quote(log_remote)} 2>/dev/null || true",
                timeout=15.0,
            )
        except Exception:
            pass  # best-effort diagnostics only
        raise TimeoutError(
            f"remote Session Host did not report ready within "
            f"{self._ready_timeout}s (state={state_remote}); log tail:\n{tail}"
        )
