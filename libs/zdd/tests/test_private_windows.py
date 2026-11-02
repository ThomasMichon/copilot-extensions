"""Portable policy/lifetime checks and guarded real Windows process proofs."""

from __future__ import annotations

import ctypes as c
import inspect
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, get_type_hints

import pytest

from zdd import private_windows as transport
from zdd import private_windows_native as win
from zdd.private_protocol import (
    Deadline,
    PeerIdentity,
    PrivateIPCError,
    Refusal,
    read_frame,
    write_frame,
)

windows = pytest.mark.skipif(os.name != "nt", reason="requires native Windows APIs")
PATH = "\\\\.\\pipe\\private-channel-test"
PEER = PeerIdentity(123, "456", "S-1-5-21-1-2-3-1000")


def test_fixed_width_structures() -> None:
    assert c.sizeof(win.DWORD) == 4
    assert c.sizeof(win.FILETIME) == 8
    assert c.sizeof(win.OVERLAPPED) == (32 if c.sizeof(c.c_void_p) == 8 else 20)
    assert win.OVERLAPPED.hEvent.offset == (24 if c.sizeof(c.c_void_p) == 8 else 16)
    assert win.SECURITY_ATTRIBUTES.descriptor.offset == c.sizeof(c.c_void_p)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "http://localhost",
        "\\\\host\\pipe\\test",
        "\\\\.\\pipe\\",
        "\\\\.\\pipe\\test\\nested",
        "\\\\.\\pipe\\test/other",
        "\\\\.\\pipe\\test\0suffix",
        "\\\\.\\pipe\\" + "x" * 257,
    ],
)
def test_refuse_nonlocal_paths(path: str) -> None:
    with pytest.raises(PrivateIPCError) as caught:
        win.validate_path(path)
    assert caught.value.reason is Refusal.UNAVAILABLE
    assert str(caught.value) == Refusal.UNAVAILABLE.value


def test_local_path() -> None:
    win.validate_path(PATH)


def test_connect_requires_expected_native_identity() -> None:
    with pytest.raises(PrivateIPCError) as caught:
        transport.connect(PATH, None, Deadline(1))
    assert caught.value.reason is Refusal.IDENTITY


@pytest.mark.parametrize("operation", [transport.WindowsListener.accept, win.Native.pin_peer])
def test_windows_peer_identity_is_required_in_public_signature(operation: Any) -> None:
    assert inspect.signature(operation).parameters["expected"].default is inspect.Parameter.empty
    assert get_type_hints(operation)["expected"] is PeerIdentity


def test_windows_channel_constructor_allows_pre_authentication_process() -> None:
    assert get_type_hints(transport.WindowsChannel.__init__)["process"] == int | None


@pytest.mark.skipif(os.name == "nt", reason="non-Windows import/refusal contract")
def test_safe_import_and_unavailable_off_windows() -> None:
    with pytest.raises(PrivateIPCError, match="^private-ipc-unavailable$"):
        transport.WindowsListener(PATH)
    with pytest.raises(PrivateIPCError, match="^private-ipc-unavailable$"):
        transport.connect(PATH, PEER, Deadline(1))


@pytest.mark.parametrize(
    "mismatch,reason",
    [
        ("pid", Refusal.IDENTITY),
        ("start", Refusal.IDENTITY),
        ("principal", Refusal.PRINCIPAL),
        ("pipe-race", Refusal.IDENTITY),
        ("exited", Refusal.IDENTITY),
        ("current-sid", Refusal.PRINCIPAL),
    ],
)
def test_pinned_identity_refusal(mismatch: str, reason: Refusal) -> None:
    api = object.__new__(win.Native)
    closed: list[int] = []
    api.k = SimpleNamespace(OpenProcess=lambda *args: 55)
    api.close = closed.append
    api.identity = lambda *args: PEER
    api.current_sid = lambda: "S-1-0-0" if mismatch == "current-sid" else PEER.principal
    pids = iter([PEER.pid, PEER.pid + (mismatch == "pipe-race")])
    api.peer_pid = lambda *args, **kwargs: next(pids)

    def alive(process: int) -> None:
        if mismatch == "exited":
            win.refuse(Refusal.IDENTITY)

    api.alive = alive
    expected = {
        "pid": replace(PEER, pid=124),
        "start": replace(PEER, start_time="457"),
        "principal": replace(PEER, principal="S-1-5-21-4-5-6-1000"),
    }.get(mismatch, PEER)
    with pytest.raises(PrivateIPCError) as caught:
        api.pin_peer(10, server=True, expected=expected)
    assert caught.value.reason is reason
    assert closed == [55]


def test_connected_sid_reverts_before_token_read() -> None:
    api = object.__new__(win.Native)
    calls: list[str] = []

    def open_token(thread: int, access: int, self_: bool, output: Any) -> bool:
        c.cast(output, c.POINTER(win.HANDLE)).contents.value = 22
        calls.append("open")
        return True

    api.k = SimpleNamespace(GetCurrentThread=lambda: -2)
    api.a = SimpleNamespace(
        ImpersonateNamedPipeClient=lambda pipe: calls.append("impersonate") or True,
        OpenThreadToken=open_token,
        RevertToSelf=lambda: calls.append("revert") or True,
    )
    api.token_sid = lambda token: calls.append("sid") or PEER.principal
    api.close = lambda token: calls.append("close")
    assert api.connected_sid(10) == PEER.principal
    assert calls == ["impersonate", "open", "revert", "sid", "close"]


def test_explicit_dacl_and_native_listener_flags() -> None:
    api = object.__new__(win.Native)
    captured: dict[str, Any] = {}
    api.current_sid = lambda: PEER.principal
    api.free = lambda pointer: captured.update(freed=True)

    def descriptor(text: str, *args: Any) -> bool:
        captured["sddl"] = text
        return True

    def pipe(path: str, flags: int, mode: int, *args: Any) -> int:
        captured.update(path=path, flags=flags, mode=mode, instances=args[0])
        attributes = c.cast(args[-1], c.POINTER(win.SECURITY_ATTRIBUTES)).contents
        assert not attributes.inherit
        return 11

    api.a = SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=descriptor)
    api.k = SimpleNamespace(CreateNamedPipeW=pipe)
    assert api.listener(PATH) == 11
    assert captured["sddl"] == f"O:{PEER.principal}D:P(A;;GA;;;{PEER.principal})"
    assert captured["flags"] == 3 | 0x40000000 | 0x80000
    assert captured["mode"] == 8
    assert captured["instances"] == 1
    assert captured["freed"]


def test_client_identification_sqos_and_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def create_file(path: str, *args: Any) -> int:
        captured["flags"] = args[-2]
        return 11

    api = SimpleNamespace(
        k=SimpleNamespace(CreateFileW=create_file),
        pin_peer=lambda *args, **kwargs: (PEER, 12),
    )

    class Channel:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.peer = PEER

        def write_all(self, value: bytes, deadline: Deadline) -> None:
            captured["handshake"] = value

        def read_exact(self, count: int, deadline: Deadline) -> bytes:
            return b"\x01"

    monkeypatch.setattr(transport, "native", lambda: api)
    monkeypatch.setattr(transport, "WindowsChannel", Channel)
    assert transport.connect(PATH, PEER, Deadline(1)).peer == PEER
    assert captured["flags"] == 0x40000000 | 0x100000 | 0x10000
    assert captured["handshake"] == b"\x00"


class FakeIO:
    def __init__(self, *, uncertain: bool = False, cancel_failed: bool = False) -> None:
        self.cancelled = False
        self.uncertain = uncertain
        self.cancel_failed = cancel_failed
        self.closed: list[int] = []
        self.k = SimpleNamespace(
            CreateEventW=lambda *args: 77,
            ReadFile=lambda *args: False,
            WriteFile=lambda *args: False,
            ConnectNamedPipe=lambda *args: False,
            GetOverlappedResult=self.result,
            CancelIoEx=self.cancel,
            WaitForSingleObject=lambda *args: win.WAIT_TIMEOUT,
        )
        self.last_error = win.IO_PENDING

    def error(self) -> int:
        return self.last_error

    def close(self, handle: int | None) -> None:
        if handle is not None:
            self.closed.append(handle)

    def alive(self, process: int) -> None:
        pass

    def result(self, *args: Any) -> bool:
        self.last_error = (
            win.OPERATION_ABORTED if self.cancelled and not self.uncertain else win.IO_INCOMPLETE
        )
        return False

    def cancel(self, *args: Any) -> bool:
        self.cancelled = True
        self.last_error = 5 if self.cancel_failed else 0
        return not self.cancel_failed


@pytest.mark.parametrize("kind", ["read", "write", "connect"])
@pytest.mark.parametrize("reason", [Refusal.DEADLINE, Refusal.CANCELLED])
def test_pending_io_cancels_before_release(kind: str, reason: Refusal) -> None:
    api = FakeIO()
    channel = transport.WindowsChannel(11, PEER, 12, api=api)
    cancelled = threading.Event()
    deadline = Deadline(0.01, cancelled=cancelled)
    original_result = api.k.GetOverlappedResult

    def result(*args: Any) -> bool:
        if reason is Refusal.CANCELLED:
            cancelled.set()
        return original_result(*args)

    api.k.GetOverlappedResult = result
    with pytest.raises(PrivateIPCError) as caught:
        channel._io(kind, c.create_string_buffer(1), 1, deadline)
    assert caught.value.reason is reason
    assert api.cancelled
    assert sorted(api.closed) == [11, 12, 77]
    assert channel._pipe is None


def test_uncertain_cancel_retains_kernel_memory() -> None:
    api = FakeIO(uncertain=True)
    channel = transport.WindowsChannel(11, PEER, 12, api=api)
    start = time.monotonic()
    with pytest.raises(PrivateIPCError) as caught:
        channel.read_exact(1, Deadline(0.001))
    assert caught.value.reason is Refusal.CANCELLATION_UNRESOLVED
    assert time.monotonic() - start < 0.5
    assert api.closed == [12]
    assert any(op.pipe == 11 and op.buffer is not None for op in transport._retained)
    api.uncertain = False
    transport._reap()
    assert sorted(api.closed) == [11, 12, 77]


def test_unknown_client_creation_time_refused_without_connect() -> None:
    listener = object.__new__(transport.WindowsListener)
    with pytest.raises(PrivateIPCError) as caught:
        listener.accept(Deadline(1), expected=None)
    assert caught.value.reason is Refusal.IDENTITY


def test_unknown_peer_creation_time_refused_without_native_probe() -> None:
    api = object.__new__(win.Native)
    with pytest.raises(PrivateIPCError) as caught:
        api.pin_peer(11, server=False, expected=None)
    assert caught.value.reason is Refusal.IDENTITY


def test_cancel_failure_is_explicit() -> None:
    api = FakeIO(cancel_failed=True)
    channel = transport.WindowsChannel(11, PEER, 12, api=api)
    with pytest.raises(PrivateIPCError, match="^private-ipc-io-failed$"):
        channel.read_exact(1, Deadline(0.001))
    assert sorted(api.closed) == [11, 12, 77]


def test_cancel_not_found_completion_race() -> None:
    api = FakeIO()

    def cancel(*args: Any) -> bool:
        api.cancelled = True
        api.last_error = win.NOT_FOUND
        return False

    api.k.CancelIoEx = cancel
    channel = transport.WindowsChannel(11, PEER, 12, api=api)
    with pytest.raises(PrivateIPCError) as caught:
        channel.read_exact(1, Deadline(0.001))
    assert caught.value.reason is Refusal.DEADLINE
    assert sorted(api.closed) == [11, 12, 77]


def test_accept_rejects_connected_token_not_process_sid() -> None:
    api = FakeIO()
    api.pin_peer = lambda *args, **kwargs: (PEER, 12)
    api.connected_sid = lambda pipe: "S-1-0-0"
    listener = object.__new__(transport.WindowsListener)
    listener._api, listener._pipe, listener._lock = api, 11, threading.RLock()
    original_io = transport.WindowsChannel._io

    def io(
        channel: transport.WindowsChannel,
        kind: str,
        buffer: Any,
        count: int,
        deadline: Deadline,
    ) -> int:
        if kind == "connect":
            return 0
        if kind == "read":
            c.memmove(buffer, b"\x00", 1)
            return 1
        pytest.fail("server acknowledged an unverified connected principal")

    transport.WindowsChannel._io = io
    try:
        with pytest.raises(PrivateIPCError) as caught:
            listener.accept(Deadline(1), PEER)
        assert caught.value.reason is Refusal.PRINCIPAL
        assert sorted(api.closed) == [11, 12]
    finally:
        transport.WindowsChannel._io = original_io


def test_partial_transfers_use_one_deadline() -> None:
    api = FakeIO()
    channel = transport.WindowsChannel(11, PEER, 12, api=api)
    deadlines: list[Deadline] = []
    incoming = iter([b"ab", b"c"])
    outgoing: list[bytes] = []

    def io(kind: str, buffer: Any, count: int, deadline: Deadline) -> int:
        deadlines.append(deadline)
        if kind == "read":
            chunk = next(incoming)
            c.memmove(buffer, chunk, len(chunk))
            return len(chunk)
        outgoing.append(buffer.raw[:1])
        return 1

    channel._io = io
    deadline = Deadline(1)
    try:
        assert channel.read_exact(3, deadline) == b"abc"
        channel.write_all(b"xyz", deadline)
        assert b"".join(outgoing) == b"xyz"
        assert all(value is deadline for value in deadlines)
    finally:
        channel.close()


@pytest.mark.parametrize("failed_handle", [11, 12])
def test_close_failure_preserves_failed_handle_and_releases_other(failed_handle) -> None:
    api = FakeIO()

    def close(handle: int | None) -> None:
        api.closed.append(handle)
        if handle == failed_handle:
            win.refuse()

    api.close = close
    channel = transport.WindowsChannel(11, PEER, 12, api=api)
    with pytest.raises(PrivateIPCError, match="^private-ipc-io-failed$"):
        channel.close()
    assert api.closed == [11, 12]
    assert channel._pipe == (11 if failed_handle == 11 else None)
    assert channel._process == (12 if failed_handle == 12 else None)
    api.close = lambda handle: api.closed.append(handle) if handle is not None else None
    channel.close()
    assert api.closed == [11, 12, failed_handle]
    assert channel._pipe is channel._process is None


def _self_identity() -> PeerIdentity:
    api = win.native()
    return api.identity(api.k.GetCurrentProcess(), os.getpid())


def _line(process: subprocess.Popen[str]) -> dict[str, Any]:
    output: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
    reader.start()
    value = output.get(timeout=8)
    reader.join(timeout=1)
    assert value, "native helper exited before control response"
    return json.loads(value)


@contextmanager
def _client(
    path: str,
    expected: PeerIdentity,
    mode: str,
) -> Iterator[subprocess.Popen[str]]:
    # A caller-owned, windowless test helper; no credentials in argv or env.
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--native-client", path, mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps(asdict(expected)) + "\n")
        process.stdin.flush()
        yield process
    finally:
        # The helper exits cooperatively, never by signalling its numeric PID.
        _, stderr = process.communicate("stop\n", timeout=8)
        assert process.returncode == 0, "native helper failed"
        assert not stderr, "native helper emitted unexpected diagnostics"


def _path() -> str:
    return "\\\\.\\pipe\\private-channel-test-" + uuid.uuid4().hex


@windows
def test_real_retained_peer_exit_survives_channel_close_and_reaping() -> None:
    path = _path()
    listener = transport.WindowsListener(path)
    reference = None
    try:
        with _client(path, _self_identity(), "idle") as process:
            peer = PeerIdentity(**_line(process)["identity"])
            channel = listener.accept(Deadline(5), expected=peer)
            try:
                assert _line(process) == {"connected": True}
                reference = channel.retain_peer(Deadline(1))
                assert reference.peer == peer
            finally:
                channel.close()
            assert reference.exited(Deadline(1)) is False
            with pytest.raises(PrivateIPCError) as caught:
                reference.wait_exit(Deadline(0.03))
            assert caught.value.reason is Refusal.DEADLINE
        reference.wait_exit(Deadline(1))
        assert reference.exited(Deadline(1)) is True
    finally:
        if reference is not None:
            reference.close()
        listener.close()


@windows
def test_real_cannot_acquire_peer_exit_custody_after_exit() -> None:
    path = _path()
    listener = transport.WindowsListener(path)
    channel = None
    try:
        with _client(path, _self_identity(), "idle") as process:
            peer = PeerIdentity(**_line(process)["identity"])
            channel = listener.accept(Deadline(5), expected=peer)
            assert _line(process) == {"connected": True}
        with pytest.raises(PrivateIPCError) as caught:
            channel.retain_peer(Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY
    finally:
        if channel is not None:
            channel.close()
        listener.close()


@windows
def test_real_process_framing_and_both_peer_identities() -> None:
    path = _path()
    listener = transport.WindowsListener(path)
    try:
        with _client(path, _self_identity(), "echo") as process:
            client = PeerIdentity(**_line(process)["identity"])
            channel = listener.accept(Deadline(5), expected=client)
            try:
                assert channel.peer == client
                write_frame(channel, {"sample": "synthetic"}, Deadline(5))
                assert read_frame(channel, Deadline(5)) == {"sample": "synthetic"}
            finally:
                channel.close()
    finally:
        listener.close()


@windows
@pytest.mark.parametrize("field", ["pid", "start_time", "principal"])
def test_real_stale_server_refusal(field: str) -> None:
    expected = _self_identity()
    expected = replace(
        expected,
        **{
            field: expected.pid + 100000
            if field == "pid"
            else "0"
            if field == "start_time"
            else "S-1-0-0",
        },
    )
    path = _path()
    listener = transport.WindowsListener(path)
    try:
        with _client(path, expected, "echo") as process:
            _line(process)
            result = _line(process)
            assert result["refusal"] == (
                Refusal.PRINCIPAL.value if field == "principal" else Refusal.IDENTITY.value
            )
    finally:
        listener.close()


@windows
@pytest.mark.parametrize("field", ["pid", "start_time", "principal"])
def test_real_stale_client_refusal(field: str) -> None:
    path = _path()
    listener = transport.WindowsListener(path)
    try:
        with _client(path, _self_identity(), "idle") as process:
            expected = PeerIdentity(**_line(process)["identity"])
            expected = replace(
                expected,
                **{
                    field: expected.pid + 100000
                    if field == "pid"
                    else "0"
                    if field == "start_time"
                    else "S-1-0-0",
                },
            )
            with pytest.raises(PrivateIPCError) as caught:
                listener.accept(Deadline(5), expected=expected)
            assert caught.value.reason is (
                Refusal.PRINCIPAL if field == "principal" else Refusal.IDENTITY
            )
    finally:
        listener.close()


@windows
def test_real_occupied_name_and_accept_timeout() -> None:
    path = _path()
    listener = transport.WindowsListener(path)
    try:
        with pytest.raises(PrivateIPCError, match="private-ipc-unavailable"):
            transport.WindowsListener(path)
        start = time.monotonic()
        with pytest.raises(PrivateIPCError) as caught:
            listener.accept(Deadline(0.05), expected=_self_identity())
        assert caught.value.reason in (Refusal.DEADLINE, Refusal.CANCELLATION_UNRESOLVED)
        assert time.monotonic() - start < 1
    finally:
        listener.close()


@windows
@pytest.mark.parametrize("cancel", [False, True])
def test_real_absent_connect_timeout(cancel: bool) -> None:
    start = time.monotonic()
    cancelled = threading.Event()
    timer = threading.Timer(0.05, cancelled.set) if cancel else None
    if timer is not None:
        timer.start()
    try:
        with pytest.raises(PrivateIPCError) as caught:
            transport.connect(
                _path(),
                _self_identity(),
                Deadline(1 if cancel else 0.05, cancelled=cancelled),
            )
        assert caught.value.reason is (Refusal.CANCELLED if cancel else Refusal.DEADLINE)
        assert time.monotonic() - start < 1
    finally:
        if timer is not None:
            timer.join(timeout=1)


@windows
@pytest.mark.parametrize("kind", ["partial-read", "blocked-write"])
@pytest.mark.parametrize("cancel", [False, True])
def test_real_pending_io_deadline_and_cancellation(kind: str, cancel: bool) -> None:
    path = _path()
    listener = transport.WindowsListener(path)
    try:
        with _client(path, _self_identity(), kind) as process:
            expected = PeerIdentity(**_line(process)["identity"])
            channel = listener.accept(Deadline(5), expected=expected)
            try:
                assert _line(process)["connected"]
                cancelled = threading.Event()
                timer = threading.Timer(0.05, cancelled.set) if cancel else None
                if timer is not None:
                    timer.start()
                try:
                    with pytest.raises(PrivateIPCError) as caught:
                        deadline = Deadline(1 if cancel else 0.05, cancelled=cancelled)
                        if kind == "partial-read":
                            channel.read_exact(2, deadline)
                        else:
                            channel.write_all(b"x" * (2 * 1024 * 1024), deadline)
                    assert caught.value.reason in (
                        Refusal.CANCELLED if cancel else Refusal.DEADLINE,
                        Refusal.CANCELLATION_UNRESOLVED,
                    )
                    assert channel._pipe is None
                finally:
                    if timer is not None:
                        timer.join(timeout=1)
            finally:
                channel.close()
    finally:
        listener.close()


@windows
def test_real_explicit_owner_only_dacl() -> None:
    api = win.native()
    listener = transport.WindowsListener(_path())
    descriptor, owner, dacl = win.PTR(), win.PTR(), win.PTR()
    get_security = api.a.GetSecurityInfo
    get_security.argtypes = [win.HANDLE, win.DWORD, win.DWORD] + [c.POINTER(win.PTR)] * 5
    get_security.restype = win.DWORD
    get_ace = api.a.GetAce
    get_ace.argtypes = [win.PTR, win.DWORD, c.POINTER(win.PTR)]
    get_ace.restype = win.BOOL
    get_control = api.a.GetSecurityDescriptorControl
    get_control.argtypes = [win.PTR, c.POINTER(c.c_uint16), c.POINTER(win.DWORD)]
    get_control.restype = win.BOOL
    try:
        assert (
            get_security(
                listener._pipe,
                6,
                5,
                c.byref(owner),
                None,
                c.byref(dacl),
                None,
                c.byref(descriptor),
            )
            == 0
        )
        assert dacl.value
        control, revision = c.c_uint16(), win.DWORD()
        assert get_control(descriptor, c.byref(control), c.byref(revision))
        assert control.value & 0x1000
        # ACL's AceCount is a WORD at offset four; one allow ACE only.
        assert c.c_uint16.from_address(dacl.value + 4).value == 1
        ace = win.PTR()
        assert get_ace(dacl, 0, c.byref(ace))
        assert c.c_ubyte.from_address(ace.value).value == 0
        assert c.c_uint32.from_address(ace.value + 4).value in (0x10000000, 0x1F01FF)
        for sid in (owner, win.PTR(ace.value + 8)):
            text = win.PTR()
            assert api.a.ConvertSidToStringSidW(sid, c.byref(text))
            try:
                assert c.wstring_at(text) == api.current_sid()
            finally:
                api.free(text)
    finally:
        if descriptor.value:
            api.free(descriptor)
        listener.close()


def _native_client(path: str, mode: str) -> None:
    expected = PeerIdentity(**json.loads(sys.stdin.readline()))
    print(json.dumps({"identity": asdict(_self_identity())}), flush=True)
    channel = None
    try:
        channel = transport.connect(path, expected, Deadline(5))
        if mode == "echo":
            write_frame(channel, read_frame(channel, Deadline(5)), Deadline(5))
            sys.stdin.readline()
        else:
            if mode == "partial-read":
                channel.write_all(b"x", Deadline(5))
            print(json.dumps({"connected": True}), flush=True)
            sys.stdin.readline()
    except PrivateIPCError as error:
        print(json.dumps({"refusal": error.reason.value}), flush=True)
    finally:
        if channel is not None:
            channel.close()


if __name__ == "__main__":
    assert sys.argv[1] == "--native-client"
    _native_client(sys.argv[2], sys.argv[3])
