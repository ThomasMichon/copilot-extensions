"""Tests for relay-port reclaim on a stale holder (#19)."""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import subprocess
import sys
import time

import pytest

from credential_relay.server import (
    CredentialRelayServer,
    _addr_in_use,
    _pid_on_port,
    _reclaim_port,
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# A child that binds the port, signals READY, then idles so it genuinely holds
# the listening socket until we evict it.
_HOLDER_SRC = (
    "import socket,sys,time\n"
    "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
    "s.bind(('127.0.0.1',{port})); s.listen(5)\n"
    "sys.stdout.write('READY\\n'); sys.stdout.flush()\n"
    "time.sleep(60)\n"
)


def _spawn_holder(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SRC.format(port=port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    # Block until the child confirms it has bound + is listening.
    line = proc.stdout.readline() if proc.stdout else ""
    assert "READY" in line, f"holder failed to bind port {port}"
    return proc


class TestAddrInUse:
    def test_eaddrinuse_detected(self):
        assert _addr_in_use(OSError(errno.EADDRINUSE, "in use")) is True

    def test_wsa_eaddrinuse_detected(self):
        assert _addr_in_use(OSError(10048, "in use")) is True

    def test_other_errno_not_detected(self):
        assert _addr_in_use(OSError(errno.EACCES, "denied")) is False


class TestPidOnPort:
    def test_finds_current_process_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(5)
        port = s.getsockname()[1]
        try:
            assert _pid_on_port(port) == os.getpid()
        finally:
            s.close()

    def test_free_port_has_no_listener(self):
        port = _free_port()
        assert _pid_on_port(port) is None


class TestReclaim:
    def test_reclaim_evicts_holder(self):
        port = _free_port()
        holder = _spawn_holder(port)
        try:
            # The actual listener pid may differ from holder.pid (the venv
            # python can be a launcher that re-execs), so assert on the real
            # owner and on the port being freed, not on holder.pid.
            listening_pid = _pid_on_port(port)
            assert listening_pid is not None
            assert _reclaim_port(port) is True
            assert _pid_on_port(port) is None  # port released
        finally:
            holder.kill()
            holder.wait(timeout=5)

    def test_reclaim_refuses_current_process(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(5)
        port = s.getsockname()[1]
        try:
            # Holder is us -- must never self-evict.
            assert _reclaim_port(port) is False
        finally:
            s.close()

    def test_start_reclaims_stale_port(self):
        port = _free_port()
        holder = _spawn_holder(port)
        try:
            server = CredentialRelayServer(port=port)

            async def _run() -> bool:
                await server.start()  # must reclaim + bind, not raise
                running = server.running
                await server.stop()
                return running

            assert asyncio.run(_run()) is True
            assert _pid_on_port(port) is None  # stale holder evicted, port free
        finally:
            holder.kill()
            holder.wait(timeout=5)
