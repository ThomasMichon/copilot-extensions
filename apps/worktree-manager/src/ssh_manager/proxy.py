"""Owned loopback transport for Windows OpenSSH ProxyCommand children."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import hmac
import logging
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_procutil import (
    JobHandle,
    no_window_kwargs,
    spawn_in_kill_on_close_job,
    windowless_python,
)

from .config_sources import SSHConfig
from .config_sources import ssh_option as _option
from .process import (
    _OWNER_JOBS,
    close_process_job,
    redirected_ssh_env,
    register_process_cleanup,
    run_process_cleanup,
    ssh_subprocess_kwargs,
    terminate_ssh_process_tree,
)
from .proxy_jump import JumpRoute, prepare_windows_launch, resolve_jump_route
from .proxy_jump import expand_proxy_tokens as _expand_tokens

log = logging.getLogger("ssh-manager.proxy")
_DRAIN_TIMEOUT = 2.0
WINDOWS_OWNED_PROBE_VERSION = 1


def _is_windows() -> bool:
    return sys.platform == "win32"


def _split_windows_command(command: str) -> list[str]:
    from ctypes import wintypes

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int),
    ]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(argv)


@lru_cache(maxsize=8)
def _client_shell(executable: str, modified: int) -> str | None:
    result = subprocess.run(
        [executable, "-V"], capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not identify SSH client: {result.stderr.strip()}")
    if "OpenSSH_for_Windows" in result.stdout + result.stderr:
        return None
    shell = Path(executable).with_name("sh.exe")
    return str(shell) if shell.is_file() else None


async def _resolve_shell(executable: str) -> str | None:
    path = Path(shutil.which(executable) or executable)
    return await asyncio.to_thread(_client_shell, str(path), path.stat().st_mtime_ns)


def _client_command(port: int, capability: str, *, shell: bool = False) -> str:
    python = windowless_python(require_windowless=True)
    args = [
        python, str(Path(__file__).with_name("proxy_client.py")), str(port), capability,
    ]
    command = (
        shlex.join([args[0].replace("\\", "/"), args[1].replace("\\", "/"), *args[2:]])
        if shell else subprocess.list2cmdline(args)
    )
    return command.replace("%", "%%")


def _broker_args(args: Sequence[str], client_command: str) -> list[str]:
    # Keep HostName/Port untouched: they also expand identity and known-hosts paths.
    return [args[0], "-o", f"ProxyCommand={client_command}", *args[1:]]


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Relay bytes from ``reader`` to ``writer`` until the source is spent.

    On Windows, ProactorEventLoop pipe/socket transports commonly raise
    ``ConnectionResetError`` (``WinError 64``, "the specified network name is
    no longer available") from a read once the peer has gone away, instead of
    the POSIX-style empty-bytes EOF. Observed live bridging a loopback
    ``ProxyCommand`` socket to a spawned ``gh cs ssh --stdio`` child: the
    other pump direction closing first triggers exactly this on a read that
    would otherwise have returned ``b""``. Treat it as a normal end-of-stream
    rather than letting it surface as an unhandled "SSH proxy connection
    failed" warning/traceback -- the peer going away IS the close signal here.
    """
    while True:
        try:
            data = await reader.read(65536)
        except ConnectionResetError:
            return
        if not data:
            return
        writer.write(data)
        await writer.drain()


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(ConnectionError, OSError):
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=_DRAIN_TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError):
            writer.transport.abort()
            log.warning("SSH proxy writer did not close within the drain allowance")


class _ProxyBroker:
    def __init__(
        self, command: list[str], env: dict[str, str] | None, *, cwd: str | None = None,
        capability: str | None = None,
        ssh_route: JumpRoute | None = None,
    ) -> None:
        self._command = command
        self._env = env
        self._cwd = cwd
        self._ssh_route = ssh_route
        self.capability = capability or secrets.token_hex(32)
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task] = set()
        self._cleanups: set[asyncio.Task] = set()
        self._child_jobs: set[JobHandle] = set()
        self._used = False
        self._close_task: asyncio.Task | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        return int(self._server.sockets[0].getsockname()[1])

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        if self._used or len(self._clients) >= 4:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._clients.add(task)
        task.add_done_callback(self._clients.discard)

    async def _spawn_child(self) -> tuple[asyncio.subprocess.Process, JobHandle | None]:
        options = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": self._env,
            "cwd": self._cwd,
        }
        if self._ssh_route is not None:
            process = await _create_prepared_ssh(
                self._command, self._ssh_route.config, self._ssh_route.upstream, options,
            )
            return process, None
        return await spawn_in_kill_on_close_job(
            *self._command, **options,
            **(no_window_kwargs() if _is_windows() else ssh_subprocess_kwargs()),
        )

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        process = None
        child_job = None
        tasks: list[asyncio.Task] = []
        stderr_tail = bytearray()

        async def drain_stderr(stream: asyncio.StreamReader) -> None:
            while chunk := await stream.read(4096):
                stderr_tail.extend(chunk)
                del stderr_tail[:-4096]

        try:
            capability = await asyncio.wait_for(
                reader.readexactly(64), timeout=3.0,
            )
            if self._used or not hmac.compare_digest(
                capability, self.capability.encode("ascii"),
            ):
                raise RuntimeError("Invalid SSH proxy connection capability")
            self._used = True
            process, child_job = await self._spawn_child()
            if child_job is not None:
                self._child_jobs.add(child_job)
                _track_owner_job(process, child_job)
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise RuntimeError("SSH proxy did not receive its redirected streams")
            inbound = asyncio.create_task(_pump(reader, process.stdin))
            outbound = asyncio.create_task(_pump(process.stdout, writer))
            exited = asyncio.create_task(process.wait())
            errors = asyncio.create_task(drain_stderr(process.stderr))
            tasks = [inbound, outbound, exited, errors]
            done, _pending = await asyncio.wait(
                [inbound, outbound, exited], return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            if inbound in done:
                process.stdin.close()
            # asyncio.wait, not wait_for(gather(shield...)): it never cancels the
            # tasks, and a cancelled _serve (every broker close) leaves no
            # orphaned gather future behind -- on Python 3.11 that future's
            # CancelledError went unretrieved and asyncio logged
            # "_GatheringFuture exception was never retrieved" on each connect.
            drained, pending = await asyncio.wait(
                {exited, outbound}, timeout=_DRAIN_TIMEOUT,
            )
            if pending:
                log.debug("SSH proxy did not drain after its transport closed")
            for task in drained:
                task.result()
            if process.returncode not in (None, 0):
                await errors
                log.warning(
                    "SSH proxy exited with code %s: %s",
                    process.returncode, stderr_tail.decode(errors="replace").strip(),
                )
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            log.debug("SSH proxy connection closed before authentication")
        except (OSError, ConnectionError, RuntimeError):
            log.warning("SSH proxy connection failed", exc_info=True)
        finally:
            cleanup = asyncio.create_task(
                self._cleanup_connection(process, tasks, writer, child_job)
            )
            self._cleanups.add(cleanup)
            cleanup.add_done_callback(self._cleanups.discard)
            await asyncio.shield(cleanup)

    async def _cleanup_connection(
        self, process: asyncio.subprocess.Process | None,
        tasks: list[asyncio.Task],
        writer: asyncio.StreamWriter,
        child_job: JobHandle | None,
    ) -> None:
        writer_cleanup = asyncio.create_task(_close_writer(writer))
        try:
            if process is not None:
                await terminate_ssh_process_tree(process)
        finally:
            if child_job is not None:
                self._child_jobs.discard(child_job)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await writer_cleanup

    async def _close(self) -> None:
        if self._server is not None:
            self._server.close()
        clients = list(self._clients)
        for task in clients:
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)
        if self._cleanups:
            await asyncio.gather(*list(self._cleanups))
        if self._server is not None:
            await self._server.wait_closed()

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)


# How long the ambient background watcher (below) waits for its own
# cleanup before moving on. This watcher has no caller waiting on it for
# correctness -- it exists purely to reap the broker's spawned process once
# the local SSH root exits -- so it must never be allowed to block the
# hosting CLI process's exit for as long as a slow remote `taskkill /T /F`
# takes (observed 100+ seconds on Windows under endpoint-protection
# scanning). The shielded cleanup keeps running in the background past this
# bound; only THIS watcher's wait is capped.
_WATCHER_CLEANUP_TIMEOUT = 10.0


async def _watch_process(process: asyncio.subprocess.Process) -> None:
    try:
        await process.wait()
    finally:
        await run_process_cleanup(process, timeout=_WATCHER_CLEANUP_TIMEOUT)


_WATCHERS: set[asyncio.Task] = set()
_OWNER_JOB_WATCHERS: set[asyncio.Task] = set()


def _watcher_done(task: asyncio.Task) -> None:
    _WATCHERS.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.warning(
            "SSH proxy cleanup failed",
            exc_info=(type(error), error, error.__traceback__),
        )


async def _close_owner_job_when_process_ends(
    process: asyncio.subprocess.Process,
) -> None:
    try:
        await process.wait()
    finally:
        close_process_job(process)


def _job_watcher_done(task: asyncio.Task) -> None:
    _OWNER_JOB_WATCHERS.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.debug(
            "SSH owner job watcher failed",
            exc_info=(type(error), error, error.__traceback__),
        )


def _track_owner_job(
    process: asyncio.subprocess.Process,
    job_handle: JobHandle | None,
) -> None:
    if job_handle is None:
        return
    _OWNER_JOBS[id(process)] = job_handle
    watcher = asyncio.create_task(_close_owner_job_when_process_ends(process))
    _OWNER_JOB_WATCHERS.add(watcher)
    watcher.add_done_callback(_job_watcher_done)


def ssh_process_env(
    config: Any, extra: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """The environment an SSH subprocess for ``config`` must run with.

    Starts from the config source's pinned ``process_env`` (for example a
    CodeSpace's owning gh account) instead of the ambient environment, then
    applies ``extra``. ``None`` means "inherit the ambient environment"."""
    base = getattr(config, "process_env", None)
    if base is None and not extra:
        return None
    env = dict(base if base is not None else os.environ)
    env.update(extra or {})
    return env


def _redirected_environment(
    config: SSHConfig, environment: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    if environment is None:
        environment = ssh_process_env(config)
    return redirected_ssh_env(environment, windows=_is_windows())


async def create_ssh_subprocess(
    *args: str, config: SSHConfig, **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Launch SSH, owning any Windows native proxy outside OpenSSH's child spawn."""
    route: JumpRoute | None = None
    if _is_windows():
        args, config = await prepare_windows_launch(
            args, config, kwargs.get("executable") or args[0], cwd=kwargs.get("cwd"),
            environment=_redirected_environment(config, kwargs.get("env")),
        )
        route = await resolve_jump_route(
            config, kwargs.get("executable") or args[0], cwd=kwargs.get("cwd"),
            environment=_redirected_environment(config, kwargs.get("env")),
        )
    return await _create_prepared_ssh(args, config, route, kwargs)


async def _create_prepared_ssh(
    args: Sequence[str], config: SSHConfig, route: JumpRoute | None,
    kwargs: dict[str, Any],
) -> asyncio.subprocess.Process:
    kwargs["env"] = _redirected_environment(config, kwargs.get("env"))
    proxy = getattr(config, "proxy_command", None)
    if not proxy and isinstance(config, SSHConfig):
        proxy = _option(config, "proxycommand")
    if not _is_windows() or (route is None and (not proxy or proxy.casefold() == "none")):
        if _is_windows() and (
            config.effective_config or config.proxy_command is not None
            or _option(config, "proxyjump") is not None
        ):
            args = _broker_args(args, "none")
        process, job_handle = await spawn_in_kill_on_close_job(
            *args, **ssh_subprocess_kwargs(**kwargs)
        )
        _track_owner_job(process, job_handle)
        return process
    shell = await _resolve_shell(kwargs.get("executable") or args[0])
    if route is not None:
        command = list(route.argv)
    else:
        if proxy is None:
            raise ValueError("SSH proxy command is unavailable")
        expanded = _expand_tokens(proxy, config)
        command = [shell, "-c", expanded] if shell else _split_windows_command(expanded)
    if not command:
        raise ValueError("SSH proxy command is empty")
    capability = secrets.token_hex(32)
    broker_options: dict[str, Any] = {"cwd": kwargs.get("cwd"), "capability": capability}
    if route is not None:
        broker_options["ssh_route"] = route
    broker = _ProxyBroker(command, kwargs.get("env"), **broker_options)
    try:
        port = await broker.start()
        process, job_handle = await spawn_in_kill_on_close_job(
            *_broker_args(args, _client_command(port, capability, shell=shell is not None)),
            **ssh_subprocess_kwargs(**kwargs),
        )
        _track_owner_job(process, job_handle)
    except BaseException:
        await broker.close()
        raise
    register_process_cleanup(process, broker.close)
    watcher = asyncio.create_task(_watch_process(process))
    _WATCHERS.add(watcher)
    watcher.add_done_callback(_watcher_done)
    return process
