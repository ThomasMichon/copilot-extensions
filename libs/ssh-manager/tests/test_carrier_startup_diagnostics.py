"""Bounded, payload-free diagnostics for the framed carrier's startup."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import traceback
from pathlib import Path

import pytest

from ssh_manager.carrier import (
    CarrierUnavailable,
    PersistentCarrier,
    encode_envelope,
    hello_envelope,
)
from ssh_manager.process import terminate_ssh_process_tree


class _Input:
    def write(self, _data):
        pass

    async def drain(self):
        pass


class _FailedProcess:
    def __init__(self, stderr, returncode=127):
        self.stdin = _Input()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        (b"command not found", "command_unavailable"),
        (b"sh: 1: /path/to/agent-bridge: not found", "command_unavailable"),
        (b"is not recognized as an internal or external command", "command_unavailable"),
        (b"Permission denied (publickey)", "authentication_rejected"),
        (b"x" * 1010 + b"Permission denied", "authentication_rejected"),
        (b"Host key verification failed", "host_key_rejected"),
        (b"unexpected bootstrap output", "process_exited"),
    ],
)
async def test_failed_hello_reports_only_allowlisted_startup_metadata(stderr, reason):
    private = os.urandom(24).hex().encode()
    process = _FailedProcess(stderr + b"\n" + private)
    closed = []

    async def opener():
        return process

    async def closer(value):
        closed.append(value)
        await asyncio.sleep(0)

    carrier = PersistentCarrier("synthetic", "private-command", opener, closer)
    try:
        with pytest.raises(CarrierUnavailable) as caught:
            await carrier.acquire()
        details = carrier.diagnostics()["startup_failure"]
        assert details == {
            "stage": "hello-read",
            "error_type": "IncompleteReadError",
            "exit_code": 127,
            "reason": reason,
        }
        assert caught.value.reconnectable is True
        assert str(caught.value).startswith("carrier handshake failed: IncompleteReadError")
        assert reason in str(caught.value)
        exposed = str(caught.value) + repr(carrier.diagnostics())
        assert private.decode() not in exposed
        assert "private-command" not in exposed
        assert stderr.decode() not in exposed
        assert closed == [process]
        assert not carrier._tasks
        assert not carrier._stderr_tasks
    finally:
        await carrier.close()


@pytest.mark.asyncio
async def test_spawn_error_does_not_expose_exception_message():
    async def opener():
        raise FileNotFoundError("private-path-and-arguments")

    async def closer(_process):
        pytest.fail("No process was created")

    carrier = PersistentCarrier("synthetic", "command", opener, closer)
    try:
        with pytest.raises(CarrierUnavailable) as caught:
            await carrier.acquire()
        assert carrier.diagnostics()["startup_failure"] == {
            "stage": "open",
            "error_type": "FileNotFoundError",
            "exit_code": None,
            "reason": "no_startup_diagnostic",
        }
        assert "private-path-and-arguments" not in "".join(
            traceback.format_exception(caught.value)
        )
    finally:
        await carrier.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("eof", [True, False])
async def test_immediate_write_failure_drains_buffered_stderr_with_a_bound(eof):
    class BrokenInput(_Input):
        async def drain(self):
            raise BrokenPipeError("private-command")

    process = _FailedProcess(b"", returncode=127)
    process.stdin = BrokenInput()
    process.stderr = asyncio.StreamReader()
    process.stderr.feed_data(b"Permission denied (publickey)")
    if eof:
        process.stderr.feed_eof()

    async def opener():
        return process

    async def closer(_process):
        pass

    carrier = PersistentCarrier("synthetic", "command", opener, closer)
    try:
        with pytest.raises(CarrierUnavailable):
            await asyncio.wait_for(carrier.acquire(), timeout=2)
        assert carrier.diagnostics()["startup_failure"] == {
            "stage": "hello-write",
            "error_type": "BrokenPipeError",
            "exit_code": 127,
            "reason": "authentication_rejected",
        }
        assert not carrier._stderr_tasks
    finally:
        await carrier.close()


async def _native_carrier(argv, *, timeout=15, env=None):
    processes = []

    async def opener():
        spawn = (
            asyncio.create_subprocess_shell if isinstance(argv, str)
            else asyncio.create_subprocess_exec
        )
        arguments = (argv,) if isinstance(argv, str) else argv
        process = await spawn(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
        processes.append(process)
        return process

    async def closer(process):
        process.stdin.close()
        with contextlib.suppress(ConnectionError, OSError):
            await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except (TimeoutError, asyncio.TimeoutError):
            await terminate_ssh_process_tree(process, grace=0.1)

    return PersistentCarrier("native-fixture", "synthetic", opener, closer,
                             handshake_timeout=timeout), processes


def _native_console():
    if os.name == "nt":
        import ctypes
        if not ctypes.windll.kernel32.GetConsoleWindow():
            pytest.skip("Nested Windows shim fixture requires an existing console")


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0, 0.1])
async def test_native_stderr_flood_preserves_raw_hello_and_drains_after_startup(delay, tmp_path):
    _native_console()
    frame = encode_envelope(hello_envelope())
    ready = tmp_path / "stderr-drained"
    script = (
        "import sys,time;"
        "sys.stderr.buffer.write(b'x'*(2*1024*1024));sys.stderr.buffer.flush();"
        f"time.sleep({delay});"
        f"sys.stdout.buffer.write({frame!r});sys.stdout.buffer.flush();"
        "sys.stdin.buffer.read(4);"
        "sys.stderr.buffer.write(b'y'*(2*1024*1024));sys.stderr.buffer.flush();"
        f"open({str(ready)!r},'w').close();"
        "sys.stdin.buffer.read()"
    )
    carrier, processes = await _native_carrier([sys.executable, "-u", "-c", script])
    try:
        lease = await carrier.acquire()
        assert carrier.diagnostics()["state"] == "healthy"
        assert "startup_failure" not in carrier.diagnostics()
        assert processes[0].returncode is None
        async def wait_until_drained():
            while not ready.exists():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_until_drained(), timeout=10)
        await lease.release()
    finally:
        await carrier.close()
    assert processes[0].returncode is not None
    await asyncio.sleep(0)
    assert not carrier._tasks
    assert not carrier._stderr_tasks


@pytest.mark.asyncio
async def test_native_timeout_cleans_process_and_stderr_reader():
    _native_console()
    carrier, processes = await _native_carrier(
        [sys.executable, "-u", "-c", "import time;time.sleep(60)"], timeout=0.05
    )
    try:
        with pytest.raises(CarrierUnavailable):
            await carrier.acquire()
        details = carrier.diagnostics()["startup_failure"]
        assert details["stage"] == "hello-read"
        assert details["reason"] == "startup_timeout"
        assert details["exit_code"] is None
        assert processes[0].returncode is not None
        assert not carrier._tasks
        assert not carrier._stderr_tasks
    finally:
        await carrier.close()


@pytest.mark.asyncio
async def test_native_close_drains_stderr_before_reaping():
    _native_console()
    frame = encode_envelope(hello_envelope())
    script = (
        "import sys;"
        f"sys.stdout.buffer.write({frame!r});sys.stdout.buffer.flush();"
        "sys.stdin.buffer.read();"
        "sys.stderr.buffer.write(b'x'*(2*1024*1024));sys.stderr.buffer.flush()"
    )
    carrier, processes = await _native_carrier([sys.executable, "-u", "-c", script])
    try:
        lease = await carrier.acquire()
        await lease.release()
    finally:
        await carrier.close()
    assert processes[0].returncode == 0
    assert not carrier._stderr_tasks


@pytest.mark.asyncio
async def test_cancelled_startup_settles_process_and_stderr_reader():
    process = _FailedProcess(b"", returncode=None)
    process.stdout = asyncio.StreamReader()
    process.stderr = asyncio.StreamReader()
    entered = asyncio.Event()
    closed = []

    async def opener():
        entered.set()
        return process

    async def closer(value):
        closed.append(value)
        process.stderr.feed_eof()
        await asyncio.sleep(0)

    carrier = PersistentCarrier("synthetic", "command", opener, closer)
    task = asyncio.create_task(carrier.acquire())
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [process]
        assert not carrier._stderr_tasks
    finally:
        await carrier.close()


@pytest.mark.asyncio
async def test_finished_stderr_reader_is_not_retained_by_a_live_carrier():
    process = _FailedProcess(b"", returncode=None)
    process.stdout = asyncio.StreamReader()
    process.stdout.feed_data(encode_envelope(hello_envelope()))

    async def opener():
        return process

    async def closer(_value):
        pass

    carrier = PersistentCarrier("synthetic", "command", opener, closer)
    try:
        lease = await carrier.acquire()
        async def reader_released():
            while carrier._stderr_tasks:
                await asyncio.sleep(0)
        await asyncio.wait_for(reader_released(), timeout=2)
        assert not carrier._stderr_tasks
        assert carrier.diagnostics()["state"] == "healthy"
        await lease.release()
    finally:
        await carrier.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Actual Windows cmd/default-shell fixture")
@pytest.mark.parametrize("installed", [True, False])
async def test_native_windows_global_shim_keeps_hello_bytes(tmp_path, installed):
    _native_console()
    home = tmp_path / "synthetic home"
    binary = home / ".local" / "bin"
    binary.mkdir(parents=True)
    probe = home / "hello.py"
    frame = encode_envelope(hello_envelope())
    probe.write_text(
        "import sys\n"
        "import msvcrt,os\n"
        "msvcrt.setmode(sys.stdin.fileno(),os.O_BINARY)\n"
        "msvcrt.setmode(sys.stdout.fileno(),os.O_BINARY)\n"
        f"sys.stdout.buffer.write({frame!r});sys.stdout.buffer.flush()\n"
        "sys.stdin.buffer.read()\n",
        encoding="utf-8",
    )
    shim = binary / "agent-bridge.cmd"
    if installed:
        shim.write_text(
            f'@echo off\n"{sys.executable}" -u "{probe}"\nexit /b %ERRORLEVEL%\n',
            encoding="utf-8",
        )
    command = r'""%USERPROFILE%\.local\bin\agent-bridge.cmd" carrier --stdio"'
    carrier, processes = await _native_carrier(
        f'"{Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"}" /d /s /c {command}',
        env={**os.environ, "USERPROFILE": str(home)},
    )
    try:
        if installed:
            lease = await carrier.acquire()
            assert carrier.diagnostics()["state"] == "healthy"
            await lease.release()
        else:
            with pytest.raises(CarrierUnavailable):
                await carrier.acquire()
            assert carrier.diagnostics()["startup_failure"]["reason"] == "command_unavailable"
    finally:
        await carrier.close()
    assert processes[0].returncode is not None
    assert not carrier._stderr_tasks


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Actual POSIX shell fixture")
async def test_native_posix_shell_missing_launcher_is_classified(tmp_path):
    missing = tmp_path / "missing-launcher"
    carrier, processes = await _native_carrier(
        ["/bin/sh", "-c", f'exec "{missing}"']
    )
    try:
        with pytest.raises(CarrierUnavailable):
            await carrier.acquire()
        assert carrier.diagnostics()["startup_failure"]["reason"] == "command_unavailable"
        assert not carrier._stderr_tasks
    finally:
        await carrier.close()
    assert processes[0].returncode is not None
