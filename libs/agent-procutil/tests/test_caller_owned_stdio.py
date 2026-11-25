"""Inherited stdio stays byte-exact and inside its finite native owner."""

from __future__ import annotations

from contextlib import AbstractContextManager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Callable, Protocol, TypedDict, cast

import pytest

from agent_procutil import _caller_owned as protocol
from agent_procutil._native_types import DescriptorStream
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

if TYPE_CHECKING:
    from typing_extensions import Unpack


native_execution = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="caller-owned native execution supports Windows, Linux and macOS",
)


class _InputOptions(TypedDict, total=False):
    input_text: str | None
    input_bytes: bytes | None
    binary: bool


class _RunOptions(_InputOptions, total=False):
    capture: bool
    stdin_stream: DescriptorStream
    stdout_stream: DescriptorStream
    on_settled: Callable[[], None]


class _ConsoleSession(Protocol):
    output: bytes

    def send(self, data: bytes) -> None: ...
    def wait(self) -> int: ...


class _ConsoleFixture(Protocol):
    def Console(
        self, argv: list[str], *, env: dict[str, str], cwd: Path,
    ) -> AbstractContextManager[_ConsoleSession]: ...


def _run(
    code: str, *, timeout: float = 5, stdout: int = 4096, stderr: int = 4096,
    **kwargs: Unpack[_RunOptions],
) -> subprocess.CompletedProcess[str | bytes | None]:
    return run_caller_owned(
        [sys.executable, "-I", "-c", code], timeout=timeout,
        max_stdout_bytes=stdout, max_stderr_bytes=stderr, **kwargs,
    )


@native_execution
def test_inherited_stdin_preserves_bytes_and_does_not_close_caller_stream(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.write_bytes(b"\xff\x00\r\nborrowed")
    with source.open("rb", buffering=0) as stream:
        result = _run("import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())",
                      stdin_stream=stream, binary=True)
        assert result.returncode == 0 and result.stdout == source.read_bytes()
        assert not stream.closed
        os.fstat(stream.fileno())


@native_execution
def test_blocked_inherited_stdin_remains_inside_finite_owner() -> None:
    reader, writer = os.pipe()
    with os.fdopen(reader, "rb", buffering=0) as stream:
        try:
            started = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired):
                _run("import sys;sys.stdin.buffer.read()", stdin_stream=stream, timeout=.5)
            assert time.monotonic() - started < 5
            os.fstat(stream.fileno())
        finally:
            os.close(writer)
    assert _run("print('settled')").stdout == "settled\n"


@native_execution
def test_inherited_output_is_delivered_before_completion_without_closing_sink(tmp_path: Path) -> None:
    with (tmp_path / "output").open("w+b", buffering=0) as stream:
        result = _run("import sys;sys.stdout.buffer.write(b'\\xff\\r\\n')",
                      capture=False, stdout_stream=stream)
        assert result.returncode == 0 and result.stdout is None
        stream.seek(0)
        assert stream.read() == b"\xff\r\n"


@pytest.mark.real_installer_processes
@pytest.mark.skipif(os.name != "nt", reason="requires actual Windows console handles")
@pytest.mark.parametrize("send_input", [True, False])
def test_inherited_console_streams_work_through_headless_owned_child(
    tmp_path: Path, send_input: bool,
) -> None:
    import agent_procutil

    fixture_path = (
        Path(__file__).resolve().parents[3] / "worktree-manager" / "tests" / "native"
        / "_terminal_console_fixture.py"
    )
    spec = importlib.util.spec_from_file_location("borrowed_console_fixture", fixture_path)
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    receipt = tmp_path / "console.json"
    script = """
import ctypes,json,os,pathlib,subprocess,sys
from ctypes import wintypes
sys.path.insert(0,sys.argv[2])
from agent_procutil.owned_process import run_caller_owned
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetConsoleMode.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
kernel.GetConsoleMode.restype=wintypes.BOOL
import msvcrt
state={}
with open('CONIN$','rb',buffering=0) as source, open('CONOUT$','wb',buffering=0) as sink:
    for name,stream in [('input_console',source),('output_console',sink)]:
        mode=wintypes.DWORD()
        state[name]=bool(kernel.GetConsoleMode(msvcrt.get_osfhandle(stream.fileno()),ctypes.byref(mode)))
    try:
        result=run_caller_owned(
            [sys.executable,'-I','-c',
             "import sys;v=sys.stdin.buffer.readline();sys.stdout.buffer.write(b'child:'+v);sys.stdout.flush()"],
            timeout=3,max_stdout_bytes=128,max_stderr_bytes=4096,capture=False,
            stdin_stream=source,stdout_stream=sink)
        state['returncode']=result.returncode
    except subprocess.TimeoutExpired:
        state['timed_out']=True
    except Exception as error:
        state['error']=type(error).__name__+': '+str(error)
    os.fstat(source.fileno())
    os.fstat(sink.fileno())
    state['caller_streams_open']=not source.closed and not sink.closed
pathlib.Path(sys.argv[1]).write_text(json.dumps(state),encoding='utf-8')
"""
    with cast(_ConsoleFixture, fixture).Console(
        [sys.executable, "-I", "-c", script, str(receipt),
         str(Path(agent_procutil.__file__).resolve().parents[1])],
        env=dict(os.environ), cwd=tmp_path,
    ) as console:
        if send_input:
            console.send(b"probe\r")
        assert console.wait() == 0
        state = json.loads(receipt.read_text(encoding="utf-8"))
        assert state == {
            "input_console": True, "output_console": True,
            **({"returncode": 0} if send_input else {"timed_out": True}),
            "caller_streams_open": True,
        }, state
    if send_input:
        assert b"child:probe" in console.output


@native_execution
def test_blocked_inherited_output_remains_inside_finite_owner() -> None:
    reader, writer = os.pipe()
    with os.fdopen(writer, "wb", buffering=0) as stream:
        try:
            started = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired):
                _run("import sys;sys.stdout.buffer.write(b'x'*1048576);sys.stdout.flush()",
                      capture=False, stdout_stream=stream, timeout=.5)
            assert time.monotonic() - started < 5
            os.fstat(stream.fileno())
        finally:
            os.close(reader)


@pytest.mark.parametrize("options", [{"input_text": "text"}, {"input_bytes": b"bytes", "binary": True}])
def test_inherited_stdin_refuses_ambiguous_input_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: _InputOptions,
) -> None:
    source = tmp_path / "input"
    source.write_bytes(b"borrowed")
    monkeypatch.setattr(protocol, "_run_owned", lambda *a, **k: pytest.fail("native launch attempted"))
    with source.open("rb", buffering=0) as stream, pytest.raises(ValueError):
        _run("pass", stdin_stream=stream, **options)


@pytest.mark.parametrize("direction", ["stdin", "stdout"])
def test_buffered_stdio_refuses_unread_or_pending_bytes_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, direction: str,
) -> None:
    source = tmp_path / "buffered"
    source.write_bytes(b"first\nremaining\n")
    settled: list[bool] = []
    monkeypatch.setattr(protocol, "_run_owned", lambda *a, **k: pytest.fail("native launch attempted"))
    with source.open("r+b") as stream:
        if direction == "stdin":
            assert stream.readline() == b"first\n"
            assert os.lseek(stream.fileno(), 0, os.SEEK_CUR) > stream.tell()
        else:
            stream.seek(0, os.SEEK_END)
            stream.write(b"pending")
        with pytest.raises(ValueError, match="unbuffered"):
            if direction == "stdin":
                _run("pass", stdin_stream=stream, on_settled=lambda: settled.append(True))
            else:
                _run("pass", stdout_stream=stream, capture=False,
                     on_settled=lambda: settled.append(True))
        assert not stream.closed
    assert settled == [True]


def test_inherited_stdio_preparation_failure_retains_failed_close_and_closes_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicates = iter((20, 21))
    closed: list[int] = []
    settled: list[bool] = []

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        if descriptor == 20:
            raise OSError("close disposition unknown")

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        F_DUPFD_CLOEXEC=1, fcntl=lambda *a: next(duplicates),
    ))
    monkeypatch.setattr(protocol, "os", SimpleNamespace(
        name="posix", fstat=lambda fd: None, close=close,
    ))
    monkeypatch.setattr(protocol, "_run_owned", lambda *a, **k: pytest.fail("native launch attempted"))
    with pytest.raises(OwnedCleanupError) as caught:
        _run(
            "pass", stdin_stream=SimpleNamespace(fileno=lambda: 0),
            stdout_stream=SimpleNamespace(fileno=lambda: 1), capture=True,
            on_settled=lambda: settled.append(True),
        )
    assert sorted(closed) == [20, 21]
    assert caught.value.cleanup_resources == (("descriptor", 20),)
    assert settled == []
