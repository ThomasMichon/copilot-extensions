"""Strict portable framing and native, retained Windows ownership sections."""

from __future__ import annotations

import ctypes as c
import json
import os
import struct
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity
from zdd.private_protocol import PrivateIPCError
from .owner_section_security import assert_private_section

from agent_mcp import owner_section as section
from agent_mcp.owner_section_native import SECTION_BYTES, SectionNative

BINDING = "a" * 64


def _value(**changes):
    return {
        "schema": section.SCHEMA, "version": 1, "binding": BINDING,
        "pid": 42, "birth": "windows:12345", **changes,
    }


def _frame(value) -> bytes:
    encoded = json.dumps(value, separators=(",", ":")).encode("ascii")
    return _raw_frame(encoded)


def _raw_frame(encoded: bytes) -> bytes:
    return (b"AMO1" + struct.pack("<I", len(encoded)) + encoded).ljust(SECTION_BYTES, b"\0")


@pytest.mark.parametrize("changes", [
    {"schema": "unknown"}, {"version": True}, {"version": 2},
    {"binding": "b" * 64}, {"pid": True}, {"pid": 0}, {"pid": 1 << 32},
    {"birth": "linux:12345"}, {"birth": "windows:0"}, {"birth": "windows:01"},
    {"birth": "windows:18446744073709551616"}, {"birth": 12345}, {"extra": "refuse"},
])
def test_invalid_identity_is_refused(changes) -> None:
    with pytest.raises(ValueError):
        section.decode(_frame(_value(**changes)), BINDING)


@pytest.mark.parametrize("packet", [
    b"", bytes(SECTION_BYTES), _frame([]),
    _raw_frame(b'{"schema":"first","schema":"second"}'),
    b"AMO1" + struct.pack("<I", SECTION_BYTES) + bytes(SECTION_BYTES - 8),
    _frame(_value())[:-1] + b"x",
    _raw_frame(b"\xff"),
    _raw_frame(b"[" * 1500 + b"]" * 1500),
    _frame({key: value for key, value in _value().items() if key != "birth"}),
])
def test_malformed_frame_is_refused(packet) -> None:
    with pytest.raises(ValueError):
        section.decode(packet, BINDING)


@pytest.mark.parametrize("binding", [None, "", "A" * 64, "..\\escape", "a" * 65])
def test_binding_cannot_select_an_arbitrary_named_object(binding) -> None:
    with pytest.raises(ValueError):
        section.section_name(binding)


def test_valid_packet_returns_only_native_identity() -> None:
    assert section.decode(_frame(_value()), BINDING) == section.OwnerIdentity(42, "windows:12345")


native_windows = pytest.mark.skipif(sys.platform != "win32", reason="native Windows sections")


@native_windows
def test_section_has_a_protected_current_user_only_dacl() -> None:
    api = SectionNative()
    with section.OwnerSection() as writer:
        assert_private_section(api, writer._mapping)


@native_windows
def test_creation_handle_capture_readonly_retention_and_exact_cleanup() -> None:
    from agent_procutil.daemon_broker import _CREATE_SUSPENDED, _resume

    writer = section.OwnerSection()
    binding, inherited = writer.binding, writer.inherited_handle
    script = (
        f"import sys;sys.path[:0]={sys.path!r}\n"
        "import ctypes as c,json,os\n"
        "from agent_mcp.owner_section import acquire,decode\n"
        "from agent_mcp.owner_section_native import SectionNative,WRITE,SECTION_BYTES\n"
        "api=SectionNative();handle=int(sys.argv[2])\n"
        "try:\n"
        " assert sys.stdin.buffer.read(1)==b'!'\n"
        " view=api.k.MapViewOfFile(handle,WRITE,0,0,SECTION_BYTES)\n"
        " assert not view and c.get_last_error()==5\n"
        " pair=decode(api.read(handle),sys.argv[1])\n"
        " with acquire(sys.argv[1]) as owner:\n"
        "  assert pair.pid==os.getpid()==owner.pid\n"
        "  owner.require_alive()\n"
        "  print(json.dumps({'pid':owner.pid,'birth':owner.birth}),flush=True)\n"
        "  sys.stdin.buffer.read()\n"
        "finally:\n"
        " api.close(handle)\n"
    )
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {"handle_list": [inherited]}
    process = subprocess.Popen(
        [sys._base_executable, "-I", "-c", script, binding, str(inherited)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=startup, close_fds=True,
        creationflags=_CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW,
    )
    resumed = False
    try:
        # Capture from the actual creation handle while execution is still suspended.
        pair = writer.publish(int(process._handle), process.pid)
        assert pair.birth == birth_identity(process.pid)
        with pytest.raises(ValueError, match="already published"):
            writer.publish(int(process._handle), process.pid)
        writer.close()
        writer.close()
        with pytest.raises(ValueError, match="closed"):
            _ = writer.inherited_handle
        _resume(process)
        resumed = True
        output, error = process.communicate(b"!", timeout=10)
        assert process.returncode == 0, error.decode(errors="replace")
        assert json.loads(output) == {"pid": pair.pid, "birth": pair.birth}
        api = SectionNative()
        assert not api.k.OpenFileMappingW(4, False, section.section_name(binding))
        assert c.get_last_error() == 2
    finally:
        writer.close()
        if not resumed:
            _resume(process)
        if process.poll() is None:
            process.communicate(b"!", timeout=10)


@native_windows
def test_collision_missing_unpublished_and_wrong_creation_handle_are_refused(monkeypatch) -> None:
    monkeypatch.setattr(section.secrets, "token_hex", lambda _: BINDING)
    with section.OwnerSection() as writer:
        with pytest.raises(FileExistsError):
            section.OwnerSection()
        with pytest.raises(ValueError, match="frame"):
            section.acquire(writer.binding)
        api = SectionNative()
        with pytest.raises(PrivateIPCError):
            writer.publish(api.k.GetCurrentProcess(), os.getpid() + 1)
        pair = writer.publish(api.k.GetCurrentProcess(), os.getpid())
        with section.acquire(writer.binding) as owner:
            assert (owner.pid, owner.birth) == (pair.pid, pair.birth)
    with pytest.raises(OSError):
        section.acquire(BINDING)


@native_windows
def test_uncertain_publication_cannot_be_retried(monkeypatch) -> None:
    with section.OwnerSection() as writer:
        original = writer._native.write

        def lose_receipt(handle, packet):
            original(handle, packet)
            raise OSError("publication outcome unknown")

        monkeypatch.setattr(writer._native, "write", lose_receipt)
        handle = writer._native.k.GetCurrentProcess()
        with pytest.raises(OSError, match="unknown"):
            writer.publish(handle, os.getpid())
        with pytest.raises(ValueError, match="already published"):
            writer.publish(handle, os.getpid())


@native_windows
@pytest.mark.parametrize("failure", [
    "wrong-birth", "wrong-binding", "dead-owner", "death-after-acquisition",
])
def test_recorded_owner_must_still_match_and_be_alive(failure: str) -> None:
    owner = subprocess.Popen(
        [sys._base_executable, "-I", "-c", "import sys;sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        with section.OwnerSection() as writer:
            pair = writer.publish(int(owner._handle), owner.pid)
            if failure in ("wrong-birth", "wrong-binding"):
                value = _value(
                    binding="b" * 64 if failure == "wrong-binding" else writer.binding,
                    pid=pair.pid, birth="windows:1" if failure == "wrong-birth" else pair.birth,
                )
                writer._native.write(writer._mapping, _frame(value))
            elif failure == "dead-owner":
                owner.communicate(timeout=10)
                assert owner.returncode == 0
            if failure == "death-after-acquisition":
                with section.acquire(writer.binding) as pinned:
                    pinned.require_alive()
                    owner.communicate(timeout=10)
                    assert owner.returncode == 0
                    with pytest.raises(ProcessLookupError):
                        pinned.require_alive()
            with pytest.raises((OSError, ValueError, PrivateIPCError)):
                section.acquire(writer.binding)
    finally:
        if owner.poll() is None:
            owner.communicate(timeout=10)
