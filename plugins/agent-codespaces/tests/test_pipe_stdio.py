"""Regression test for the ACP stdio relay (#46.6).

``_pipe_stdio`` relays the SSH/ACP channel between agent-bridge and the
codespace. A prior ``fut.result(timeout=30)`` on the stdout read terminated
the relay after 30s of *quiet* (a long, output-buffered remote build/test),
silently collapsing the dispatch -- and on Python 3.11+ the resulting
``TimeoutError`` (an ``OSError`` subclass) was swallowed, hiding it. The relay
must forward across arbitrary quiet gaps and exit only on EOF.
"""

from __future__ import annotations

import asyncio
import io
import sys

from agent_codespaces import __main__ as m


class _FakeStdin:
    def write(self, data):  # noqa: ANN001
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.eof = asyncio.Event()

    async def read(self, _n):
        if self._chunks:
            chunk = self._chunks.pop(0)
            if chunk == b"__gap__":
                # A quiet period mid-stream must NOT terminate the relay.
                await asyncio.sleep(0.15)
                chunk = self._chunks.pop(0)
            return chunk
        self.eof.set()
        return b""


class _FakeProc:
    def __init__(self, chunks):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(chunks)
        self.returncode = 0

    async def wait(self):
        await self.stdout.eof.wait()


async def test_pipe_stdio_forwards_across_quiet_gap(monkeypatch):
    written = bytearray()

    class _OutBuf:
        def write(self, b):  # noqa: ANN001
            written.extend(b)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", type("S", (), {"buffer": _OutBuf()})())
    # BytesIO has no real fileno(); _forward_in catches that and exits cleanly,
    # which is fine -- this test exercises the stdout pump.
    monkeypatch.setattr(sys, "stdin", type("I", (), {"buffer": io.BytesIO(b"")})())

    proc = _FakeProc([b"hello ", b"__gap__", b"world"])
    await asyncio.wait_for(m._pipe_stdio(proc), timeout=5)

    assert bytes(written) == b"hello world"
