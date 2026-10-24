"""Parent-side launch forwarding tests; these do not establish native admission."""

import io
import hashlib
import json
import os
import subprocess
import time
import sys
from pathlib import Path

import pytest

from tools.coverage_guided_selection.scoped_recovery_client import ScopedRecoveryClient
from tools.coverage_guided_selection.child_audit import argv_key


@pytest.mark.parametrize("context", ["owned", "unrecognized"])
@pytest.mark.parametrize("preloader", [None, "/existing/preloader.so"])
@pytest.mark.parametrize("argument_kind", [
    "list", "tuple", "path", "text", "bytes", "nonutf8-bytes", "surrogate-text",
    "iterator", "generator",
])
def test_launch_options_and_actual_exits_preserved(monkeypatch, context, preloader, argument_kind):
    calls = []

    class NativePopen:
        def __init__(self, args, bufsize=-1, *, env=None, cwd=None):
            calls.append((args, bufsize, env, cwd))
            self.pid = 71

        def wait(self, timeout=None):
            assert timeout == 5
            return 17

        def poll(self):
            return 17

    monkeypatch.setattr(subprocess, "Popen", NativePopen)
    writer = io.StringIO()
    client = ScopedRecoveryClient(None, writer, "private-token", ["owned"])
    client.plan = {"controls": {"owned": "/control"}, "native_library": "/observer.so",
                   "entry_socket": "/entry.sock"}
    environment = {"ORIGINAL_CONFIG": "unchanged"}
    if preloader:
        environment["LD_PRELOAD"] = preloader
    original = dict(environment)
    argv = ["/actual/python", "-I", "-c", "raise SystemExit(17)"]
    expected_arguments = list(argv)
    if argument_kind == "tuple":
        argv = tuple(argv)
    elif argument_kind == "path":
        argv = Path(argv[0])
    elif argument_kind == "text":
        argv = argv[0]
    elif argument_kind == "bytes":
        argv = argv[0].encode()
    elif argument_kind == "nonutf8-bytes":
        argv.append(b"\xff")
    elif argument_kind == "surrogate-text":
        argv.append(b"\xff".decode("utf-8", "surrogateescape"))
    elif argument_kind == "iterator":
        argv = iter(argv)
    elif argument_kind == "generator":
        argv = (value for value in argv)
    client.install_launch_recorder(lambda: context)
    try:
        child = subprocess.Popen(argv, 0, env=environment, cwd="/original")
        assert child.wait(timeout=5) == child.poll() == 17
    finally:
        client.restore_launch_recorder()
    assert subprocess.Popen is NativePopen
    assert environment == original
    actual_args, bufsize, forwarded, cwd = calls[0]
    if argument_kind in {"iterator", "generator"}:
        assert actual_args == expected_arguments and type(actual_args) is list
        sentinel = object()
        assert next(argv, sentinel) is sentinel
    else:
        assert actual_args is argv
    assert bufsize == 0 and cwd == "/original"
    if context == "owned":
        assert forwarded["LD_PRELOAD"] == (preloader or "/observer.so")
        assert forwarded["CGS_NATIVE_CONTROL"] == "/control"
    else:
        assert forwarded is environment
    events = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert [row["op"] for row in events] == ["LAUNCHED", "EXIT_OBSERVED"]
    assert events[0]["case"] == context and events[0]["pid"] == 71
    tail = (b"-I\0-c\0raise SystemExit(17)" if
            argument_kind in {"list", "tuple", "nonutf8-bytes", "surrogate-text", "iterator", "generator"} else b"")
    if argument_kind in {"nonutf8-bytes", "surrogate-text"}:
        tail += b"\0" + os.fsencode(argv[-1])
    assert events[0]["argv_key"] == hashlib.sha256(tail).hexdigest()
    assert events[0]["argv_key"] == argv_key(
        [actual_args] if isinstance(actual_args, (str, bytes, os.PathLike)) else actual_args,
    )
    assert events[1]["returncode"] == 17


@pytest.mark.skipif(
    os.name == "nt" or os.environ.get("CGS_RUN_STANDALONE_INTEGRATION") != "1",
    reason="deliberate real POSIX native-argument boundary",
)
@pytest.mark.parametrize("argument", [b"\xff", b"\xff".decode("utf-8", "surrogateescape")])
@pytest.mark.parametrize("argument_kind", ["list", "iterator", "generator"])
def test_posix_nonutf8_arguments_reach_actual_child_unchanged(argument, argument_kind):
    writer = io.StringIO()
    client = ScopedRecoveryClient(None, writer, "token", [])
    code = "import os,sys;print(os.fsencode(sys.argv[1]).hex())"
    command = [sys.executable, "-I", "-c", code, argument]
    expected_command = list(command)
    if argument_kind == "iterator":
        command = iter(command)
    elif argument_kind == "generator":
        command = (value for value in command)
    client.install_launch_recorder(lambda: "unrecognized")
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0 and stdout.strip() == b"ff", stderr
    finally:
        client.restore_launch_recorder()
    events = [json.loads(line) for line in writer.getvalue().splitlines()]
    expected = hashlib.sha256(b"-I\0-c\0" + code.encode() + b"\0\xff").hexdigest()
    assert events[0]["argv_key"] == argv_key(expected_command) == expected


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_STANDALONE_INTEGRATION") != "1",
    reason="deliberate real iterable-argument process boundary",
)
@pytest.mark.parametrize("argument_kind", ["iterator", "generator"])
def test_iterable_arguments_reach_actual_native_child(argument_kind):
    writer = io.StringIO()
    client = ScopedRecoveryClient(None, writer, "token", [])
    arguments = [sys.executable, "-I", "-c", "print('actual iterable reached')"]
    command = iter(arguments) if argument_kind == "iterator" else (value for value in arguments)
    client.install_launch_recorder(lambda: "unrecognized")
    try:
        child = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0 and stdout.strip() == b"actual iterable reached", stderr
    finally:
        client.restore_launch_recorder()
    events = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert events[0]["argv_key"] == argv_key(arguments)


def test_empty_or_refused_completion_cannot_claim_producers():
    client = ScopedRecoveryClient(None, None, None, [])
    with pytest.raises(RuntimeError, match="missing scoped Recovery completion"):
        client.evidence()
    client.completion = {"all_admitted": False}
    assert client.evidence()["admitted"] is False
    assert client.evidence()["producer_count"] == 0


@pytest.mark.skipif(os.name == "nt", reason="owned observer uses POSIX control pipes")
def test_reply_prefix_without_newline_cannot_block_beyond_deadline():
    reader_fd, writer_fd = os.pipe()
    with os.fdopen(reader_fd, "rb", buffering=0) as reader, os.fdopen(writer_fd, "wb", buffering=0) as writer:
        client = ScopedRecoveryClient(reader, None, "token", [])
        writer.write(b'{"token":"token"')
        started = time.monotonic()
        client.deadline = started + 0.05
        with pytest.raises(RuntimeError, match="fixture guard expired"):
            client._reply()
        assert time.monotonic() - started < 0.20


@pytest.mark.skipif(os.name == "nt", reason="owned observer uses POSIX control pipes")
def test_multiple_bounded_reply_frames_preserve_buffered_next_reply():
    reader_fd, writer_fd = os.pipe()
    with os.fdopen(reader_fd, "rb", buffering=0) as reader, os.fdopen(writer_fd, "wb", buffering=0) as writer:
        client = ScopedRecoveryClient(reader, None, "token", [])
        client.deadline = time.monotonic() + 1
        writer.write(b'{"token":"token","op":"READY"}\n{"token":"token","op":"COMPLETED"}\n')
        assert client._reply()["op"] == "READY"
        assert client._reply()["op"] == "COMPLETED"


@pytest.mark.skipif(os.name == "nt", reason="owned observer uses POSIX control pipes")
def test_reply_bound_applies_before_json_parsing():
    import threading

    reader_fd, writer_fd = os.pipe()
    with os.fdopen(reader_fd, "rb", buffering=0) as reader, os.fdopen(writer_fd, "wb", buffering=0) as writer:
        client = ScopedRecoveryClient(reader, None, "token", [])
        client.deadline = time.monotonic() + 1

        def send():
            writer.write(b"x" * 65536)

        thread = threading.Thread(target=send)
        thread.start()
        try:
            with pytest.raises(RuntimeError, match="invalid owned observer reply"):
                client._reply()
        finally:
            thread.join(timeout=1)
        assert not thread.is_alive()
