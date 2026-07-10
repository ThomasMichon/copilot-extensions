"""LocalForward -- a dedicated ``ssh -N -L <local>:127.0.0.1:<remote>`` process.

Makes a **remote loopback endpoint** (a Session Host running on a machine-mesh
box or inside a CodeSpace) look local, so the agent-bridge frontend always dials
``127.0.0.1:<local_port>`` and speaks the seq/ack protocol -- there is no
per-boundary transport in the ACP hot path (see the ``codespace-dispatch-
reliability`` effort's "one Session Host, two seams, one client").

This is deliberately a **dedicated forwarding process**, not a ControlMaster
``-O forward``:

* On Windows ssh-manager runs in DIRECT mode with **no** persistent master
  process to carry a forward, so an ``-O forward`` has nowhere to live.
* Even on POSIX a dedicated ``ssh -N -L`` process is boundary-uniform and cleanly
  **cancel / re-establish**-able -- which is exactly what the reattach driver's
  ``refresh_endpoint()`` needs after a transport drop.

The near end binds **loopback only** (``127.0.0.1``), so the forwarded endpoint is
never exposed off-box. The forward reuses the same :class:`SSHConfig` the
ConnectionManager uses (including a CodeSpace's ``gh cs ssh`` ``ProxyCommand`` via
its ``-F`` config file), so a CodeSpace forward is just ``ssh -F <cfg> <host>
-N -L ...`` over the gh tunnel.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import sys

from .config_sources import SSHConfig

log = logging.getLogger("ssh-manager.forward")


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def pick_free_local_port() -> int:
    """Return a free loopback TCP port the OS is willing to hand out.

    Binding ``127.0.0.1:0`` lets the OS choose an ephemeral port that is **not**
    in a reserved/excluded range (on Windows, Hyper-V/WinNAT reserve blocks that
    the OS then refuses to bind -- ``netsh int ipv4 show excludedportrange``).
    Letting the kernel pick sidesteps that class of failure entirely.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def build_forward_ssh_args(
    config: SSHConfig,
    local_port: int,
    remote_port: int,
    *,
    remote_host: str = "127.0.0.1",
    reverse_forwards: list[str] | None = None,
    extra_options: dict[str, str] | None = None,
) -> list[str]:
    """Build the ``ssh -N -L`` argv for a dedicated local-forward process.

    Mirrors :meth:`ConnectionManager._base_ssh_args` (``-F``/``-p``/``-i`` +
    keepalive/batch options) and appends a loopback ``-L`` forward plus ``-N``
    (no remote command -- just hold the forward). ``ExitOnForwardFailure=yes``
    makes ssh exit immediately if the local port cannot be bound, so the caller
    can retry with a fresh port instead of hanging on a half-open forward.

    ``reverse_forwards`` are additional ``-R`` specs (e.g. the credential-relay
    port ``"51234:127.0.0.1:51234"``) carried on the *same* persistent process,
    so a detached far-side Session Host that outlives its launch channel keeps a
    live relay for the whole session -- and a ``refresh()`` brings both the ``-L``
    endpoint and the ``-R`` relay back together after a transport drop.
    """
    args = ["ssh"]
    if config.config_file:
        args += ["-F", config.config_file]
    if config.port:
        args += ["-p", str(config.port)]
    if config.identity_file:
        args += ["-i", config.identity_file]
    args += [
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes",
        "-T",  # no PTY
        "-N",  # no remote command -- forward only
    ]
    for key, val in config.extra_options.items():
        # ControlMaster machinery must not leak in: a dedicated forward is not
        # multiplexed over the shared master (that master may not exist on
        # Windows, and reusing it would tie the forward's lifetime to it).
        if key.lower() in ("controlmaster", "controlpath", "controlpersist"):
            continue
        args += ["-o", f"{key}={val}"]
    for key, val in (extra_options or {}).items():
        args += ["-o", f"{key}={val}"]
    args += ["-L", f"127.0.0.1:{local_port}:{remote_host}:{remote_port}"]
    for spec in reverse_forwards or []:
        args += ["-R", spec]
    args.append(config.ssh_target)
    return args


class LocalForward:
    """A dedicated ``ssh -N -L`` process forwarding a remote loopback port.

    ``establish()`` picks a free local port (unless one is fixed), spawns the
    forward, and waits until the local end accepts connections. ``cancel()``
    tears the process down. ``refresh()`` (cancel + re-establish, reusing the
    same local port when possible) is the ``refresh_endpoint()`` primitive the
    reattach driver calls after a transport drop.
    """

    def __init__(
        self,
        config: SSHConfig,
        remote_port: int,
        *,
        local_port: int | None = None,
        remote_host: str = "127.0.0.1",
        reverse_forwards: list[str] | None = None,
        extra_options: dict[str, str] | None = None,
        ready_timeout: float = 40.0,
        connect_probe_interval: float = 0.25,
    ) -> None:
        self._config = config
        self._remote_port = int(remote_port)
        self._remote_host = remote_host
        self._reverse_forwards = list(reverse_forwards or [])
        self._extra_options = dict(extra_options or {})
        self._ready_timeout = ready_timeout
        self._probe_interval = connect_probe_interval
        self._fixed_local_port = int(local_port) if local_port else None
        self.local_port: int | None = self._fixed_local_port
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def establish(self) -> int:
        """Spawn the forward and wait until the local port accepts. Returns it.

        Retries a couple of times on a fresh port if the chosen local port loses
        a race (``ExitOnForwardFailure`` makes that a fast, clean ssh exit).
        Raises :class:`ConnectionError` if the forward never comes up.
        """
        attempts = 1 if self._fixed_local_port else 3
        last_err: str = ""
        for attempt in range(1, attempts + 1):
            port = self._fixed_local_port or pick_free_local_port()
            args = build_forward_ssh_args(
                self._config, port, self._remote_port,
                remote_host=self._remote_host,
                reverse_forwards=self._reverse_forwards,
                extra_options=self._extra_options,
            )
            log.debug("Establishing local forward (attempt %d): %s",
                      attempt, " ".join(args))
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_creation_flags(),
                start_new_session=(sys.platform != "win32"),
            )
            self._proc = proc
            self.local_port = port
            if await self._wait_ready(proc, port):
                log.info("Local forward up: 127.0.0.1:%d -> %s:%d",
                         port, self._remote_host, self._remote_port)
                return port
            # Failed -- collect stderr, tear down, maybe retry a fresh port.
            last_err = await self._drain_stderr(proc)
            await self._kill(proc)
            self._proc = None
        raise ConnectionError(
            f"local forward to {self._config.ssh_target} "
            f"({self._remote_host}:{self._remote_port}) did not come up: "
            f"{last_err or 'timeout'}"
        )

    async def refresh(self) -> int:
        """Re-establish the forward after a transport drop (``refresh_endpoint``).

        Cancels any existing process and brings a new one up. Reuses the prior
        local port so a caller/index that cached it stays valid.
        """
        if self._proc is not None:
            await self._kill(self._proc)
            self._proc = None
        if self.local_port and self._fixed_local_port is None:
            # Pin to the previously-advertised port so cached endpoints resolve.
            self._fixed_local_port = self.local_port
        return await self.establish()

    async def cancel(self) -> None:
        """Tear down the forward process (idempotent)."""
        if self._proc is not None:
            await self._kill(self._proc)
            self._proc = None

    async def _wait_ready(
        self, proc: asyncio.subprocess.Process, port: int,
    ) -> bool:
        """Poll until the local port accepts a TCP connection or ssh exits."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._ready_timeout
        while loop.time() < deadline:
            if proc.returncode is not None:
                return False  # ssh exited before the forward came up
            if await self._port_accepts(port):
                return True
            await asyncio.sleep(self._probe_interval)
        return False

    @staticmethod
    async def _port_accepts(port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0,
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> str:
        if proc.stderr is None:
            return ""
        try:
            raw = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
        except (asyncio.TimeoutError, OSError):
            return ""
        return raw.decode(errors="replace").strip()

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
