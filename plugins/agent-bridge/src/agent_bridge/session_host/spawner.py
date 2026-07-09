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
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .launcher import launch_session_host


@dataclass
class SpawnedHost:
    """A launched Session Host + everything the frontend needs to reach it.

    ``local_port`` is always a loopback port on *this* machine (directly bound
    for a local/elevated Host, or the near end of a forward for a remote one).
    ``nonce`` is the connect-auth token to present on ATTACH. ``refresh_endpoint``
    re-establishes the local port before a reattach redial (no-op for local).
    """

    local_port: int
    host_pid: int
    child_pid: int
    protocol_version: int
    boundary: str = "local"
    nonce: str = ""
    state_file: str = ""
    proc: Any = None
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
