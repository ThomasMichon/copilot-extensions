"""Real local requests retain direct server admission through native settlement."""

import json
import os
import subprocess
import sys

import pytest


HARNESS = r"""
import asyncio, contextlib, json, os, subprocess, sys, threading, uuid
from pathlib import Path
from agent_procutil import daemon_instances as api
from agent_vault import config, keepassxc, service

state, mode, transport = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
state.mkdir()
registry = state / "registry"
os.environ[config.HOME_ENV] = str(state)
service.PID_FILE = str(state / "vault.pid")
service.SOCKET_PATH = str(state / "vault.sock")
service.PIPE_PATH = r"\\.\pipe\vault-settlement-" + uuid.uuid4().hex
service.run_dir = lambda: state / "run"
service.configured_tcp_port = lambda: 0
entered, release, settled = (threading.Event() for _ in range(3))
vault = service.VaultService()
vault.ttl_override = 0
kpdb = str(state / "fixture.kdbx")
vault.cli._cli_path = "fixture-keepassxc"
vault.cli.set_password(kpdb, "fixture-password")
original_run = subprocess.run
native_calls = []
tcp_address = []
listeners = []
original_tcp = asyncio.start_server

async def record_tcp(*args, **kwargs):
    listener = await original_tcp(*args, **kwargs)
    tcp_address.append(listener.sockets[0].getsockname())
    listeners.append(("server", listener))
    return listener

asyncio.start_server = record_tcp
if os.name != "nt":
    original_unix = asyncio.start_unix_server
    async def record_unix(*args, **kwargs):
        listener = await original_unix(*args, **kwargs)
        listeners.append(("server", listener))
        return listener
    asyncio.start_unix_server = record_unix
else:
    original_pipe_bind = service.start_pipe_server
    async def record_pipes(*args, **kwargs):
        pipes = await original_pipe_bind(*args, **kwargs)
        listeners.extend(("pipe", pipe) for pipe in pipes)
        return pipes
    service.start_pipe_server = record_pipes

def native(argv, **options):
    if argv[0] != "fixture-keepassxc":
        return original_run(argv, **options)
    assert argv[1] == "show", argv
    assert vault._credential_lock._is_owned()
    native_calls.append("entered")
    entered.set()
    assert release.wait(12), "native fixture was not released"
    settled.set()
    return subprocess.CompletedProcess(argv, 0, stdout="fixture-value\n", stderr="")

keepassxc.subprocess.run = native

def rows():
    return [row for row in api.inspect_instances(registry)
            if row.get("pid") == str(os.getpid())]

def running():
    current = rows()
    assert len(current) == 1 and current[0]["state"] == "running", current

async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)

async def connect():
    if transport == "pipe":
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        handle, _ = await loop.create_pipe_connection(lambda: protocol, service.PIPE_PATH)
        return reader, asyncio.StreamWriter(handle, protocol, reader, loop)
    if transport == "unix":
        return await asyncio.open_unix_connection(service.SOCKET_PATH)
    host, port = tcp_address[0]
    return await asyncio.open_connection(host, port)

async def main():
    writers = []
    unrelated_release = threading.Event()
    unrelated_started = threading.Event()
    def unrelated():
        unrelated_started.set()
        unrelated_release.wait(12)
    cohost = asyncio.get_running_loop().run_in_executor(None, unrelated)
    await until(unrelated_started.is_set)
    startup_entered = asyncio.Event()
    startup_release = asyncio.Event()
    original_context = service.resolve_context
    close_release = threading.Event()
    close_attempts = []
    close_failed = threading.Event()
    close_mode = mode.startswith("close-") or mode == "pipe-close-error"
    class FixtureCloseError(BaseException):
        pass
    close_error = (
        FixtureCloseError("fixture intake close failed") if mode == "close-base-error"
        else OSError("fixture intake close failed")
    )

    # Fail after an actual listener has accepted a request, not before startup.
    if mode.startswith("startup"):
        if os.name == "nt":
            original_pipe = service.start_pipe_server
            async def startup_pipe(*args):
                pipes = await original_pipe(*args)
                startup_entered.set()
                try:
                    await startup_release.wait()
                    raise RuntimeError("fixture startup failed")
                except BaseException:
                    for pipe in pipes:
                        pipe.close()
                    raise
            service.start_pipe_server = startup_pipe
        else:
            original_start = asyncio.start_server
            async def startup_tcp(*args, **kwargs):
                startup_entered.set()
                await startup_release.wait()
                raise RuntimeError("fixture startup failed")
            asyncio.start_server = startup_tcp
        transport_local = "pipe" if os.name == "nt" else "unix"
        assert transport == transport_local

    server = asyncio.create_task(service.run_server(vault))
    response_task = None
    try:
        if mode.startswith("startup"):
            await startup_entered.wait()
        else:
            await until(lambda: (state / "run" / "endpoint.json").exists())
        reader, writer = await connect()
        writers.append(writer)
        writer.write((json.dumps(dict(action="get", kpdb=kpdb, entry="fixture")) + "\n").encode())
        await writer.drain()
        response_task = asyncio.create_task(reader.readline())
        await until(entered.is_set)
        running()

        # An accepted but idle connection must not hold shutdown open.
        idle_reader, idle_writer = await connect()
        writers.append(idle_writer)
        await asyncio.sleep(.03)
        if close_mode:
            target = next(listener for kind, listener in listeners
                          if kind == ("pipe" if mode == "pipe-close-error" else "server"))
            for _, listener in listeners:
                original_close = listener.close
                def observed_close(listener=listener, original=original_close):
                    close_attempts.append(id(listener))
                    if listener is target and (
                        not close_failed.is_set()
                        or mode == "close-unknown" and not close_release.is_set()
                    ):
                        close_failed.set()
                        raise close_error
                    return original()
                listener.close = observed_close
        handler = next(task for task in asyncio.all_tasks()
                       if task.get_coro().__name__ == "handle_client"
                       and not task.done())
        if mode == "handler-cancel":
            # Identify the native request, not the second idle reader.
            handler = next(task for task in asyncio.all_tasks()
                           if task.get_coro().__name__ == "handle_client"
                           and task.get_coro().cr_frame.f_locals.get("request", {}).get("action") == "get")
            handler.cancel()
            await asyncio.sleep(.03)
            handler.cancel()

        if mode.startswith("startup"):
            if mode == "startup-cancel":
                server.cancel()
            else:
                # The Windows pipe bind is best-effort; context failure is fatal.
                if os.name == "nt":
                    def fail_context(*args, **kwargs):
                        raise RuntimeError("fixture startup failed")
                    service.resolve_context = fail_context
                startup_release.set()
        elif mode == "cancel":
            server.cancel()
        else:
            stop_reader, stop_writer = await connect()
            writers.append(stop_writer)
            stop_writer.write(b'{"action":"stop"}\n')
            await stop_writer.drain()
            assert json.loads(await asyncio.wait_for(stop_reader.readline(), 3))["ok"]

        await asyncio.sleep(.65)
        assert not server.done(), "direct run_server finished before its native request"
        running()
        assert not settled.is_set()
        assert not vault._credential_lock.acquire(False), "real request lost its credential lock"
        if mode in {"cancel", "startup-cancel"} or close_mode:
            if close_mode:
                assert close_failed.is_set()
                assert {id(listener) for _, listener in listeners} <= set(close_attempts)
            server.cancel()
            await asyncio.sleep(.03)
            server.cancel()
        await asyncio.sleep(.03)
        running()
        assert not server.done()
        assert await asyncio.wait_for(idle_reader.read(), 2) == b""
        release.set()
        if mode == "close-unknown":
            await until(settled.is_set)
            await until(response_task.done)
            assert vault.cache
            running()
            assert not server.done(), "failed intake closure was treated as settled"
            assert Path(service.PID_FILE).exists()
            if os.name == "nt":
                late_reader, late_writer = await asyncio.open_connection(*tcp_address[0])
            else:
                late_reader, late_writer = await asyncio.open_unix_connection(service.SOCKET_PATH)
            writers.append(late_writer)
            late_writer.write(
                (json.dumps(dict(action="get", kpdb=kpdb, entry="fixture")) + "\n").encode()
            )
            with contextlib.suppress(ConnectionError):
                await late_writer.drain()
            with contextlib.suppress(ConnectionError):
                assert await asyncio.wait_for(late_reader.read(), 2) == b""
            assert native_calls == ["entered"], "unclosed intake admitted new native work"
            close_release.set()
        failure_observed = False
        try:
            await asyncio.wait_for(asyncio.shield(server), 4)
        except (OSError, FixtureCloseError) as exc:
            assert close_mode and exc is close_error, exc
            failure_observed = True
        except asyncio.CancelledError:
            assert mode in {"cancel", "startup-cancel"}
        except RuntimeError as exc:
            assert mode.startswith("startup") and str(exc) == "fixture startup failed"
        if close_mode:
            assert failure_observed, "first intake-close error was lost"
        assert settled.is_set() and vault.cache
        if mode != "handler-cancel":
            response = json.loads(await asyncio.wait_for(response_task, 2))
            assert response["ok"], response
        else:
            assert await asyncio.wait_for(response_task, 2) == b""
        assert native_calls == ["entered"], native_calls
        assert rows()[0]["state"] == "finished", rows()
        assert not Path(service.PID_FILE).exists()
        assert not cohost.done(), "server shut down the embedding loop's executor"
        print(json.dumps({"mode": mode, "transport": transport, "state": rows()[0]["state"],
                          "native_calls": len(native_calls), "cohost_preserved": True}))
    finally:
        service.resolve_context = original_context
        release.set()
        close_release.set()
        startup_release.set()
        unrelated_release.set()
        vault._shutdown = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(server), 4)
        if response_task is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(response_task, 2)
        for writer in writers:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        await cohost

with api.daemon_test_lifecycle(owner="vault-direct-settlement", registry_root=registry) as overlay:
    os.environ.update(overlay)
    asyncio.run(main())
"""


@pytest.mark.parametrize(
    ("mode", "transport"),
    [
        ("ordinary", "tcp"),
        ("ordinary", "pipe" if sys.platform == "win32" else "unix"),
        ("cancel", "pipe" if sys.platform == "win32" else "tcp"),
        ("handler-cancel", "pipe" if sys.platform == "win32" else "tcp"),
        ("startup-error", "pipe" if sys.platform == "win32" else "unix"),
        ("startup-cancel", "pipe" if sys.platform == "win32" else "unix"),
        ("close-error", "pipe" if sys.platform == "win32" else "tcp"),
        ("close-base-error", "pipe" if sys.platform == "win32" else "tcp"),
        ("close-unknown", "pipe" if sys.platform == "win32" else "tcp"),
        pytest.param(
            "pipe-close-error", "pipe",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="native Windows pipe"),
        ),
    ],
)
def test_direct_server_retains_native_request_until_settled(tmp_path, mode, transport):
    result = subprocess.run(
        [sys.executable, "-B", "-c", HARNESS, str(tmp_path / "vault"), mode, transport],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=25,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout.splitlines()[-1])
    assert summary["state"] == "finished"
    assert summary["cohost_preserved"]
