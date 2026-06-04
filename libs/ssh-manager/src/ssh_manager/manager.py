"""ConnectionManager -- SSH ControlMaster connection pool.

Owns one SSH ControlMaster connection per unique remote host. All plugins
that need SSH go through this manager to share multiplexed connections.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config_sources import ConfigSource, SSHConfig
from .platform import (
    PlatformInfo,
    detect_platform,
    ensure_socket_dir,
    socket_path_for_host,
)

log = logging.getLogger("ssh-manager")

# Module-level default instance (lazy-initialized)
_default_manager: ConnectionManager | None = None


def get_default_manager() -> ConnectionManager:
    """Return the process-wide default ConnectionManager.

    Creates one on first call. For testing, create your own instance.
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = ConnectionManager()
    return _default_manager


def _creation_flags() -> int:
    """Subprocess creation flags for Windows headless compatibility."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


@dataclass
class CommandResult:
    """Result of a remote command execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def check(self) -> None:
        """Raise if the command failed."""
        if self.timed_out:
            raise TimeoutError(
                f"SSH command timed out. stderr: {self.stderr}"
            )
        if self.exit_code != 0:
            raise subprocess.CalledProcessError(
                self.exit_code, "ssh", self.stdout, self.stderr
            )


@dataclass
class ConnectionInfo:
    """Information about an active SSH master connection."""

    host: str
    config: SSHConfig
    socket_path: Path
    master_process: asyncio.subprocess.Process | None
    platform: PlatformInfo
    port_forwards: list[str] = field(default_factory=list)
    connection_identity: str = ""

    @property
    def multiplexed(self) -> bool:
        """Whether this connection uses ControlMaster multiplexing."""
        return self.platform.supports_control_master


class ConnectionManager:
    """Owns one SSH ControlMaster connection per remote host.

    Thread-safe via per-host async locks. Supports both ControlMaster
    multiplexing (Unix) and direct SSH fallback (Windows).
    """

    def __init__(self, platform: PlatformInfo | None = None) -> None:
        self._platform = platform or detect_platform()
        self._connections: dict[str, ConnectionInfo] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    @property
    def platform(self) -> PlatformInfo:
        return self._platform

    async def _get_lock(self, host: str) -> asyncio.Lock:
        """Get or create a per-host lock."""
        async with self._global_lock:
            if host not in self._locks:
                self._locks[host] = asyncio.Lock()
            return self._locks[host]

    async def ensure_connected(
        self,
        host: str,
        config_source: ConfigSource,
        port_forwards: list[str] | None = None,
    ) -> ConnectionInfo:
        """Ensure a master connection exists for the given host.

        Idempotent -- if a matching connection already exists and is
        healthy, returns it. If port_forwards differ from an existing
        connection, disconnects and reconnects with the new forwards.
        """
        lock = await self._get_lock(host)
        async with lock:
            forwards = port_forwards or []

            # Check existing connection
            if host in self._connections:
                existing = self._connections[host]
                config = config_source.get_ssh_config()

                # Verify identity matches (same user, host, port, proxy)
                if existing.connection_identity != config.connection_identity:
                    log.info(
                        "Connection identity changed for %s, reconnecting",
                        host,
                    )
                    await self._disconnect_unlocked(host)
                elif sorted(existing.port_forwards) != sorted(forwards):
                    log.info(
                        "Port forwards changed for %s, reconnecting",
                        host,
                    )
                    await self._disconnect_unlocked(host)
                elif existing.master_process and existing.master_process.returncode is not None:
                    log.info(
                        "Master process died for %s (rc=%d), reconnecting",
                        host,
                        existing.master_process.returncode,
                    )
                    await self._disconnect_unlocked(host)
                else:
                    return existing

            # Establish new connection
            config = config_source.get_ssh_config()
            return await self._connect(host, config, forwards)

    async def _connect(
        self,
        host: str,
        config: SSHConfig,
        port_forwards: list[str],
    ) -> ConnectionInfo:
        """Establish a new master SSH connection."""
        ensure_socket_dir(self._platform)

        socket = socket_path_for_host(
            self._platform,
            config.hostname or config.host_alias,
            config.user,
            config.port,
        )

        if self._platform.supports_control_master:
            proc = await self._start_control_master(config, socket, port_forwards)
        else:
            # Direct mode -- no persistent master process
            proc = None
            log.info(
                "Platform does not support ControlMaster; using direct SSH for %s",
                host,
            )

        info = ConnectionInfo(
            host=host,
            config=config,
            socket_path=socket,
            master_process=proc,
            platform=self._platform,
            port_forwards=port_forwards,
            connection_identity=config.connection_identity,
        )
        self._connections[host] = info

        log.info(
            "Connected to %s (mode=%s, socket=%s)",
            host,
            self._platform.mode.value,
            socket,
        )
        return info

    async def _start_control_master(
        self,
        config: SSHConfig,
        socket: Path,
        port_forwards: list[str],
    ) -> asyncio.subprocess.Process:
        """Start an SSH ControlMaster process."""
        args = self._base_ssh_args(config)
        args.extend([
            "-o", f"ControlPath={socket}",
            "-o", "ControlMaster=yes",
            "-o", "ControlPersist=yes",
            "-N",  # no remote command -- just hold the connection
        ])

        for fwd in port_forwards:
            args.append(fwd)  # e.g., "-R 9847:localhost:9847"

        args.append(config.ssh_target)

        log.debug("Starting ControlMaster: %s", " ".join(args))

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
        )

        # Wait briefly for connection to establish or fail
        try:
            await asyncio.wait_for(self._wait_for_socket(socket), timeout=15.0)
        except TimeoutError:
            stderr = ""
            if proc.stderr:
                try:
                    raw = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
                    stderr = raw.decode(errors="replace")
                except (TimeoutError, Exception):  # noqa: S110
                    pass  # best-effort stderr capture
            proc.kill()
            raise ConnectionError(
                f"ControlMaster failed to establish for {config.ssh_target}: {stderr}"
            ) from None

        return proc

    async def _wait_for_socket(self, socket: Path) -> None:
        """Wait for the ControlMaster socket to appear."""
        for _ in range(150):  # 15s at 0.1s intervals
            if socket.exists():
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"Socket {socket} did not appear")

    def _base_ssh_args(self, config: SSHConfig) -> list[str]:
        """Build base SSH arguments from config (reused for all operations)."""
        args = ["ssh"]

        if config.config_file:
            args.extend(["-F", config.config_file])
        if config.port:
            args.extend(["-p", str(config.port)])
        if config.identity_file:
            args.extend(["-i", config.identity_file])

        args.extend([
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30",
            "-o", "BatchMode=yes",
            "-T",  # no PTY
        ])

        for key, val in config.extra_options.items():
            args.extend(["-o", f"{key}={val}"])

        return args

    def _mux_ssh_args(self, info: ConnectionInfo) -> list[str]:
        """Build SSH args that use the existing ControlMaster socket."""
        args = self._base_ssh_args(info.config)
        if info.multiplexed:
            args.extend([
                "-o", f"ControlPath={info.socket_path}",
                "-o", "ControlMaster=no",
            ])
        return args

    async def exec_command(
        self,
        host: str,
        command: str,
        timeout: float | None = 60.0,
    ) -> CommandResult:
        """Run a command over the multiplexed (or direct) SSH connection.

        Returns a CommandResult with stdout, stderr, exit code, and
        timeout status. Does not raise on nonzero exit -- call
        result.check() if you want exceptions.
        """
        if host not in self._connections:
            raise RuntimeError(
                f"No connection to {host}. Call ensure_connected() first."
            )

        info = self._connections[host]
        args = self._mux_ssh_args(info)
        args.append(info.config.ssh_target)
        args.append(command)

        log.debug("exec_command on %s: %s", host, command)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            stdout_bytes, stderr_bytes = await proc.communicate()
            timed_out = True

        return CommandResult(
            stdout=stdout_bytes.decode(errors="replace").rstrip(),
            stderr=stderr_bytes.decode(errors="replace").rstrip(),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timed_out=timed_out,
        )

    async def open_stdio_channel(
        self,
        host: str,
        remote_cmd: str,
    ) -> asyncio.subprocess.Process:
        """Open a bidirectional stdin/stdout channel for ACP sessions.

        Returns the subprocess.Process with pipes for stdin/stdout/stderr.
        The caller owns the process lifetime. The connection is multiplexed
        over the existing ControlMaster when available.
        """
        if host not in self._connections:
            raise RuntimeError(
                f"No connection to {host}. Call ensure_connected() first."
            )

        info = self._connections[host]
        args = self._mux_ssh_args(info)
        args.append(info.config.ssh_target)
        args.append(remote_cmd)

        log.debug("open_stdio_channel on %s: %s", host, remote_cmd)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
        )

        return proc

    async def disconnect(self, host: str) -> None:
        """Tear down the master connection for a host."""
        lock = await self._get_lock(host)
        async with lock:
            await self._disconnect_unlocked(host)

    async def _disconnect_unlocked(self, host: str) -> None:
        """Disconnect without acquiring the lock (caller holds it)."""
        if host not in self._connections:
            return

        info = self._connections.pop(host)

        if info.multiplexed and info.socket_path.exists():
            # Gracefully close the ControlMaster via -O exit
            args = self._base_ssh_args(info.config)
            args.extend([
                "-o", f"ControlPath={info.socket_path}",
                "-O", "exit",
                info.config.ssh_target,
            ])
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=_creation_flags(),
                )
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (TimeoutError, OSError) as e:
                log.warning("Graceful disconnect failed for %s: %s", host, e)

        # Kill master process if still running
        if info.master_process and info.master_process.returncode is None:
            info.master_process.kill()
            try:
                await asyncio.wait_for(info.master_process.wait(), timeout=5.0)
            except TimeoutError:
                log.warning("Master process for %s did not exit after kill", host)

        # Clean up stale socket
        if info.socket_path.exists():
            try:
                info.socket_path.unlink()
            except OSError:
                pass

        log.info("Disconnected from %s", host)

    def list_connections(self) -> list[ConnectionInfo]:
        """List all active connections."""
        return list(self._connections.values())

    async def disconnect_all(self) -> None:
        """Disconnect all hosts. Use during shutdown."""
        hosts = list(self._connections.keys())
        for host in hosts:
            await self.disconnect(host)
