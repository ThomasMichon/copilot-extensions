"""Isolated native Linux foreground custody; no product process or credential."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time

import psutil
import pytest

from agent_procutil import async_foreground as api
from agent_procutil import no_window_kwargs
from test_interactive import _environment, _gone, _read, _wait

pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux pidfd custody"),
]

_NATIVE = """
import json,os,sys,time
from pathlib import Path
command=sys.argv[3] if len(sys.argv)>3 else 'import time;time.sleep(60)'
if os.environ.get('FOREGROUND_FIXTURE_GUARDIAN_GROUP')=='1':
    os.setpgid(0,os.getsid(0))
child=os.posix_spawn(sys.executable,[sys.executable,'-c',command,*sys.argv[4:]],
                     os.environ,setpgroup=0)
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'descendant':child}))
while not Path(sys.argv[2]).exists():
    time.sleep(.01)
"""


async def _eventually(read, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        await asyncio.sleep(.01)
    raise AssertionError("Native fixture did not reach its bounded checkpoint")


@pytest.mark.parametrize("mode", ["natural", "terminate", "cancelled-waiter", "guardian-group"])
def test_native_scope_and_single_cleanup_retire_descendants_not_unrelated(
    tmp_path, monkeypatch, mode,
):
    async def exercise():
        history, gate = tmp_path / "native.json", tmp_path / "exit"
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        owner = None
        release, settling = threading.Event(), threading.Event()
        calls = []
        confirm = api.confirm_session

        def gated_confirm(process, **kwargs):
            calls.append(process.pid)
            settling.set()
            assert release.wait(5)
            return confirm(process, **kwargs)

        try:
            owner = await api.AsyncForegroundProcess.start([
                sys.executable, "-c", _NATIVE, str(history), str(gate),
            ], env={
                **os.environ,
                "FOREGROUND_FIXTURE_GUARDIAN_GROUP": "1" if mode == "guardian-group" else "0",
            })
            native = await _eventually(lambda: _read(history), lambda value: value is not None)
            assert owner.pid == native["pid"] and not owner.retirement_started
            assert api._pidfd_pid(owner._native_fd) == owner.pid
            assert os.getsid(native["descendant"]) == owner._guardian.pid
            assert os.getpgid(native["descendant"]) != os.getpgid(owner.pid)
            pins = [psutil.Process(native[key]) for key in ("pid", "descendant")]
            if mode == "natural":
                gate.touch()
                assert await asyncio.wait_for(owner.wait(), 5) == 0
                assert not owner.retirement_started
            elif mode in {"terminate", "guardian-group"}:
                if mode == "guardian-group":
                    assert os.getpgid(owner.pid) == owner._guardian.pid
                await asyncio.wait_for(owner.terminate(), 5)
                assert await owner.wait() == -signal.SIGKILL and owner.retirement_started
            else:
                monkeypatch.setattr(api, "confirm_session", gated_confirm)
                first = asyncio.create_task(owner.terminate())
                assert await asyncio.to_thread(settling.wait, 2)
                assert not owner.retirement_started
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                second = asyncio.create_task(owner.terminate())
                await asyncio.sleep(.025)
                assert not second.done() and calls == [owner._guardian.pid]
                release.set()
                await asyncio.wait_for(second, 5)
                assert owner.retirement_started and calls == [owner._guardian.pid]
            assert all(_gone(process) for process in pins)
            assert unrelated.poll() is None
        finally:
            release.set()
            try:
                if owner is not None:
                    await owner.terminate()
            finally:
                unrelated.kill()
                unrelated.wait(timeout=5)

    asyncio.run(exercise())


def test_guardian_queued_retirement_cannot_hide_a_live_sdk_writer_eof(tmp_path, monkeypatch):
    gate, release = tmp_path / "retiring", tmp_path / "release"
    hook = """
import os,time
from pathlib import Path
from agent_procutil._interactive_groups import SessionCustody
original=SessionCustody.retire
def gated(self,*args,**kwargs):
    Path(os.environ['FOREGROUND_FIXTURE_GATE']).touch()
    deadline=time.monotonic()+5
    while not Path(os.environ['FOREGROUND_FIXTURE_RELEASE']).exists():
        if time.monotonic()>=deadline:
            raise TimeoutError('fixture retirement gate was not released')
        time.sleep(.005)
    return original(self,*args,**kwargs)
SessionCustody.retire=gated
"""
    marker = "from agent_procutil._interactive_session import main;"
    monkeypatch.setattr(api, "_BOOTSTRAP", api._BOOTSTRAP.replace(
        marker, f"exec({hook!r});" + marker,
    ))
    writer_code = """
import socket,sys,time
from pathlib import Path
peer=socket.create_connection(('127.0.0.1',int(sys.argv[1])))
peer.sendall(b'idle\\n')
while not Path(sys.argv[2]).exists():
    time.sleep(.005)
peer.close()
time.sleep(60)
"""

    async def exercise():
        connected, disconnected = asyncio.Event(), asyncio.Event()

        async def receive(reader, writer):
            try:
                assert await reader.readline() == b"idle\n"
                connected.set()
                assert await reader.read() == b""
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(receive, "127.0.0.1", 0)
        owner = None
        history, disconnect = tmp_path / "native.json", tmp_path / "disconnect"
        try:
            owner = await api.AsyncForegroundProcess.start(
                [
                    sys.executable, "-c", _NATIVE, str(history), str(tmp_path / "exit"),
                    writer_code, str(server.sockets[0].getsockname()[1]), str(disconnect),
                ],
                env={
                    **os.environ, "FOREGROUND_FIXTURE_GATE": str(gate),
                    "FOREGROUND_FIXTURE_RELEASE": str(release),
                },
            )
            native = await _eventually(lambda: _read(history), lambda value: value is not None)
            pins = [psutil.Process(native[key]) for key in ("pid", "descendant")]
            await asyncio.wait_for(connected.wait(), 2)
            retiring = asyncio.create_task(owner.terminate())
            await _eventually(gate.exists, bool)
            assert not owner.retirement_started and all(not _gone(pin) for pin in pins)
            disconnect.touch()
            await asyncio.wait_for(disconnected.wait(), 2)
            assert not owner.retirement_started and all(not _gone(pin) for pin in pins)
            release.touch()
            await asyncio.wait_for(retiring, 5)
            assert owner.retirement_started and all(_gone(pin) for pin in pins)
        finally:
            release.touch()
            if owner is not None:
                await owner.terminate()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_native_admission_refusal_does_not_execute_the_command(tmp_path, monkeypatch):
    marker = tmp_path / "not-executed"
    guardians = []
    launch = api.subprocess.Popen

    def observe(*args, **kwargs):
        process = launch(*args, **kwargs)
        guardians.append(process)
        return process

    def refuse():
        raise ValueError("fixture source authority expired")

    monkeypatch.setattr(api.subprocess, "Popen", observe)
    with pytest.raises(ValueError, match="source authority expired"):
        asyncio.run(api.AsyncForegroundProcess.start(
            [sys.executable, "-c", "from pathlib import Path;import sys;Path(sys.argv[1]).touch()",
             str(marker)],
            before_exec=refuse,
        ))
    assert not marker.exists() and len(guardians) == 1
    assert guardians[0].returncode is not None


@pytest.mark.parametrize("guardian_group", [False, True])
def test_caller_sigkill_independently_retires_native_scope(tmp_path, guardian_group):
    history, gate, receipt = tmp_path / "native.json", tmp_path / "exit", tmp_path / "owner.json"
    caller = """
import asyncio,json,sys
from pathlib import Path
from agent_procutil.async_foreground import AsyncForegroundProcess
async def run():
    owner=await AsyncForegroundProcess.start(sys.argv[2:])
    Path(sys.argv[1]).write_text(json.dumps({'pid':owner.pid,'guardian':owner._guardian.pid}))
    await asyncio.Future()
asyncio.run(run())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", caller, str(receipt),
         sys.executable, "-c", _NATIVE, str(history), str(gate)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={
            **_environment(), "FOREGROUND_FIXTURE_GUARDIAN_GROUP": "1" if guardian_group else "0",
        }, **no_window_kwargs(),
    )
    pins = []
    try:
        owner = _wait(lambda: _read(receipt), "caller did not publish its owned native identity")
        native = _wait(lambda: _read(history), "native fixture did not start")
        assert owner["pid"] == native["pid"]
        if guardian_group:
            assert os.getpgid(owner["pid"]) == owner["guardian"]
        pins = [psutil.Process(pid) for pid in (*native.values(), owner["guardian"])]
        process.kill()
        process.wait(timeout=5)
        _wait(lambda: all(_gone(pin) for pin in pins), "caller death left owned native members")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for pin in pins:
            if not _gone(pin):
                pin.kill()


@pytest.mark.parametrize("failure", ["confirmation", "drain"])
def test_failed_cleanup_restores_real_terminal_and_retains_retry_custody(tmp_path, failure):
    result = tmp_path / "restored.json"
    caller = """
import asyncio,fcntl,json,os,sys,termios,time
from pathlib import Path
from agent_procutil import async_foreground as api
from agent_procutil.owned_process import OwnedCleanupError
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
before=termios.tcgetattr(0)
original=api.confirm_session if sys.argv[2]=='confirmation' else api._drain
def fail(*args,**kwargs):
    raise OSError('fixture cleanup failure')
async def run():
    owner=await api.AsyncForegroundProcess.start([sys.executable,'-c','pass'])
    if sys.argv[2]=='confirmation':
        api.confirm_session=fail
    else:
        api._drain=fail
    try:
        await owner.wait()
    except OwnedCleanupError as error:
        restored=termios.tcgetattr(0)==before
        retained=error.cleanup_resources==(owner,) and owner._terminal is not None
        if sys.argv[2]=='confirmation':
            api.confirm_session=original
        else:
            api._drain=original
        await asyncio.to_thread(error.retry_cleanup,deadline=time.monotonic()+2)
        Path(sys.argv[1]).write_text(json.dumps({
            'restored':restored,'retained':retained,
            'settled':owner._terminal is None and not owner._descriptors}))
    else:
        raise AssertionError('fixture cleanup failure was not observed')
asyncio.run(run())
"""
    master, slave = os.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", caller, str(result), failure],
        stdin=slave, stdout=slave, stderr=slave, env=_environment(),
    )
    os.close(slave)
    try:
        process.wait(timeout=10)
        assert process.returncode == 0
        assert _read(result) == {"restored": True, "retained": True, "settled": True}
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        os.close(master)


@pytest.mark.parametrize("high_descriptors", [False, True])
def test_pty_input_resize_redirected_output_and_terminal_restore(tmp_path, high_descriptors):
    import fcntl
    import select
    import struct
    import termios

    result, native_ready = tmp_path / "result.json", tmp_path / "native.json"
    native = """
import fcntl,json,os,struct,sys,termios
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'tty':os.ttyname(0)}))
text=input()
rows,cols,*_=struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'\\0'*8))
print(json.dumps({'text':text,'rows':rows,'cols':cols}),flush=True)
"""
    caller = """
import asyncio,fcntl,json,os,sys,termios
from pathlib import Path
from agent_procutil.async_foreground import AsyncForegroundProcess
from agent_procutil import _interactive_terminal as terminal
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
before=termios.tcgetattr(0)
if os.environ.get('FOREGROUND_FIXTURE_HIGH_FDS')=='1':
    def high(fd):
        moved=fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,2048)
        os.close(fd)
        return moved
    terminal._above_stdio=high
async def run():
    owner=await AsyncForegroundProcess.start(sys.argv[2:])
    if os.environ.get('FOREGROUND_FIXTURE_HIGH_FDS')=='1':
        assert owner._events>=2048 and owner._terminal.fd>=2048
    code=await owner.wait()
    Path(sys.argv[1]).write_text(json.dumps({
        'code':code,'attributes':termios.tcgetattr(0)==before,
        'foreground':os.tcgetpgrp(0)==os.getpgrp()}))
asyncio.run(run())
"""
    master, slave = os.openpty()
    original_tty = os.ttyname(slave)
    process = subprocess.Popen(
        [sys.executable, "-c", caller, str(result),
         sys.executable, "-c", native, str(native_ready)],
        stdin=slave, stdout=subprocess.PIPE, stderr=slave, env={
            **_environment(), "FOREGROUND_FIXTURE_HIGH_FDS": "1" if high_descriptors else "0",
        },
    )
    os.close(slave)
    try:
        ready = _wait(lambda: _read(native_ready), "native PTY did not become ready")
        assert ready["tty"] != original_tty
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 31, 97, 0, 0))
        time.sleep(.2)
        os.write(master, b"literal foreground input\n")
        assert select.select([process.stdout], [], [], 5)[0]
        message = json.loads(process.stdout.readline())
        assert message == {"text": "literal foreground input", "rows": 31, "cols": 97}
        process.wait(timeout=5)
        assert _read(result) == {"code": 0, "attributes": True, "foreground": True}
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        os.close(master)


def test_overlapping_native_terminal_start_refuses_and_restores_original_modes(tmp_path):
    result = tmp_path / "overlap.json"
    caller = """
import asyncio,fcntl,json,os,sys,termios
from pathlib import Path
from agent_procutil.async_foreground import AsyncForegroundProcess
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
before=termios.tcgetattr(0)
async def run():
    first=await AsyncForegroundProcess.start([sys.executable,'-c','import time;time.sleep(60)'])
    second=None
    refused=False
    try:
        try:
            second=await AsyncForegroundProcess.start(
                [sys.executable,'-c','import time;time.sleep(60)'])
        except RuntimeError as error:
            refused='already has an interactive owner' in str(error)
    finally:
        if second is not None:
            await second.terminate()
        await first.terminate()
    Path(sys.argv[1]).write_text(json.dumps({
        'refused':refused,'restored':termios.tcgetattr(0)==before}))
asyncio.run(run())
"""
    master, slave = os.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", caller, str(result)],
        stdin=slave, stdout=slave, stderr=slave, env=_environment(),
    )
    os.close(slave)
    try:
        process.wait(timeout=10)
        assert process.returncode == 0
        assert _read(result) == {"refused": True, "restored": True}
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        os.close(master)


def test_queued_native_stop_frame_does_not_turn_retirement_into_cleanup_failure(tmp_path):
    async def exercise():
        owner = await api.AsyncForegroundProcess.start([
            sys.executable, "-c",
            "import os,signal,time;os.kill(os.getpid(),signal.SIGSTOP);time.sleep(60)",
        ])
        stopped, release = asyncio.Event(), asyncio.Event()
        original = owner._frame

        async def gated():
            frame = await original()
            if frame == b"stopped":
                stopped.set()
                await release.wait()
            return frame

        owner._frame = gated
        try:
            await asyncio.wait_for(stopped.wait(), 2)
            retiring = asyncio.create_task(owner.terminate())
            await asyncio.sleep(0)
            assert owner._requested
            release.set()
            await asyncio.wait_for(retiring, 5)
            assert await owner.wait() == -signal.SIGKILL and owner.retirement_started
        finally:
            release.set()
            await owner.terminate()

    asyncio.run(exercise())
