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
from ssh_manager.reverse_candidates import (
    FiniteReverseForward, ReverseEndpoint, ReverseEvidence, ReverseForwardError,
)


CHILD = """
import errno, os, socket, sys
s = socket.socket()
if os.name != 'nt':
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('127.0.0.1', int(sys.argv[1])))
    s.listen()
except OSError as error:
    if error.errno != errno.EADDRINUSE:
        raise
    print('fixture-bind-refused', file=sys.stderr, flush=True)
    raise SystemExit(255)
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
    extra_owners = []
    race_spawn_target = 0
    race_spawned = asyncio.Event()

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
        if race_spawn_target and len(processes) >= race_spawn_target:
            race_spawned.set()
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
        proc = next(proc for proc in processes if proc.pid == attempt.process_pid)
        assert attempt.process_pid == proc.pid
        if not attempt.process_settled:
            assert proc.returncode == 255
            if attempt.endpoint.port == occupied:
                assert held.fileno() >= 0
            else:
                await asyncio.wait_for(race_spawned.wait(), 2)
                assert any(other is not proc and other.returncode is None for other in processes)
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
        assert dynamic.reverse_status.attempt.target_port == occupied
        await dynamic.stop()
        assert all(proc.returncode is not None for proc in processes)
        assert dynamic.reverse_status.state != "ready"
        # The foreign/previous listener remains held after owned cleanup.
        with socket.socket() as collision:
            with pytest.raises(OSError):
                collision.bind(("127.0.0.1", occupied))
        # Two independent owners must arbitrate through actual child socket binds.
        race_spawn_target = len(processes) + 2
        for name in ("native-race-a", "native-race-b"):
            extra_owners.append(FiniteReverseForward(
                SSHConfig(host_alias="fixture"), (ReverseEndpoint("127.0.0.1", alternate),),
                request_id=name, observe=observe, ready_timeout=20,
            ))
        results = await asyncio.gather(
            *(owner.establish() for owner in extra_owners), return_exceptions=True,
        )
        assert sum(result is None for result in results) == 1
        failure = next(result for result in results if result is not None)
        assert isinstance(failure, ReverseForwardError)
        assert failure.status.reason == "candidates-exhausted"
        winner = next(owner for owner in extra_owners if owner.is_alive)
        loser = next(owner for owner in extra_owners if owner is not winner)
        await loser.stop()
        assert winner.is_alive
        await winner.stop()
        assert all(proc.returncode is not None for proc in processes)
    finally:
        for owner in extra_owners:
            await owner.stop()
        await dynamic.stop()
        await fixed.stop()
        held.close()
