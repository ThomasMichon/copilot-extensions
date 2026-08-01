"""Tests for relay-port reclaim on a stale holder (#19)."""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import subprocess
import sys

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

    def test_start_binds_ephemeral_when_port_held_by_live_occupant(self):
        # Hold the preferred port in THIS process so _reclaim_port refuses to
        # evict it (never self-evicts). start() must fall back to an OS-assigned
        # ephemeral port and record it on self.port, rather than raising (#540).
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(5)
        port = s.getsockname()[1]
        try:
            server = CredentialRelayServer(port=port)

            async def _run() -> tuple[bool, int]:
                await server.start()  # must bind ephemeral, not raise
                running = server.running
                bound = server.port
                await server.stop()
                return running, bound

            running, bound = asyncio.run(_run())
            assert running is True
            assert bound not in (0, port)  # fell back to a distinct ephemeral port
        finally:
            s.close()

    def test_start_with_port_zero_binds_and_reads_back_ephemeral(self):
        # The dynamic default (dotfiles #694): port 0 -> the OS assigns an
        # ephemeral port and start() reads the actually-bound port back into
        # self.port so relay_state can publish the real port to consumers.
        server = CredentialRelayServer(port=0)

        async def _run() -> tuple[bool, int]:
            await server.start()
            running = server.running
            bound = server.port
            await server.stop()
            return running, bound

        running, bound = asyncio.run(_run())
        assert running is True
        assert isinstance(bound, int) and bound > 0  # real port, not the 0 sentinel
