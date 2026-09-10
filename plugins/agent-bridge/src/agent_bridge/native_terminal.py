"""Terminal presentation for an existing native execution; never launches one."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import ssl
import struct
import sys
import threading
import time
from urllib.parse import quote

from .acp_connect import _parse_ws_url
from .client import BridgeClient
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


async def connection(client: BridgeClient, execution_id: str, generation: str, input_source: Input) -> int | None:
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, BytesMessage, CloseConnection, Ping, RejectConnection, Request, TextMessage

    url = client._base.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    url += f"/api/v1/native-executions/{quote(execution_id, safe='')}/terminal?generation={quote(generation, safe='')}"
    host, port, target, tls = _parse_ws_url(url)
    reader, writer = await asyncio.open_connection(host, port, ssl=ssl.create_default_context() if tls else None)
    protocol = WSConnection(ConnectionType.CLIENT)
    accepted = asyncio.Event()
    write_lock = asyncio.Lock()

    async def send(event):
        async with write_lock:
            writer.write(protocol.send(event))
            await writer.drain()

    await send(Request(
        host=f"{host}:{port}", target=target, subprotocols=["native.v1"],
        extra_headers=[(b"Authorization", ("Bearer " + client._token).encode())],
    ))

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
    try:
        done, _ = await asyncio.wait([*tasks, detach], return_when=asyncio.FIRST_COMPLETED)
        if detach in done:
            return 0
        return tasks[0].result() if tasks[0] in done else None
    finally:
        if input_source.connected:
            input_source.last_disconnect = time.monotonic()
        input_source.connected = False
        for task in [*tasks, detach]:
            task.cancel()
        await asyncio.gather(*tasks, detach, return_exceptions=True)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def attach(execution_id: str, generation: str, *, reconnect_timeout: float = 120) -> int:
    from .__main__ import _get_client

    deadline = time.monotonic() + reconnect_timeout
    print("Native terminal: Ctrl+] detaches without stopping the execution.", file=sys.stderr)
    with raw_terminal():
        input_source = Input()
        while not input_source.detached.is_set():
            client = await asyncio.to_thread(_get_client)
            try:
                status = await asyncio.to_thread(client.native_status, execution_id, generation=generation)
                if status["state"] == "stopped":
                    return int(status.get("exitCode") or 0)
                result = await connection(client, execution_id, generation, input_source)
                if result is not None:
                    return result
            except Exception:
                deadline = max(deadline, input_source.last_disconnect + reconnect_timeout)
                if time.monotonic() >= deadline:
                    raise NativeError("unreachable", "Native reconnect budget exhausted; execution remains owned", 503)
            deadline = max(deadline, input_source.last_disconnect + reconnect_timeout)
            await asyncio.sleep(1)
    return 0
