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
    CarrierProtocolError,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
    PersistentCarrier,
    decode_envelope,
    encode_envelope,
    hello_envelope,
)
from ssh_manager.process import terminate_ssh_process_tree
from ssh_manager.carrier_lifecycle import _drain_startup_stderr


class _Input:
    def write(self, _data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
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
async def test_native_descendant_held_stderr_read_transport_is_closed(tmp_path):
    _native_console()
    ready = tmp_path / "descendant-ready"
    release = tmp_path / "release-descendant"
    finished = tmp_path / "descendant-finished"
    child = (
        "import pathlib,time\n"
        f"ready=pathlib.Path({str(ready)!r});release=pathlib.Path({str(release)!r})\n"
        f"finished=pathlib.Path({str(finished)!r})\n"
        "ready.write_text('ready')\n"
        "deadline=time.monotonic()+10\n"
        "while not release.exists() and time.monotonic()<deadline:time.sleep(.01)\n"
        "finished.write_text('finished')\n"
    )
    parent = (
        "import os,subprocess,sys;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=sys.stderr);"
        "os._exit(0)"
    )
    carrier, processes = await _native_carrier([sys.executable, "-u", "-c", parent])
    process = await carrier._opener()
    stderr_task = asyncio.create_task(
        _drain_startup_stderr(process.stderr, {"reason": None})
    )
    carrier._stderr_tasks[id(process)] = stderr_task

    async def wait_until(predicate):
        while not predicate():
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(
            wait_until(lambda: ready.exists() and process.returncode is not None),
            timeout=10,
        )
        transport = process.stderr._transport
        assert process.returncode == 0
        assert not transport.is_closing()
        assert not finished.exists()
        await asyncio.wait_for(carrier._closer(process), timeout=2)
        assert stderr_task.done()
        assert not carrier._stderr_tasks
        assert transport.is_closing()
        assert not finished.exists()
    finally:
        release.write_text("release")
        await carrier._closer(process)
        await asyncio.wait_for(wait_until(finished.exists), timeout=10)
        await carrier.close()
    assert processes[0].returncode == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement_mode", [
        "ensure", "request", "failed", "cancel", "submitted-cancel", "recover",
        "heartbeat-failed", "heartbeat-cleanup-cancel",
    ],
)
@pytest.mark.parametrize("old_request", [False, True])
async def test_replacement_retires_exited_carrier_with_descendant_held_pipes(
    tmp_path, replacement_mode, old_request,
):
    _native_console()
    ready = tmp_path / "descendant-ready"
    release = tmp_path / "release-descendant"
    finished = tmp_path / "descendant-finished"
    child = (
        "import pathlib,time\n"
        f"ready=pathlib.Path({str(ready)!r});release=pathlib.Path({str(release)!r})\n"
        f"finished=pathlib.Path({str(finished)!r})\n"
        "ready.write_text('ready')\n"
        "deadline=time.monotonic()+15\n"
        "while not release.exists() and time.monotonic()<deadline:time.sleep(.01)\n"
        "finished.write_text('finished')\n"
    )
    frame = encode_envelope(hello_envelope())
    has_subscriptions = replacement_mode in (
        "recover", "heartbeat-failed", "heartbeat-cleanup-cancel",
    )
    receive_old_request = (
        "n=struct.unpack('!I',sys.stdin.buffer.read(4))[0];sys.stdin.buffer.read(n);"
    ) * (
        1 + int(old_request) + (2 if has_subscriptions else 0)
        if old_request or has_subscriptions else 0
    )
    parent = (
        "import os,struct,subprocess,sys;"
        f"sys.stdout.buffer.write({frame!r});sys.stdout.buffer.flush();"
        f"{receive_old_request}"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr);"
        "os._exit(0)"
    )
    carrier, processes = await _native_carrier([sys.executable, "-u", "-c", parent])
    old_tasks = set()
    pending_request = fresh_request = None
    heartbeat = None
    original_closer = carrier._process_closer
    cleanup_entered = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def wait_until(predicate):
        while not predicate():
            await asyncio.sleep(0.01)

    try:
        lease = await carrier.acquire()
        process = processes[0]
        if has_subscriptions:
            replayable = await carrier.subscribe(
                {"operation": "synthetic-events"}, subscription_id="replayable",
            )
            one_shot = await carrier.subscribe(
                {"operation": "synthetic-events"}, subscription_id="one-shot",
                replayable=False,
            )
        if old_request:
            pending_request = asyncio.create_task(carrier.request(
                {"operation": "old-synthetic"}, timeout=None, request_id="old-request",
            ))
        await asyncio.wait_for(
            wait_until(lambda: ready.exists() and process.returncode is not None),
            timeout=10,
        )
        assert len(carrier._request_epochs) == int(old_request)
        assert set(carrier._request_epochs.values()) <= {carrier._transport_epoch}
        if old_request:
            with pytest.raises(CarrierProtocolError, match="duplicate carrier request_id"):
                await carrier.request({}, request_id="old-request")
            assert len(processes) == 1
        stderr_task = carrier._stderr_tasks[id(process)]
        transport = process.stderr._transport
        old_tasks = set(carrier._tasks)
        replacement = _FailedProcess(b"", returncode=None)
        submitted = []
        restored = []
        if replacement_mode not in ("failed", "cancel"):
            replacement.stdout = asyncio.StreamReader()
            replacement.stdout.feed_data(frame)
        elif replacement_mode == "cancel":
            replacement.stdout = asyncio.StreamReader()
        opening = asyncio.Event()

        class ReplyInput(_Input):
            def write(self, data):
                envelope = decode_envelope(data[4:])
                if envelope.subscription_id:
                    restored.append(envelope.subscription_id)
                    replacement.stdout.feed_data(encode_envelope(Envelope(
                        EnvelopeType.EVENT,
                        subscription_id=envelope.subscription_id,
                        payload={"restored": True},
                    )))
                elif envelope.type is EnvelopeType.REQUEST:
                    assert carrier._request_epochs[envelope.request_id] == carrier._transport_epoch
                    submitted.append(envelope.request_id)
                    if replacement_mode != "submitted-cancel":
                        replacement.stdout.feed_data(encode_envelope(Envelope(
                            EnvelopeType.RESPONSE,
                            request_id=envelope.request_id,
                            payload={"executed": 1},
                        )))

        replacement.stdin = ReplyInput()
        failed_replacement = _FailedProcess(b"", returncode=127)
        opens = 0

        async def reopen():
            nonlocal opens, replacement
            assert stderr_task.done()
            assert transport.is_closing()
            opening.set()
            opens += 1
            if replacement_mode in ("recover", "heartbeat-failed") and opens == 1:
                return failed_replacement
            if replacement_mode == "cancel" and opens > 1:
                replacement = _FailedProcess(b"", returncode=None)
                replacement.stdout = asyncio.StreamReader()
                replacement.stdout.feed_data(frame)
                replacement.stdin = ReplyInput()
            return replacement

        carrier._opener = reopen
        if replacement_mode == "request":
            response = await carrier.request({"operation": "synthetic"}, timeout=2)
            assert response.payload == {"executed": 1}
            assert submitted == [response.request_id]
            assert carrier._process is replacement
            for _ in range(8):
                assert not carrier._request_epochs
                await carrier.request({"operation": "synthetic"}, timeout=2)
            assert len(submitted) == len(set(submitted)) == 9
            assert "old-request" not in submitted
        elif replacement_mode == "ensure":
            await asyncio.wait_for(carrier.ensure_started(), timeout=2)
            assert carrier._process is replacement
        elif replacement_mode == "failed":
            with pytest.raises(CarrierUnavailable):
                await asyncio.wait_for(carrier.ensure_started(), timeout=2)
            assert carrier._process is None
        elif replacement_mode == "cancel":
            fresh_request = asyncio.create_task(carrier.request(
                {"operation": "cancelled-synthetic"}, timeout=None,
            ))
            await asyncio.wait_for(opening.wait(), timeout=2)
            fresh_request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await fresh_request
            assert not submitted
            await asyncio.wait_for(
                wait_until(lambda: carrier.diagnostics()["state"] == "healthy"),
                timeout=2,
            )
        elif replacement_mode == "submitted-cancel":
            fresh_request = asyncio.create_task(carrier.request(
                {"operation": "cancelled-synthetic"}, timeout=None,
            ))
            await asyncio.wait_for(wait_until(lambda: len(submitted) == 1), timeout=2)
            fresh_request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await fresh_request
            assert len(submitted) == 1
        else:
            if replacement_mode == "recover":
                with pytest.raises(CarrierUnavailable):
                    await asyncio.wait_for(carrier.ensure_started(), timeout=2)
            else:
                if replacement_mode == "heartbeat-cleanup-cancel":
                    async def paused_closer(value):
                        if value is process:
                            cleanup_entered.set()
                            await cleanup_release.wait()
                        await original_closer(value)

                    carrier._process_closer = paused_closer
                carrier._heartbeat_interval = 0.01
                carrier._start_task(
                    carrier._heartbeat_loop(carrier._transport_epoch),
                    "fixture-heartbeat",
                )
                heartbeat = next(
                    task for task in carrier._tasks
                    if task.get_name() == "fixture-heartbeat"
                )
                old_tasks.add(heartbeat)
                if replacement_mode == "heartbeat-cleanup-cancel":
                    await asyncio.wait_for(cleanup_entered.wait(), timeout=2)
                    assert carrier._reconnect_task is not None
                    assert not carrier._reconnect_task.done()
                    heartbeat.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await heartbeat
                    cleanup_release.set()
                    carrier._process_closer = original_closer
            await asyncio.wait_for(
                wait_until(lambda: carrier._process is replacement
                           and carrier.diagnostics()["state"] == "healthy"),
                timeout=5,
            )
            event = await asyncio.wait_for(replayable.get(), timeout=2)
            assert event.payload == {"restored": True}
            assert restored == ["replayable"]
            with pytest.raises(CarrierUnavailable, match="non-replayable subscription"):
                await one_shot.get()
            assert not submitted
            assert opens == (1 if replacement_mode == "heartbeat-cleanup-cancel" else 2)
            assert any(task.get_name() == "ssh-carrier-stale" for task in carrier._tasks)
        if pending_request is not None:
            with pytest.raises(CarrierUnavailable, match="carrier transport was lost"):
                await asyncio.wait_for(asyncio.shield(pending_request), timeout=2)
        assert not carrier._pending
        assert not carrier._request_epochs
        await carrier.close()
        assert stderr_task.done()
        assert transport.is_closing()
        assert not carrier._stderr_tasks
        await asyncio.wait_for(asyncio.gather(*old_tasks, return_exceptions=True), timeout=2)
        assert not finished.exists()
        await lease.release()
    finally:
        release.write_text("release")
        cleanup_release.set()
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        carrier._process_closer = original_closer
        await carrier.close()
        for request in (pending_request, fresh_request):
            if request is not None:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(*old_tasks, return_exceptions=True), timeout=2)
        for process in processes:
            await carrier._closer(process)
            await asyncio.wait_for(process.stdout.read(), timeout=10)
        await asyncio.wait_for(wait_until(finished.exists), timeout=10)


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
