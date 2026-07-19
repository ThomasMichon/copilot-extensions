"""Tests for the Windows named-pipe transport (Stage C).

The round-trip tests run only on Windows (named pipes are Windows-only); the
off-Windows guards run everywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
import uuid

import pytest

from agent_vault import winpipe


def _unique_pipe() -> str:
    return rf"\\.\pipe\agent-vault-test-{uuid.uuid4().hex}"


class _PipeEcho:
    """Run a proactor-loop pipe server in a thread that echoes a canned reply."""

    def __init__(self, pipe_path: str, response: dict):
        self.pipe_path = pipe_path
        self.response = response
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("pipe server did not start")

    def _run(self):
        self.loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.loop.run_forever()

    async def _start(self):
        async def handle(reader, writer):
            try:
                await reader.readline()
                writer.write((json.dumps(self.response) + "\n").encode())
                await writer.drain()
            finally:
                writer.close()

        self.servers = await winpipe.start_pipe_server(self.pipe_path, handle)
        self._ready.set()

    def close(self):
        if self.loop is not None:
            def _shutdown():
                for s in getattr(self, "servers", []):
                    with contextlib.suppress(Exception):
                        s.close()
                self.loop.stop()

            self.loop.call_soon_threadsafe(_shutdown)
            self.thread.join(timeout=3)
            with contextlib.suppress(Exception):
                self.loop.close()


# ---------------------------------------------------------------------------
# Off-Windows guards (run everywhere)
# ---------------------------------------------------------------------------


def test_pipe_send_none_off_windows(monkeypatch):
    monkeypatch.setattr(winpipe, "IS_WINDOWS", False)
    assert winpipe.pipe_send(r"\\.\pipe\whatever", {"action": "ping"}) is None


@pytest.mark.skipif(sys.platform == "win32", reason="off-Windows behavior")
def test_start_pipe_server_raises_off_windows():
    async def _call():
        await winpipe.start_pipe_server(r"\\.\pipe\x", lambda r, w: None)

    with pytest.raises(RuntimeError):
        asyncio.run(_call())


# ---------------------------------------------------------------------------
# Windows round-trip
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_pipe_round_trip():
    pipe = _unique_pipe()
    server = _PipeEcho(pipe, {"ok": True, "value": "pong"})
    try:
        resp = winpipe.pipe_send(pipe, {"action": "ping", "n": 7})
    finally:
        server.close()
    assert resp is not None
    assert resp["ok"] is True
    assert resp["value"] == "pong"


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_pipe_round_trip_multiple_connections():
    pipe = _unique_pipe()
    server = _PipeEcho(pipe, {"ok": True})
    try:
        for _ in range(4):
            assert winpipe.pipe_send(pipe, {"action": "ping"}) == {"ok": True}
    finally:
        server.close()


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_pipe_send_missing_pipe_returns_none():
    # No server bound -> CreateFileW fails -> None (caller falls through).
    assert winpipe.pipe_send(_unique_pipe(), {"action": "ping"}, timeout=1.0) is None
