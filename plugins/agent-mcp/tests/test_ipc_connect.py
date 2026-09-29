"""Tests for agent_mcp.ipc._connect's connection-refused retry (#4366).

A transient ``ConnectionRefusedError`` against a live, already-advertised
loopback-TCP endpoint has been observed under heavy Windows host load (see
``test_serve.py``'s real-socket tests). This is not an endpoint-before-
listener ordering race: ``serve.py`` awaits ``asyncio.start_server()`` --
which only returns once the listener is bound and accepting -- before it
writes the ``.endpoint`` sidecar, so the socket is always live by the time a
client can discover it. The underlying cause is unconfirmed, consistent with
transient host/OS-level contention. ``_connect`` retries a refused connection
a few times with a short backoff before giving up, regardless of cause.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_mcp import ipc


def _write_endpoint(tmp_path, port: int, secret: str = "tok"):  # noqa: S107
    sock_path = tmp_path / "serve.sock"
    (tmp_path / "serve.sock.endpoint").write_text(
        json.dumps({"port": port, "token": secret}), encoding="utf-8",
    )
    return sock_path


@pytest.mark.asyncio
@pytest.mark.skipif(ipc._HAS_AF_UNIX, reason="loopback-TCP transport is Windows-only")
async def test_connect_retries_past_a_transient_refusal(tmp_path, monkeypatch):
    """The first two attempts raise a mocked ``ConnectionRefusedError``; the
    third reaches a real, live listener (bound to an OS-assigned port, so
    there is no released-port reuse race) and must still succeed."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    sock_path = _write_endpoint(tmp_path, port)

    real_open_connection = asyncio.open_connection
    attempts = {"n": 0}

    async def _flaky_open_connection(host, p, *a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionRefusedError("refused")
        return await real_open_connection(host, p, *a, **kw)

    monkeypatch.setattr("asyncio.open_connection", _flaky_open_connection)
    try:
        _reader, writer, secret = await ipc._connect(sock_path)
        assert secret == "tok"
        writer.close()
    finally:
        server.close()
        await server.wait_closed()
    assert attempts["n"] == 3


@pytest.mark.asyncio
@pytest.mark.skipif(ipc._HAS_AF_UNIX, reason="loopback-TCP transport is Windows-only")
async def test_connect_gives_up_after_exhausting_retries(tmp_path, monkeypatch):
    """A connection that is always refused still raises -- and only after
    the exact bounded number of attempts, not indefinitely."""
    sock_path = _write_endpoint(tmp_path, 1)  # port is never dialed for real
    attempts = {"n": 0}

    async def _always_refused(*_a, **_kw):
        attempts["n"] += 1
        raise ConnectionRefusedError("refused")

    async def _no_sleep(*_a, **_kw):
        return None

    monkeypatch.setattr("asyncio.open_connection", _always_refused)
    monkeypatch.setattr("asyncio.sleep", _no_sleep)  # skip backoff

    with pytest.raises(ConnectionRefusedError):
        await ipc._connect(sock_path)
    assert attempts["n"] == 5
