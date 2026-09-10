"""Owned loopback transport for Windows OpenSSH ProxyCommand children."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import getpass
import hmac
import logging
import ntpath
import re
import secrets
import subprocess
import sys
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
import shlex
import shutil
from typing import Any

from agent_procutil import no_window_kwargs, windowless_python

from .config_sources import SSHConfig
from .process import (
    register_process_cleanup,
    run_process_cleanup,
    ssh_subprocess_kwargs,
    terminate_ssh_process_tree,
)

log = logging.getLogger("ssh-manager.proxy")
_DRAIN_TIMEOUT = 2.0


def _is_windows() -> bool:
    return sys.platform == "win32"


def _option(config: SSHConfig, name: str) -> str | None:
    for key, value in config.extra_options.items():
        if key.casefold() == name:
            return str(value)
    for key, value in config.effective_config:
        if key.casefold() == name:
            return str(value)
    return None


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


def _expand_tokens(value: str, config: SSHConfig) -> str:
    tokens = {
        "%": "%",
        "h": config.hostname or _option(config, "hostname") or config.host_alias,
        "n": config.host_alias,
        "p": str(config.port or _option(config, "port") or 22),
        "r": config.user or _option(config, "user") or getpass.getuser(),
    }

    def expand(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in tokens:
            raise ValueError(f"Unsupported SSH proxy token %{token}")
        return tokens[token]

    return re.sub(r"%(.)", expand, value)


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
    python = windowless_python()
    if ntpath.basename(python).casefold() != "pythonw.exe":
        raise RuntimeError("Windows SSH proxies require the runtime's pythonw.exe")
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
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


class _ProxyBroker:
    def __init__(
        self, command: list[str], env: dict[str, str] | None, *, cwd: str | None = None,
        capability: str | None = None,
    ) -> None:
        self._command = command
        self._env = env
        self._cwd = cwd
        self.capability = capability or secrets.token_hex(32)
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task] = set()
        self._cleanups: set[asyncio.Task] = set()
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

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        process = None
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
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                **(no_window_kwargs() if _is_windows() else ssh_subprocess_kwargs()),
            )
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
            try:
                await asyncio.wait_for(
                    asyncio.gather(asyncio.shield(exited), asyncio.shield(outbound)),
                    timeout=_DRAIN_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.debug("SSH proxy did not drain after its transport closed")
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
            cleanup = asyncio.create_task(self._cleanup_connection(process, tasks, writer))
            self._cleanups.add(cleanup)
            cleanup.add_done_callback(self._cleanups.discard)
            await asyncio.shield(cleanup)

    async def _cleanup_connection(
        self, process: asyncio.subprocess.Process | None,
        tasks: list[asyncio.Task], writer: asyncio.StreamWriter,
    ) -> None:
        try:
            if process is not None:
                await terminate_ssh_process_tree(process)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

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


async def _watch_process(process: asyncio.subprocess.Process) -> None:
    try:
        await process.wait()
    finally:
        await run_process_cleanup(process)


_WATCHERS: set[asyncio.Task] = set()


def _watcher_done(task: asyncio.Task) -> None:
    _WATCHERS.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.warning(
            "SSH proxy cleanup failed",
            exc_info=(type(error), error, error.__traceback__),
        )


async def create_ssh_subprocess(
    *args: str, config: SSHConfig, **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Launch SSH, owning any Windows native proxy outside OpenSSH's child spawn."""
    proxy = getattr(config, "proxy_command", None)
    if not proxy and isinstance(config, SSHConfig):
        proxy = _option(config, "proxycommand")
    if not _is_windows() or not proxy or proxy.casefold() == "none":
        return await asyncio.create_subprocess_exec(*args, **ssh_subprocess_kwargs(**kwargs))
    shell = await _resolve_shell(kwargs.get("executable") or args[0])
    expanded = _expand_tokens(proxy, config)
    command = [shell, "-c", expanded] if shell else _split_windows_command(expanded)
    if not command:
        raise ValueError("SSH proxy command is empty")
    capability = secrets.token_hex(32)
    broker = _ProxyBroker(
        command, kwargs.get("env"), cwd=kwargs.get("cwd"), capability=capability,
    )
    try:
        port = await broker.start()
        process = await asyncio.create_subprocess_exec(
            *_broker_args(args, _client_command(port, capability, shell=shell is not None)),
            **ssh_subprocess_kwargs(**kwargs),
        )
    except BaseException:
        await broker.close()
        raise
    register_process_cleanup(process, broker.close)
    watcher = asyncio.create_task(_watch_process(process))
    _WATCHERS.add(watcher)
    watcher.add_done_callback(_watcher_done)
    return process
