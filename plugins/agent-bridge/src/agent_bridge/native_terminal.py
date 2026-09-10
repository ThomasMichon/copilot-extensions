"""Terminal presentation for an existing native execution; never launches one."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import shutil
import ssl
import struct
import sys
import threading
import time
from urllib.parse import quote

from .acp_connect import _parse_ws_url
from .client import BridgeClient, BridgeClientError, BridgeConnectionError
from .native_store import NativeError


@contextlib.contextmanager
def raw_terminal():
    if not sys.stdin.isatty():
        yield
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handles = []
        try:
            for number, clear, enable in ((-10, 0x7, 0x200), (-11, 0, 0x4)):
                handle = kernel.GetStdHandle(number & 0xFFFFFFFF)
                previous = wintypes.DWORD()
                if kernel.GetConsoleMode(handle, ctypes.byref(previous)):
                    handles.append((handle, previous.value))
                    if not kernel.SetConsoleMode(handle, (previous.value & ~clear) | enable):
                        raise OSError("could not configure native terminal handles")
            yield
        finally:
            for handle, mode in handles:
                kernel.SetConsoleMode(handle, mode)
    else:
        import termios
        import tty

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)


class Input:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=32)
        self.detached = asyncio.Event()
        self.connected = False
        self.has_attached = False
        self.last_disconnect = 0.0
        self.loop = asyncio.get_running_loop()
        threading.Thread(target=self._read, name="native-terminal-input", daemon=True).start()

    def _read(self):
        try:
            while data := os.read(sys.stdin.fileno(), 4096):
                if b"\x1d" in data:  # explicit presentation detach, never execution stop
                    self.loop.call_soon_threadsafe(self.detached.set)
                    return
                if self.connected:
                    asyncio.run_coroutine_threadsafe(self.queue.put(data), self.loop).result()
        except (OSError, ValueError, RuntimeError):
            pass
        finally:
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self.detached.set)


async def connection(
    client: BridgeClient, execution_id: str, generation: str, input_source: Input,
    *, handshake_timeout: float | None = None,
) -> int | None:
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, BytesMessage, CloseConnection, Ping, RejectConnection, Request, TextMessage

    url = client._base.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    url += f"/api/v1/native-executions/{quote(execution_id, safe='')}/terminal?generation={quote(generation, safe='')}"
    host, port, target, tls = _parse_ws_url(url)
    deadline = time.monotonic() + handshake_timeout if handshake_timeout is not None else None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ssl.create_default_context() if tls else None),
        timeout=handshake_timeout,
    )
    protocol = WSConnection(ConnectionType.CLIENT)
    accepted = asyncio.Event()
    write_lock = asyncio.Lock()

    async def send(event):
        async with write_lock:
            writer.write(protocol.send(event))
            await writer.drain()

    async def outbound():
        await accepted.wait()
        while True:
            data = await input_source.queue.get()
            await send(BytesMessage(data=data))

    async def resize():
        await accepted.wait()
        previous = None
        while True:
            size = shutil.get_terminal_size((80, 24))
            if size != previous:
                await send(TextMessage(data=json.dumps({"type": "resize", "rows": size.lines, "columns": size.columns})))
                previous = size
            await asyncio.sleep(1)

    async def inbound():
        binary = bytearray()
        text = []
        while data := await reader.read(65536):
            protocol.receive_data(data)
            for event in protocol.events():
                if isinstance(event, AcceptConnection):
                    accepted.set()
                    input_source.connected = True
                    input_source.has_attached = True
                elif isinstance(event, RejectConnection):
                    raise NativeError("not_ready", "Native terminal is not available; execution is retained", 503)
                elif isinstance(event, BytesMessage):
                    binary.extend(event.data)
                    if len(binary) > 1024 * 1024:
                        raise NativeError("invalid_frame", "Native terminal frame exceeds its budget")
                    if event.message_finished:
                        if len(binary) < 8:
                            raise NativeError("invalid_frame", "Native terminal sequence is missing")
                        sequence = struct.unpack(">Q", binary[:8])[0]
                        sys.stdout.buffer.write(binary[8:])
                        sys.stdout.buffer.flush()
                        binary.clear()
                        await send(TextMessage(data=json.dumps({"type": "ack", "sequence": sequence})))
                elif isinstance(event, TextMessage):
                    text.append(event.data)
                    if event.message_finished:
                        value = json.loads("".join(text))
                        text = []
                        if value.get("type") == "exit":
                            return int(value["exitCode"])
                elif isinstance(event, Ping):
                    await send(event.response())
                elif isinstance(event, CloseConnection):
                    return None
        return None

    tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound()), asyncio.create_task(resize())]
    detach = asyncio.create_task(input_source.detached.wait())
    handshake = asyncio.create_task(accepted.wait())
    try:
        await asyncio.wait_for(send(Request(
            host=f"{host}:{port}", target=target, subprotocols=["native.v1"],
            extra_headers=[(b"Authorization", ("Bearer " + client._token).encode())],
        )), timeout=max(0, deadline - time.monotonic()) if deadline is not None else None)
        done, _ = await asyncio.wait(
            [*tasks, detach, handshake], return_when=asyncio.FIRST_COMPLETED,
            timeout=max(0, deadline - time.monotonic()) if deadline is not None else None,
        )
        if not done:
            raise TimeoutError("Native terminal handshake deadline expired")
        if detach in done:
            return 0
        if handshake not in done:
            return tasks[0].result() if tasks[0] in done else None
        done, _ = await asyncio.wait([*tasks, detach], return_when=asyncio.FIRST_COMPLETED)
        if detach in done:
            return 0
        return tasks[0].result() if tasks[0] in done else None
    finally:
        if input_source.connected:
            input_source.last_disconnect = time.monotonic()
        input_source.connected = False
        for task in [*tasks, detach, handshake]:
            task.cancel()
        await asyncio.gather(*tasks, detach, handshake, return_exceptions=True)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


_DETACHED = object()


async def _until_detach(operation, input_source: Input, timeout: float):
    task = asyncio.create_task(operation)
    detach = asyncio.create_task(input_source.detached.wait())
    try:
        done, _ = await asyncio.wait(
            [task, detach], timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
        )
        if detach in done:
            return _DETACHED
        if task not in done:
            raise TimeoutError("Native observation deadline expired")
        return task.result()
    finally:
        for pending in (task, detach):
            pending.cancel()
        await asyncio.gather(task, detach, return_exceptions=True)


async def attach(
    execution_id: str, generation: str, *, reconnect_timeout: float = 120,
    preparation_timeout: float = 1800,
) -> int:
    from .__main__ import _get_client

    if any(not math.isfinite(value) or value <= 0 for value in (preparation_timeout, reconnect_timeout)):
        raise ValueError("Native preparation and reconnect budgets must be positive and finite")
    preparation_deadline = time.monotonic() + preparation_timeout
    print("Native terminal: Ctrl+] detaches without stopping the execution.", file=sys.stderr)
    with raw_terminal():
        input_source = Input()

        def remaining() -> float:
            deadline = (
                input_source.last_disconnect + reconnect_timeout
                if input_source.has_attached else preparation_deadline
            )
            budget = deadline - time.monotonic()
            if budget <= 0:
                kind = "reconnect" if input_source.has_attached else "preparation"
                raise NativeError(
                    "unreachable" if input_source.has_attached else "preparation_timeout",
                    f"Native {kind} budget exhausted; execution remains owned", 503,
                )
            return budget

        while not input_source.detached.is_set():
            try:
                budget = remaining()
                client = await _until_detach(
                    asyncio.to_thread(_get_client, ensure=False), input_source, budget,
                )
                if client is _DETACHED:
                    return 0
                budget = remaining()
                status = await _until_detach(
                    asyncio.to_thread(client.native_status, execution_id, generation=generation),
                    input_source, budget,
                )
                if status is _DETACHED or input_source.detached.is_set():
                    return 0
                if not isinstance(status, dict) or status.get("state") not in {
                    "starting", "ready", "unrepresented", "unreachable", "stopping", "stopped", "rejected",
                }:
                    raise NativeError("invalid_status", "Native status is invalid; refusing replacement", 503)
                if (
                    status.get("executionId", execution_id) != execution_id
                    or status.get("generation", generation) != generation
                ):
                    raise NativeError("identity_mismatch", "Native execution identity changed")
                if status["state"] == "stopped":
                    return int(status.get("exitCode") or 0)
                if status["state"] == "rejected":
                    raise NativeError("venue_busy", "Native execution was rejected; no replacement will be launched")
                budget = remaining()
                if status["state"] in {"ready", "unrepresented"}:
                    result = await connection(
                        client, execution_id, generation, input_source, handshake_timeout=budget,
                    )
                    if result is not None:
                        return result
            except NativeError as exc:
                if exc.code != "not_ready":
                    raise
            except BridgeClientError as exc:
                if exc.status < 500 and exc.status not in {408, 429}:
                    raise
            except (OSError, BridgeConnectionError):
                pass
            if input_source.detached.is_set():
                return 0
            await asyncio.sleep(min(1, remaining()))
    return 0
