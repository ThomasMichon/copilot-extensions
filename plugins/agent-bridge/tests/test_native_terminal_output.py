"""Synthetic PTY byte boundaries, not captured terminal replays."""

import asyncio
import io
import json
import os
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_bridge import native_terminal as terminal
from agent_procutil import no_window_kwargs


class ConsoleRaw:
    pass


class ConsoleBuffer:
    def __init__(self):
        self.raw = ConsoleRaw()
        self.writes = []
        self.flushes = 0

    def write(self, data):
        # Incomplete UTF-8 at this boundary can make Windows console flush spin.
        data.decode("utf-8", "strict")
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        self.flushes += 1


@pytest.fixture
def console(monkeypatch):
    monkeypatch.setattr(terminal, "_io", SimpleNamespace(_WindowsConsoleIO=ConsoleRaw))
    buffer = ConsoleBuffer()
    return SimpleNamespace(buffer=buffer)


@pytest.mark.parametrize("character", ["\u00a2", "\u2500", "\U0001f680"])
def test_console_retains_every_two_three_four_byte_split(console, character):
    encoded = character.encode()
    for boundary in range(1, len(encoded)):
        console.buffer.writes.clear()
        console.buffer.flushes = 0
        output = terminal.Output(console)
        output.write(encoded[:boundary])
        assert console.buffer.writes == [] and console.buffer.flushes == 0
        output.write(encoded[boundary:])
        output.finish()
        assert console.buffer.writes == [encoded]
        assert console.buffer.flushes == 1


def test_console_preserves_ansi_carriage_returns_and_one_byte_frames(console):
    data = "\x1b[2J\x1b[H\u00a2\u2500\U0001f680\r\n\x1b[31mred\x1b[0m".encode()
    output = terminal.Output(console)
    for byte in data:
        output.write(bytes([byte]))
    output.finish()
    assert b"".join(console.buffer.writes) == data


def test_console_invalid_and_truncated_utf8_have_explicit_replacement(console):
    output = terminal.Output(console)
    output.write(b"valid\xff\xe2\x94")
    assert b"".join(console.buffer.writes) == "valid\ufffd".encode()
    output.finish()
    output.finish()
    assert b"".join(console.buffer.writes) == "valid\ufffd\ufffd".encode()
    # A new connection replays an independent tail; never join its bytes to old state.
    next_connection = terminal.Output(console)
    next_connection.write("\u2500".encode())
    next_connection.finish()
    assert b"".join(console.buffer.writes) == "valid\ufffd\ufffd\u2500".encode()


def test_non_console_output_preserves_arbitrary_bytes_and_frame_boundaries(console):
    stream = SimpleNamespace(buffer=io.BytesIO())
    output = terminal.Output(stream)
    for data in (b"\xff", b"\xe2\x94", b"\x80", b"\x1b[0m\r\n"):
        output.write(data)
    output.finish()
    assert stream.buffer.getvalue() == b"\xff\xe2\x94\x80\x1b[0m\r\n"


def test_unbuffered_windows_console_is_also_detected(monkeypatch):
    class RawConsole(ConsoleRaw, ConsoleBuffer):
        def __init__(self):
            super().__init__()
            del self.raw

    monkeypatch.setattr(terminal, "_io", SimpleNamespace(_WindowsConsoleIO=ConsoleRaw))
    buffer = RawConsole()
    output = terminal.Output(SimpleNamespace(buffer=buffer))
    output.write(b"\xe2\x94")
    assert buffer.writes == []
    output.write(b"\x80")
    output.finish()
    assert buffer.writes == [b"\xe2\x94\x80"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["exit", "eof", "detach", "cancel"])
async def test_connection_acks_partial_codepoint_and_finalizes_its_tail(
    console, monkeypatch, ending,
):
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, BytesMessage, Request, TextMessage

    monkeypatch.setattr(terminal, "sys", SimpleNamespace(stdout=console))
    received = []
    finished = asyncio.Event()

    async def server(reader, writer):
        wire = WSConnection(ConnectionType.SERVER)
        try:
            while data := await reader.read(65536):
                wire.receive_data(data)
                for event in wire.events():
                    if isinstance(event, Request):
                        writer.write(wire.send(AcceptConnection(subprotocol="native.v1")))
                        writer.write(wire.send(BytesMessage(
                            data=struct.pack(">Q", 1) + b"\x1b[31m\xe2\x94",
                        )))
                        await writer.drain()
                    elif isinstance(event, BytesMessage):
                        received.append(bytes(event.data))
                    elif isinstance(event, TextMessage):
                        value = json.loads(event.data)
                        if value.get("type") == "ack" and value["sequence"] == 1:
                            writer.write(wire.send(BytesMessage(
                                data=struct.pack(">Q", 2) + b"\x80\x1b[0m\xf0\x9f",
                            )))
                            await writer.drain()
                        elif value.get("type") == "ack" and value["sequence"] == 2:
                            if ending == "exit":
                                writer.write(wire.send(TextMessage(
                                    data=json.dumps({"type": "exit", "exitCode": 0}),
                                )))
                                await writer.drain()
                            elif ending == "detach":
                                source.detached.set()
                                await reader.read()
                            elif ending == "cancel":
                                task.cancel()
                                await reader.read()
                            return
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    listener = await asyncio.start_server(server, "127.0.0.1", 0)
    source = SimpleNamespace(
        detached=asyncio.Event(), queue=asyncio.Queue(), connected=False,
        has_attached=False, last_disconnect=0.0,
    )
    source.queue.put_nowait(b"synthetic\r")
    client = SimpleNamespace(
        _base=f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}", _token="synthetic",
    )
    try:
        task = asyncio.create_task(terminal.connection(
            client, "execution", "generation", source, handshake_timeout=2,
        ))
        if ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 4)
        else:
            assert await asyncio.wait_for(task, 4) == (None if ending == "eof" else 0)
        await asyncio.wait_for(finished.wait(), 1)
        assert b"".join(console.buffer.writes) == "\x1b[31m\u2500\x1b[0m\ufffd".encode()
        assert b"".join(received) == b"synthetic\r"
        assert not source.connected
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.skipif(os.name != "nt", reason="real Windows console regression")
def test_real_windows_console_split_utf8_baseline_and_fixed_output():
    common = r'''
import json
output = open("CONOUT$", "w", encoding="utf-8")
print(json.dumps({"raw": type(output.buffer.raw).__name__, "phase": "before"}), flush=True)
'''
    baseline = common + r'''
output.buffer.write(b"x" * 4093 + b"\xe2\x94")
output.buffer.flush()
print(json.dumps({"phase": "after"}), flush=True)
'''
    # A hidden, test-owned console reproduces the baseline without any live PTY.
    try:
        original = subprocess.run(
            [sys.executable, "-c", baseline], capture_output=True, timeout=5,
            check=False, **no_window_kwargs(),
        )
        before = original.stdout
    except subprocess.TimeoutExpired as error:
        before = error.stdout
    # Future Python versions may repair the raw flush; the fixed path must work
    # either way. The bounded baseline currently times out on affected Python.
    assert json.loads(before.decode().splitlines()[0]) == {
        "raw": "_WindowsConsoleIO", "phase": "before",
    }
    fixed = common + r'''
from agent_bridge.native_terminal import Output
sink = Output(output)
sink.write(b"x" * 4093 + b"\xe2\x94")
sink.write(b"\x80")
for byte in "\u00a2\u2500\U0001f680".encode():
    sink.write(bytes([byte]))
sink.write(b"\xf0\x9f")
sink.finish()
import ctypes, msvcrt
from ctypes import wintypes
class Coord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.ReadConsoleOutputCharacterW.argtypes = [
    wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, Coord,
    ctypes.POINTER(wintypes.DWORD),
]
kernel.ReadConsoleOutputCharacterW.restype = wintypes.BOOL
text = ctypes.create_unicode_buffer(4095)
read = wintypes.DWORD()
if not kernel.ReadConsoleOutputCharacterW(
    msvcrt.get_osfhandle(output.fileno()), text, 4094, Coord(0, 0), ctypes.byref(read),
):
    raise ctypes.WinError(ctypes.get_last_error())
assert read.value == 4094 and text.value == "x" * 4093 + "\u2500"
print(json.dumps({"phase": "after", "unicodeVerified": True}), flush=True)
'''
    result = subprocess.run(
        [sys.executable, "-c", fixed], capture_output=True, timeout=5,
        check=True, **no_window_kwargs(),
    )
    assert [json.loads(line) for line in result.stdout.decode().splitlines()] == [
        {"raw": "_WindowsConsoleIO", "phase": "before"},
        {"phase": "after", "unicodeVerified": True},
    ]
