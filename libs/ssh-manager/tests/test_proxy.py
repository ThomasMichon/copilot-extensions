"""Contracts for owned, byte-transparent Windows SSH proxies."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_procutil import no_window_kwargs, windowless_python

from ssh_manager import SSHConfig
from ssh_manager import proxy
from ssh_manager.process import register_process_cleanup, run_process_cleanup


def test_loopback_routing_preserves_remote_identity_and_command():
    config = SSHConfig(
        host_alias="codespace.example",
        hostname="ssh.example",
        user="test-user",
        port=2222,
        proxy_command="gh cs ssh --stdio",
    )
    command = "printf '%s' '-p 99'"
    args = [
        "ssh", "-F", "example.config", "-p", "2222",
        "-o", "HostName=ssh.example", "-o", "Port=2222",
        "-o", "AddressFamily=inet6",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=example.known_hosts",
        "-i", r"C:\keys\%h\identity", "-o", "CertificateFile=certs/%h-cert.pub",
        config.ssh_target, command,
    ]
    routed = proxy._broker_args(args, "windowless-client")
    assert routed == ["ssh", "-o", "ProxyCommand=windowless-client", *args[1:]]


def test_loopback_routing_preserves_explicit_host_key_alias():
    config = SSHConfig(host_alias="example", extra_options={"HostKeyAlias": "stable"})
    args = ["ssh", "-o", "HostKeyAlias=stable", config.ssh_target]
    routed = proxy._broker_args(args, "windowless-client")
    assert [arg for arg in routed if arg.startswith("HostKeyAlias=")] == [
        "HostKeyAlias=stable",
    ]


def test_proxy_tokens_use_remote_not_loopback_identity():
    config = SSHConfig(
        host_alias="alias", hostname="remote.example", user="tester", port=2222,
    )
    assert proxy._expand_tokens("%h %n %p %r %%", config) == (
        "remote.example alias 2222 tester %"
    )
    with pytest.raises(ValueError, match="Unsupported SSH proxy token"):
        proxy._expand_tokens("%Z", config)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows argument parser")
def test_windows_proxy_command_preserves_quoted_arguments():
    args = [r"C:\Program Files\Example\gh.exe", "cs", "ssh", "a b", r"C:\path with spaces\x"]
    assert proxy._split_windows_command(subprocess.list2cmdline(args)) == args


@pytest.mark.asyncio
async def test_no_proxy_preserves_existing_subprocess_contract(monkeypatch):
    spawn = AsyncMock(return_value=object())
    monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", spawn)
    config = SSHConfig(host_alias="example")
    result = await proxy.create_ssh_subprocess(
        "ssh", "example", config=config, stdin=asyncio.subprocess.DEVNULL,
    )
    assert result is spawn.return_value
    assert spawn.await_args.args == ("ssh", "example")
    assert spawn.await_args.kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert "config" not in spawn.await_args.kwargs


@pytest.mark.asyncio
async def test_non_windows_proxy_keeps_native_ssh_routing(monkeypatch):
    monkeypatch.setattr(proxy, "_is_windows", lambda: False)
    spawn = AsyncMock(return_value=object())
    monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", spawn)
    config = SSHConfig(host_alias="example", proxy_command="some-proxy")
    await proxy.create_ssh_subprocess("ssh", "example", config=config)
    assert spawn.await_args.args == ("ssh", "example")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("spawn failed"), asyncio.CancelledError()])
async def test_ssh_spawn_failure_closes_broker(monkeypatch, error):
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_resolve_shell", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy, "_split_windows_command", lambda command: ["proxy"])
    monkeypatch.setattr(proxy, "_client_command", lambda port, capability, **kwargs: "client")
    broker = SimpleNamespace(start=AsyncMock(return_value=34567), close=AsyncMock())
    monkeypatch.setattr(proxy, "_ProxyBroker", lambda command, env, **kwargs: broker)
    monkeypatch.setattr(
        proxy.asyncio, "create_subprocess_exec", AsyncMock(side_effect=error),
    )
    with pytest.raises(type(error)):
        await proxy.create_ssh_subprocess(
            "ssh", "example",
            config=SSHConfig(host_alias="example", proxy_command="proxy"),
        )
    broker.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_cleanup_is_shared_between_concurrent_owners():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def close():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()

    process = SimpleNamespace()
    register_process_cleanup(process, close)
    first = asyncio.create_task(run_process_cleanup(process))
    await entered.wait()
    second = asyncio.create_task(run_process_cleanup(process))
    await asyncio.sleep(0)
    assert not first.done() and not second.done()
    release.set()
    await asyncio.gather(first, second)
    assert calls == 1


@pytest.mark.asyncio
async def test_proxy_preserves_binary_stream_and_reaps_on_close(monkeypatch):
    spawn = asyncio.create_subprocess_exec
    processes = []

    async def record(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", record)
    script = (
        "import sys\n"
        "while data := sys.stdin.buffer.read1(65536):\n"
        " sys.stdout.buffer.write(data)\n"
        " sys.stdout.buffer.flush()\n"
    )
    broker = proxy._ProxyBroker([sys.executable, "-u", "-c", script], None)
    port = await broker.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    payload = bytes(range(256)) * 513
    try:
        writer.write(broker.capability.encode("ascii") + payload)
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(len(payload)), timeout=5) == payload
    finally:
        writer.close()
        await writer.wait_closed()
        await broker.close()
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert not broker._clients


@pytest.mark.asyncio
async def test_proxy_cancellation_reaps_hung_child(monkeypatch):
    spawn = asyncio.create_subprocess_exec
    processes = []

    async def record(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", record)
    script = (
        "import sys,time; sys.stdout.buffer.write(b'ready\\n'); "
        "sys.stdout.buffer.flush(); time.sleep(60)"
    )
    broker = proxy._ProxyBroker([sys.executable, "-u", "-c", script], None)
    port = await broker.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(broker.capability.encode("ascii"))
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), timeout=5) == b"ready\n"
        await asyncio.wait_for(broker.close(), timeout=5)
        assert len(processes) == 1
        assert processes[0].returncode is not None
    finally:
        writer.close()
        await writer.wait_closed()
        await broker.close()


@pytest.mark.asyncio
async def test_broker_close_waits_for_already_started_cleanup(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    terminate = proxy.terminate_ssh_process_tree

    async def delayed_terminate(process):
        entered.set()
        await release.wait()
        await terminate(process)

    monkeypatch.setattr(proxy, "terminate_ssh_process_tree", delayed_terminate)
    monkeypatch.setattr(proxy, "_DRAIN_TIMEOUT", 0.01)
    script = "import time; print('ready',flush=True); time.sleep(60)"
    broker = proxy._ProxyBroker([sys.executable, "-u", "-c", script], None)
    port = await broker.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    closing = None
    try:
        writer.write(broker.capability.encode("ascii"))
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=5)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(entered.wait(), timeout=5)
        closing = asyncio.create_task(broker.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, timeout=5)
        assert not broker._cleanups
    finally:
        release.set()
        writer.close()
        await broker.close()
        if closing is not None:
            await closing


@pytest.mark.asyncio
async def test_invalid_capability_does_not_spawn_proxy(monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", spawn)
    broker = proxy._ProxyBroker(["unused"], None)
    port = await broker.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(b"invalid".ljust(64, b"x"))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        spawn.assert_not_awaited()
    finally:
        writer.close()
        await writer.wait_closed()
        await broker.close()


@pytest.mark.asyncio
async def test_stdio_client_streams_binary_before_stdin_closes():
    script = (
        "import sys\n"
        "while data := sys.stdin.buffer.read1(65536):\n"
        " sys.stdout.buffer.write(data)\n"
        " sys.stdout.buffer.flush()\n"
    )
    broker = proxy._ProxyBroker([sys.executable, "-u", "-c", script], None)
    port = await broker.start()
    client = await asyncio.create_subprocess_exec(
        windowless_python(), str(Path(proxy.__file__).with_name("proxy_client.py")),
        str(port), broker.capability,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    try:
        greeting = b"SSH-2.0-local-test\r\n"
        client.stdin.write(greeting)
        await client.stdin.drain()
        assert await asyncio.wait_for(
            client.stdout.readexactly(len(greeting)), timeout=5,
        ) == greeting
        payload = bytes(range(256)) * 513
        client.stdin.write(payload)
        await client.stdin.drain()
        client.stdin.close()
        output, error = await asyncio.wait_for(client.communicate(), timeout=5)
        assert output == payload
        assert client.returncode == 0, error
    finally:
        if client.returncode is None:
            client.kill()
            await client.wait()
        await broker.close()
