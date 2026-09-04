"""ConnectionManager -- SSH ControlMaster connection pool.

Owns one SSH ControlMaster connection per unique remote host. All plugins
that need SSH go through this manager to share multiplexed connections.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .carrier import CarrierLease, CarrierUnavailable, PersistentCarrier
from .config_sources import ConfigSource, SSHConfig
from .platform import (
    PlatformInfo,
    detect_platform,
    ensure_socket_dir,
    socket_path_for_host,
)
from .process import ssh_subprocess_kwargs, terminate_ssh_process_tree

log = logging.getLogger("ssh-manager")

# Max bytes for a single newline-delimited frame read from a multiplexed stdio
# channel. asyncio's StreamReader defaults to 64 KiB per line; ACP
# `session/update` frames carrying a large tool result can exceed that in one
# frame, overflowing readline() and tearing down the channel ("Connection
# closed"). Mirror the acp library's 50 MB default so remote ACP sessions match
# local ones.
_STDIO_CHANNEL_LIMIT_BYTES = 50 * 1024 * 1024


def _carrier_transport_identity(info: ConnectionInfo) -> str:
    """Key a carrier by SSH routing plus tunnels inherited by its process."""
    forwards = sorted(
        " ".join(str(forward).split())
        for forward in getattr(info, "port_forwards", [])
    )
    if not forwards:
        return info.connection_identity
    encoded = json.dumps(
        {
            "connection_identity": info.connection_identity,
            "port_forwards": forwards,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

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


async def _terminate_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace: float = 5.0,
) -> None:
    """Terminate an SSH child and any children it spawned."""
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(  # noqa: S603, S607
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=_creation_flags(),
            )
        except OSError:
            proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except (TimeoutError, asyncio.TimeoutError):
        pass
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
    else:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("Process tree rooted at pid %s did not exit", proc.pid)


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
    child_processes: list[asyncio.subprocess.Process] = field(default_factory=list)

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
        self._carriers: dict[str, PersistentCarrier] = {}
        self._carrier_hosts: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._carrier_lock = asyncio.Lock()

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
        *,
        preserve_existing_forwards: bool = False,
    ) -> ConnectionInfo:
        """Ensure a master connection exists for the given host.

        Idempotent -- if a matching connection already exists and is
        healthy, returns it. If port_forwards differ from an existing
        connection, disconnects and reconnects with the new forwards.
        """
        lock = await self._get_lock(host)
        async with lock:
            existing = self._connections.get(host)
            forwards = (
                list(existing.port_forwards)
                if preserve_existing_forwards and existing is not None
                else port_forwards or []
            )

            # Check existing connection
            if host in self._connections:
                existing = self._connections[host]
                # Off-load the config fetch. For a CodespaceConfigSource this
                # runs a blocking ``gh codespace ssh --config``, which on a
                # *Shutdown* CodeSpace cold-starts the box and can block for
                # 60-120s. Left on the event loop that blocking call would freeze
                # the daemon's /health endpoint and trip the serving watchdog,
                # force-exiting the daemon mid-connect (#166). to_thread keeps the
                # loop serving while the boot proceeds.
                config = await asyncio.to_thread(config_source.get_ssh_config)

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
            config = await asyncio.to_thread(config_source.get_ssh_config)
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
            namespace=host,
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
            args.append(fwd)  # e.g., "-R 9857:localhost:9857"

        args.append(config.ssh_target)

        log.debug("Starting ControlMaster: %s", " ".join(args))

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **ssh_subprocess_kwargs(),
        )

        # Wait briefly for connection to establish or fail
        try:
            await asyncio.wait_for(self._wait_for_socket(socket), timeout=15.0)
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            raise
        except (TimeoutError, asyncio.TimeoutError):
            stderr = ""
            if proc.stderr:
                try:
                    raw = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
                    stderr = raw.decode(errors="replace")
                except (TimeoutError, Exception):  # noqa: S110
                    pass  # best-effort stderr capture
            await _terminate_process_tree(proc)
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
        """Build SSH args that use the existing ControlMaster socket.

        In direct mode (Windows), also includes port forwards since there
        is no persistent master connection to carry them.
        """
        args = self._base_ssh_args(info.config)
        if info.multiplexed:
            args.extend([
                "-o", f"ControlPath={info.socket_path}",
                "-o", "ControlMaster=no",
            ])
        else:
            # Direct mode: port forwards must be on every SSH invocation
            # (no master connection to carry them).
            # Each forward may be "-R host:port" (two tokens) or a single
            # string — split on the first space to handle both forms.
            for fwd in info.port_forwards:
                parts = fwd.split(None, 1)
                args.extend(parts)
        return args

    async def exec_command(
        self,
        host: str,
        command: str,
        timeout: float | None = 60.0,
        input_bytes: bytes | None = None,
    ) -> CommandResult:
        """Run a command over the multiplexed (or direct) SSH connection.

        When ``input_bytes`` is given it is written to the remote command's
        stdin instead of embedding large payloads in the command line.

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
            stdin=(
                asyncio.subprocess.PIPE
                if input_bytes is not None
                else asyncio.subprocess.DEVNULL
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **ssh_subprocess_kwargs(),
        )

        timed_out = False
        try:
            info.child_processes.append(proc)
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=input_bytes), timeout=timeout
                )
            finally:
                if proc in info.child_processes and proc.returncode is not None:
                    info.child_processes.remove(proc)
        except (TimeoutError, asyncio.TimeoutError):
            await _terminate_process_tree(proc)
            stdout_bytes, stderr_bytes = await proc.communicate()
            timed_out = True
            if proc in info.child_processes:
                info.child_processes.remove(proc)
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            if proc in info.child_processes:
                info.child_processes.remove(proc)
            raise

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
        *,
        discard_stderr: bool = False,
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
            stderr=(
                asyncio.subprocess.DEVNULL
                if discard_stderr
                else asyncio.subprocess.PIPE
            ),
            # POSIX: give the ssh child its own session/process group so
            # teardown signals only the ssh process tree -- never the parent's
            # group. Windows uses taskkill /T against the root pid.
            **ssh_subprocess_kwargs(limit=_STDIO_CHANNEL_LIMIT_BYTES),
        )

        info.child_processes.append(proc)
        return proc

    async def close_stdio_channel(
        self,
        host: str,
        proc: asyncio.subprocess.Process,
        *,
        grace: float = 2.0,
    ) -> None:
        """Close a stdio channel by EOF, then reap its isolated SSH tree."""
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except (AttributeError, ConnectionError, OSError):
                pass
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace)
            except (TimeoutError, asyncio.TimeoutError):
                await terminate_ssh_process_tree(proc)
        info = self._connections.get(host)
        if info is not None and proc in info.child_processes:
            info.child_processes.remove(proc)

    async def acquire_carrier(
        self,
        host: str,
        remote_command: str,
        **options: Any,
    ) -> CarrierLease:
        """Acquire the one persistent carrier for this connection identity.

        ``ensure_connected`` remains the authority for SSH configuration. The
        carrier registry is keyed by its complete normalized identity so two
        aliases resolving to the same connection cannot race up duplicate
        long-lived SSH processes.
        """
        info = self._connections.get(host)
        if info is None:
            raise RuntimeError(
                f"No connection to {host}. Call ensure_connected() first."
            )
        identity = _carrier_transport_identity(info)
        process_hosts: dict[int, str] = {}

        async def _open() -> asyncio.subprocess.Process:
            active_host = next(
                (
                    candidate
                    for candidate, connection in self._connections.items()
                    if _carrier_transport_identity(connection) == identity
                ),
                None,
            )
            if active_host is None:
                raise RuntimeError(
                    "no active SSH connection for carrier identity"
                )
            process = await self.open_stdio_channel(
                active_host,
                remote_command,
                discard_stderr=True,
            )
            process_hosts[id(process)] = active_host
            self._carrier_hosts[identity] = active_host
            return process

        async def _close(proc: asyncio.subprocess.Process) -> None:
            active_host = process_hosts.pop(id(proc), None)
            if self._carrier_hosts.get(identity) == active_host:
                self._carrier_hosts.pop(identity, None)
            if active_host is not None:
                await self.close_stdio_channel(active_host, proc)
            elif proc.returncode is None:
                await _terminate_process_tree(proc)

        def _new_carrier() -> PersistentCarrier:
            return PersistentCarrier(
                identity,
                remote_command,
                _open,
                _close,
                on_retired=self._carrier_retired,
                **options,
            )

        async with self._carrier_lock:
            carrier = self._carriers.get(identity)
            if carrier is not None and carrier.retired:
                self._carriers.pop(identity, None)
                carrier = None
            if carrier is None:
                carrier = _new_carrier()
                self._carriers[identity] = carrier
            elif carrier.remote_command != remote_command:
                raise RuntimeError(
                    "connection identity already has a different carrier endpoint"
                )

        try:
            return await carrier.acquire()
        except CarrierUnavailable:
            if not carrier.retired:
                raise
            async with self._carrier_lock:
                replacement = self._carriers.get(identity)
                if replacement is carrier or replacement is None or replacement.retired:
                    if self._carriers.get(identity) is carrier:
                        self._carriers.pop(identity, None)
                        self._carrier_hosts.pop(identity, None)
                    replacement = _new_carrier()
                    self._carriers[identity] = replacement
                elif replacement.remote_command != remote_command:
                    raise RuntimeError(
                        "connection identity already has a different carrier endpoint"
                    )
            return await replacement.acquire()
        except Exception:
            async with self._carrier_lock:
                if self._carriers.get(identity) is carrier:
                    self._carriers.pop(identity, None)
            await carrier.close()
            raise

    def _carrier_retired(
        self,
        identity: str,
        carrier: PersistentCarrier,
    ) -> None:
        if self._carriers.get(identity) is carrier:
            self._carriers.pop(identity, None)

    def carrier_diagnostics(self) -> dict[str, Any]:
        """Return aggregate carrier health/counts without identities or payloads."""
        snapshots = [carrier.diagnostics() for carrier in self._carriers.values()]
        return {
            "total": len(snapshots),
            "healthy": sum(item["state"] == "healthy" for item in snapshots),
            "degraded": sum(item["state"] == "degraded" for item in snapshots),
            "logical_clients": sum(item["logical_clients"] for item in snapshots),
            "active_requests": sum(item["active_requests"] for item in snapshots),
            "active_subscriptions": sum(
                item["active_subscriptions"] for item in snapshots
            ),
            "queued_frames": sum(item["queued_frames"] for item in snapshots),
            "buffered_bytes": sum(
                item["queued_bytes"] + item["buffered_event_bytes"]
                for item in snapshots
            ),
        }

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
        carrier_identity = _carrier_transport_identity(info)
        carrier = self._carriers.get(carrier_identity)
        if carrier is not None:
            identity_remains = any(
                _carrier_transport_identity(connection) == carrier_identity
                for connection in self._connections.values()
            )
            if identity_remains:
                if self._carrier_hosts.get(carrier_identity) == host:
                    await carrier.invalidate_transport(
                        "carrier SSH alias disconnected"
                    )
            else:
                await carrier.close()

        for child in list(info.child_processes):
            if child.returncode is None:
                await _terminate_process_tree(child)
            if child in info.child_processes:
                info.child_processes.remove(child)

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
                    **ssh_subprocess_kwargs(),
                )
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (TimeoutError, asyncio.TimeoutError) as e:
                await _terminate_process_tree(proc)
                log.warning("Graceful disconnect failed for %s: %s", host, e)
            except OSError as e:
                log.warning("Graceful disconnect failed for %s: %s", host, e)

        # Kill master process if still running
        if info.master_process and info.master_process.returncode is None:
            await _terminate_process_tree(info.master_process)

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
