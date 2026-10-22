"""Real local process/socket contract; deliberately not OpenSSH qualification."""

from __future__ import annotations

import asyncio
import socket
import sys

import pytest

import ssh_manager.relay_channel as legacy
import ssh_manager.reverse_candidates as candidate_mod
from ssh_manager.config_sources import SSHConfig
from ssh_manager.proxy import create_ssh_subprocess
from ssh_manager.relay_channel import SupervisedRelayForward, _SettleResult
from ssh_manager.reverse_candidates import FiniteReverseForward, ReverseEndpoint, ReverseEvidence


CHILD = """
import os, socket, sys
s = socket.socket()
try:
    s.bind(('127.0.0.1', int(sys.argv[1])))
except OSError:
    print('fixture-bind-refused', file=sys.stderr, flush=True)
    raise SystemExit(255)
s.listen()
print('fixture-bound', flush=True)
while True:
    connection, _ = s.accept()
    with connection:
        connection.sendall(('fixture-owner-' + str(os.getpid())).encode())
"""


@pytest.mark.integration
async def test_real_retained_process_recovers_without_retiring_foreign_listener(monkeypatch):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    occupied = held.getsockname()[1]
    # Test input only: the successful child's actual bind decides ownership.
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        alternate = candidate.getsockname()[1]
    processes = []

    async def spawn(*args, **kwargs):
        spec = args[args.index("-R") + 1]
        port = spec.split(":")[1]
        # The unchanged legacy forward omits an explicit bind address.
        if len(spec.split(":")) == 3:
            port = spec.split(":")[0]
        proc = await create_ssh_subprocess(
            sys.executable, "-c", CHILD, port, config=kwargs["config"],
            env=kwargs["env"], stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        processes.append(proc)
        return proc

    async def settle(self, proc):
        line = await asyncio.wait_for(proc.stdout.readline(), 5)
        if line == b"fixture-bound\n" or line == b"fixture-bound\r\n":
            return _SettleResult(True, "")
        stderr = await proc.stderr.read()
        await proc.wait()
        assert b"fixture-bind-refused" in stderr
        return _SettleResult(False, "remote port forwarding failed", True)

    async def observe(attempt):
        proc = processes[-1]
        assert attempt.process_pid == proc.pid
        if attempt.endpoint.port == occupied:
            assert held.fileno() >= 0 and proc.returncode == 255
            return ReverseEvidence(attempt.attempt_id, "occupied")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(attempt.endpoint.host, attempt.endpoint.port), 2,
        )
        try:
            assert await asyncio.wait_for(reader.read(), 2) == (
                "fixture-owner-" + str(attempt.process_pid)
            ).encode()
        finally:
            writer.close()
            await writer.wait_closed()
        return ReverseEvidence(attempt.attempt_id, "ready")

    async def no_backoff(*_args):
        return None

    monkeypatch.setattr(candidate_mod, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(legacy, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(SupervisedRelayForward, "_wait_settled", settle)
    monkeypatch.setattr(SupervisedRelayForward, "_sleep", no_backoff)
    fixed = SupervisedRelayForward(SSHConfig(host_alias="fixture"), occupied)
    dynamic = FiniteReverseForward(
        SSHConfig(host_alias="fixture"),
        (ReverseEndpoint("127.0.0.1", occupied), ReverseEndpoint("127.0.0.1", alternate)),
        request_id="native-operation-a", observe=observe, ready_timeout=20,
    )
    try:
        with pytest.raises(ConnectionError):
            await fixed.establish()
        assert len(processes) == 4
        await dynamic.establish()
        assert len(processes) == 6
        assert dynamic.reverse_status.state == "ready"
        assert dynamic.reverse_status.attempt.endpoint.port == alternate
        assert dynamic.process_pid == processes[-1].pid and processes[-1].returncode is None
        assert held.getsockname()[1] == occupied
        assert all(proc.returncode is not None for proc in processes[:-1])
        previous_attempt = dynamic.reverse_status.attempt.attempt_id
        await dynamic._terminate(processes[-1])
        assert dynamic.reverse_status.state == "unavailable"
        assert await dynamic._restart_with_backoff("owned test interruption") is True
        assert len(processes) == 8
        assert dynamic.reverse_status.state == "ready"
        assert dynamic.reverse_status.attempt.attempt_id != previous_attempt
        assert dynamic.reverse_status.attempt.request_id == "native-operation-a"
        await dynamic.stop()
        assert all(proc.returncode is not None for proc in processes)
        assert dynamic.reverse_status.state != "ready"
        # The foreign/previous listener remains held after owned cleanup.
        with socket.socket() as collision:
            with pytest.raises(OSError):
                collision.bind(("127.0.0.1", occupied))
    finally:
        await dynamic.stop()
        await fixed.stop()
        held.close()
