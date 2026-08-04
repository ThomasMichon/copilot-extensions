"""Tests for the supervised credential-relay reverse-forward channel."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ssh_manager import SSHConfig, SupervisedRelayForward


class _FakeStderr:
    def __init__(self, data: bytes = b"") -> None:
        self._chunks = [data] if data else []

    async def read(self, _limit: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    def __init__(self, *, returncode: int | None = None, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = _FakeStderr(stderr)
        self.stdout = SimpleNamespace()
        self.killed = False
        self._waiter: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        if returncode is not None:
            self._waiter.set_result(returncode)

    async def wait(self) -> int:
        return await self._waiter

    def kill(self) -> None:
        self.killed = True
        self.set_exit(-9)

    def set_exit(self, returncode: int = 255) -> None:
        self.returncode = returncode
        if not self._waiter.done():
            self._waiter.set_result(returncode)


def _config() -> SSHConfig:
    return SSHConfig(
        host_alias="cs-one",
        user="codespace",
        port=2222,
        identity_file="id_ed25519",
        config_file="ssh_config",
        extra_options={
            "ControlMaster": "auto",
            "ControlPath": "cm-%r@%h:%p",
            "ControlPersist": "yes",
            "UserKnownHostsFile": "known_hosts",
        },
    )


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


@pytest.mark.asyncio
async def test_argv_shape_reverse_only(monkeypatch) -> None:
    calls: list[tuple[tuple[str, ...], dict]] = []
    proc = _FakeProcess()

    async def fake_create(*args, **kwargs):
        calls.append((args, kwargs))
        return proc

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(_config(), 51234, ready_timeout=0.01)

    await relay.establish()
    await relay.stop()

    argv = list(calls[0][0])
    assert "-N" in argv
    assert "-R" in argv
    assert "51234:127.0.0.1:51234" in argv
    assert "-L" not in argv
    assert "ExitOnForwardFailure=yes" not in argv
    assert "ServerAliveInterval=30" in argv
    joined = " ".join(argv)
    assert "ControlMaster" not in joined
    assert "ControlPath" not in joined
    assert "ControlPersist" not in joined
    assert "UserKnownHostsFile=known_hosts" in argv


@pytest.mark.asyncio
async def test_self_heals_when_process_exits(monkeypatch) -> None:
    procs = [_FakeProcess(), _FakeProcess()]
    calls: list[tuple[str, ...]] = []

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        return procs[len(calls) - 1]

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(
        _config(), 51234, monitor_interval=0.01, ready_timeout=0.01,
    )

    await relay.start()
    procs[0].set_exit()
    await _wait_for(lambda: len(calls) == 2)
    await asyncio.sleep(0.05)
    await relay.stop()

    assert procs[0].killed is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_backoff_grows_caps_and_resets(monkeypatch) -> None:
    relay = SupervisedRelayForward(
        _config(), 51234, backoff_base=0.5, backoff_max=1.0,
    )
    outcomes = [
        ConnectionError("one"),
        ConnectionError("two"),
        ConnectionError("three"),
        None,
        ConnectionError("after reset"),
        None,
    ]
    sleeps: list[float] = []

    async def establish() -> None:
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        relay._proc = _FakeProcess()

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(relay, "establish", establish)
    monkeypatch.setattr(relay, "_sleep", sleep)

    await relay._restart_with_backoff("test")
    await relay._restart_with_backoff("test")

    assert sleeps == [0.5, 1.0, 1.0, 0.5]


@pytest.mark.asyncio
async def test_serving_probe_false_reestablishes(monkeypatch) -> None:
    procs = [_FakeProcess(), _FakeProcess()]
    calls: list[tuple[str, ...]] = []
    probe_results = [False, True, True]

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        return procs[len(calls) - 1]

    async def probe() -> bool:
        return probe_results.pop(0) if probe_results else True

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(
        _config(),
        51234,
        monitor_interval=0.01,
        ready_timeout=0.01,
        serving_probe=probe,
    )

    await relay.start()
    await _wait_for(lambda: len(calls) == 2)
    await asyncio.sleep(0.05)
    await relay.stop()

    assert procs[0].killed is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_serving_probe_true_does_not_reestablish(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    probe_calls = 0

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        return _FakeProcess()

    async def probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(
        _config(),
        51234,
        monitor_interval=0.01,
        ready_timeout=0.01,
        serving_probe=probe,
    )

    await relay.start()
    await asyncio.sleep(0.05)
    await relay.stop()

    assert len(calls) == 1
    assert probe_calls > 0


@pytest.mark.asyncio
async def test_stop_cancels_monitor_and_process_idempotently(monkeypatch) -> None:
    proc = _FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(
        _config(), 51234, monitor_interval=0.01, ready_timeout=0.01,
    )

    await relay.start()
    await relay.stop()
    await relay.stop()

    assert proc.killed is True
    assert relay.is_alive is False
    assert relay._monitor_task is None


@pytest.mark.asyncio
async def test_establish_failure_raises_with_stderr(monkeypatch) -> None:
    proc = _FakeProcess(returncode=255, stderr=b"remote bind denied")

    async def fake_create(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(_config(), 51234, ready_timeout=0.01)

    with pytest.raises(ConnectionError, match="remote bind denied"):
        await relay.establish()


@pytest.mark.asyncio
async def test_establish_retries_remote_forward_failure_then_succeeds(
    monkeypatch,
) -> None:
    warning = b"Warning: remote port forwarding failed for listen port 51234\n"
    procs = [_FakeProcess(stderr=warning), _FakeProcess()]
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        return procs[len(calls) - 1]

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(
        _config(), 51234, ready_timeout=0.01, backoff_base=0.1,
    )
    monkeypatch.setattr(relay, "_sleep", sleep)

    await relay.establish()
    await relay.stop()

    assert len(calls) == 2
    assert procs[0].killed is True
    assert procs[1].killed is True
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_establish_raises_after_bounded_remote_forward_failures(
    monkeypatch,
) -> None:
    warning = b"remote port forwarding failed for listen port 51234\r\n"
    procs = [_FakeProcess(stderr=warning) for _ in range(4)]
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        return procs[len(calls) - 1]

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "ssh_manager.relay_channel.asyncio.create_subprocess_exec",
        fake_create,
    )
    relay = SupervisedRelayForward(
        _config(), 51234, ready_timeout=0.01, backoff_base=0.1,
    )
    monkeypatch.setattr(relay, "_sleep", sleep)

    with pytest.raises(ConnectionError, match="remote port forwarding failed"):
        await relay.establish()

    assert len(calls) == 4
    assert all(proc.killed for proc in procs)
    assert sleeps == [2.0, 2.0, 2.0]


@pytest.mark.asyncio
async def test_live_process_with_remote_forward_failure_is_not_ready(
) -> None:
    proc = _FakeProcess(stderr=b"Remote port forwarding failed for listen port 51234\n")
    relay = SupervisedRelayForward(_config(), 51234, ready_timeout=0.01)

    settled = await relay._wait_settled(proc)

    assert settled.ready is False
    assert settled.remote_forward_failed is True
    assert "Remote port forwarding failed" in settled.stderr
    assert proc.returncode is None
