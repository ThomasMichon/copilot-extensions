"""Typed Win32 bindings and pinned identities for private named pipes."""

from __future__ import annotations

import ctypes as c
import os
from typing import NoReturn

from .private_protocol import PeerIdentity, PrivateIPCError, Refusal

DWORD = c.c_uint32
BOOL = c.c_int32
HANDLE = c.c_void_p
PTR = c.c_void_p
INVALID_HANDLE = c.c_void_p(-1).value
WAIT_TIMEOUT = 258
IO_PENDING = 997
IO_INCOMPLETE = 996
OPERATION_ABORTED = 995
PIPE_CONNECTED = 535
NOT_FOUND = 1168
BROKEN_PIPE = 109
NO_DATA = 232


class FILETIME(c.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class OVERLAPPED(c.Structure):
    _fields_ = [
        ("Internal", c.c_size_t),
        ("InternalHigh", c.c_size_t),
        ("Offset", DWORD),
        ("OffsetHigh", DWORD),
        ("hEvent", HANDLE),
    ]


class SECURITY_ATTRIBUTES(c.Structure):
    _fields_ = [("length", DWORD), ("descriptor", PTR), ("inherit", BOOL)]


class SID_AND_ATTRIBUTES(c.Structure):
    _fields_ = [("sid", PTR), ("attributes", DWORD)]


def refuse(reason: Refusal = Refusal.IO) -> NoReturn:
    raise PrivateIPCError(reason)


class Native:
    """Load lazily; importing the transport on another OS is harmless."""

    def __init__(self) -> None:
        if os.name != "nt":
            refuse(Refusal.UNAVAILABLE)
        try:
            self.k = c.WinDLL("kernel32", use_last_error=True)
            self.a = c.WinDLL("advapi32", use_last_error=True)
        except OSError:
            raise PrivateIPCError(Refusal.UNAVAILABLE) from None
        signatures = [
            (self.k, "CloseHandle", [HANDLE], BOOL),
            (self.k, "LocalFree", [PTR], PTR),
            (self.k, "GetCurrentProcess", [], HANDLE),
            (self.k, "GetCurrentThread", [], HANDLE),
            (self.k, "OpenProcess", [DWORD, BOOL, DWORD], HANDLE),
            (self.k, "GetProcessId", [HANDLE], DWORD),
            (
                self.k, "DuplicateHandle",
                [HANDLE, HANDLE, HANDLE, c.POINTER(HANDLE), DWORD, BOOL, DWORD], BOOL,
            ),
            (
                self.k, "QueryFullProcessImageNameW",
                [HANDLE, DWORD, c.c_wchar_p, c.POINTER(DWORD)], BOOL,
            ),
            (self.k, "GetProcessTimes", [HANDLE] + [c.POINTER(FILETIME)] * 4, BOOL),
            (self.k, "WaitForSingleObject", [HANDLE, DWORD], DWORD),
            (self.k, "CreateEventW", [PTR, BOOL, BOOL, c.c_wchar_p], HANDLE),
            (
                self.k,
                "CreateNamedPipeW",
                [
                    c.c_wchar_p,
                    DWORD,
                    DWORD,
                    DWORD,
                    DWORD,
                    DWORD,
                    DWORD,
                    c.POINTER(SECURITY_ATTRIBUTES),
                ],
                HANDLE,
            ),
            (
                self.k,
                "CreateFileW",
                [c.c_wchar_p, DWORD, DWORD, PTR, DWORD, DWORD, HANDLE],
                HANDLE,
            ),
            (self.k, "ConnectNamedPipe", [HANDLE, c.POINTER(OVERLAPPED)], BOOL),
            (
                self.k,
                "ReadFile",
                [HANDLE, PTR, DWORD, c.POINTER(DWORD), c.POINTER(OVERLAPPED)],
                BOOL,
            ),
            (
                self.k,
                "WriteFile",
                [HANDLE, PTR, DWORD, c.POINTER(DWORD), c.POINTER(OVERLAPPED)],
                BOOL,
            ),
            (
                self.k,
                "GetOverlappedResult",
                [HANDLE, c.POINTER(OVERLAPPED), c.POINTER(DWORD), BOOL],
                BOOL,
            ),
            (self.k, "CancelIoEx", [HANDLE, c.POINTER(OVERLAPPED)], BOOL),
            (self.k, "GetNamedPipeServerProcessId", [HANDLE, c.POINTER(DWORD)], BOOL),
            (self.k, "GetNamedPipeClientProcessId", [HANDLE, c.POINTER(DWORD)], BOOL),
            (self.a, "OpenProcessToken", [HANDLE, DWORD, c.POINTER(HANDLE)], BOOL),
            (self.a, "OpenThreadToken", [HANDLE, DWORD, BOOL, c.POINTER(HANDLE)], BOOL),
            (self.a, "GetTokenInformation", [HANDLE, DWORD, PTR, DWORD, c.POINTER(DWORD)], BOOL),
            (self.a, "ConvertSidToStringSidW", [PTR, c.POINTER(PTR)], BOOL),
            (
                self.a,
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                [c.c_wchar_p, DWORD, c.POINTER(PTR), c.POINTER(DWORD)],
                BOOL,
            ),
            (self.a, "ImpersonateNamedPipeClient", [HANDLE], BOOL),
            (self.a, "RevertToSelf", [], BOOL),
        ]
        for dll, name, args, result in signatures:
            function = getattr(dll, name)
            function.argtypes = args
            function.restype = result

    def error(self) -> int:
        return c.get_last_error()

    def close(self, handle: int | None) -> None:
        if handle not in (None, 0, INVALID_HANDLE) and not self.k.CloseHandle(handle):
            refuse()

    def free(self, pointer: PTR) -> None:
        if self.k.LocalFree(pointer):
            refuse()

    def token_sid(self, token: int) -> str:
        size = DWORD()
        if self.a.GetTokenInformation(token, 1, None, 0, c.byref(size)):
            refuse(Refusal.PRINCIPAL)
        if self.error() != 122 or not 0 < size.value <= 65536:
            refuse(Refusal.PRINCIPAL)
        buffer = c.create_string_buffer(size.value)
        if not self.a.GetTokenInformation(token, 1, buffer, size, c.byref(size)):
            refuse(Refusal.PRINCIPAL)
        sid = c.cast(buffer, c.POINTER(SID_AND_ATTRIBUTES)).contents.sid
        text = PTR()
        if not self.a.ConvertSidToStringSidW(sid, c.byref(text)):
            refuse(Refusal.PRINCIPAL)
        try:
            return c.wstring_at(text)
        finally:
            self.free(text)

    def process_sid(self, process: int) -> str:
        token = HANDLE()
        if not self.a.OpenProcessToken(process, 8, c.byref(token)):
            refuse(Refusal.PRINCIPAL)
        try:
            return self.token_sid(token.value)
        finally:
            self.close(token.value)

    def current_sid(self) -> str:
        return self.process_sid(self.k.GetCurrentProcess())

    def alive(self, process: int) -> None:
        if self.k.WaitForSingleObject(process, 0) != WAIT_TIMEOUT:
            refuse(Refusal.IDENTITY)

    def identity(self, process: int, pid: int) -> PeerIdentity:
        times = [FILETIME() for _ in range(4)]
        if not self.k.GetProcessTimes(process, *(c.byref(t) for t in times)):
            refuse(Refusal.IDENTITY)
        return PeerIdentity(
            pid,
            str(times[0].high << 32 | times[0].low),
            self.process_sid(process),
        )

    def duplicate_process(self, handle: int, pid: int) -> int:
        """Duplicate spawn-owned custody without reopening a numeric PID."""
        duplicate = HANDLE()
        current = self.k.GetCurrentProcess()
        if not self.k.DuplicateHandle(current, handle, current, c.byref(duplicate), 0, False, 2):
            refuse(Refusal.IDENTITY)
        try:
            if self.k.GetProcessId(duplicate.value) != pid:
                refuse(Refusal.IDENTITY)
            self.alive(duplicate.value)
            return duplicate.value
        except BaseException:
            self.close(duplicate.value)
            raise

    def process_image(self, process: int) -> str:
        """Read the executable through a held native process handle."""
        size = DWORD(32768)
        buffer = c.create_unicode_buffer(size.value)
        if not self.k.QueryFullProcessImageNameW(process, 0, buffer, c.byref(size)):
            refuse(Refusal.IDENTITY)
        return buffer.value

    def peer_pid(self, pipe: int, *, server: bool) -> int:
        pid = DWORD()
        function = (
            self.k.GetNamedPipeServerProcessId if server else self.k.GetNamedPipeClientProcessId
        )
        if not function(pipe, c.byref(pid)) or not pid.value:
            refuse(Refusal.IDENTITY)
        return pid.value

    def pin_peer(
        self,
        pipe: int,
        *,
        server: bool,
        expected: PeerIdentity,
    ) -> tuple[PeerIdentity, int]:
        """Pin a live process and reread the endpoint's native creator PID.

        Named-pipe PID APIs expose no creation time. Expected creation time
        binds acquisition to a known generation; without it, acquisition is
        refused because a pre-acquisition PID recycle cannot be excluded. Pipe-handle duplication
        also does not change the reported creator PID. Neither is proof of
        exclusive endpoint-handle ownership.
        """
        if not isinstance(expected, PeerIdentity):
            refuse(Refusal.IDENTITY)
        pid = self.peer_pid(pipe, server=server)
        process = self.k.OpenProcess(0x1000 | 0x100000, False, pid)
        if not process:
            refuse(Refusal.IDENTITY)
        try:
            identity = self.identity(process, pid)
            self.alive(process)
            if self.peer_pid(pipe, server=server) != pid:
                refuse(Refusal.IDENTITY)
            if identity.principal != self.current_sid():
                refuse(Refusal.PRINCIPAL)
            if expected.principal != identity.principal:
                refuse(Refusal.PRINCIPAL)
            if expected != identity:
                refuse(Refusal.IDENTITY)
            self.alive(process)
            return identity, process
        except BaseException:
            self.close(process)
            raise

    def connected_sid(self, pipe: int) -> str:
        """Identify the client's actual pipe security context after a read."""
        if not self.a.ImpersonateNamedPipeClient(pipe):
            refuse(Refusal.PRINCIPAL)
        token = HANDLE()
        try:
            if not self.a.OpenThreadToken(
                self.k.GetCurrentThread(),
                8,
                True,
                c.byref(token),
            ):
                refuse(Refusal.PRINCIPAL)
        finally:
            # Never run caller code or process-token checks while impersonating.
            if not self.a.RevertToSelf():
                self.close(token.value)
                refuse(Refusal.PRINCIPAL)
        try:
            return self.token_sid(token.value)
        finally:
            self.close(token.value)

    def listener(self, path: str) -> int:
        sid = self.current_sid()
        descriptor = PTR()
        pipe: int | None = None
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{sid}D:P(A;;GA;;;{sid})",
            1,
            c.byref(descriptor),
            None,
        ):
            refuse(Refusal.PRINCIPAL)
        try:
            attributes = SECURITY_ATTRIBUTES(c.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
            # Duplex, overlapped, first instance; byte mode, reject remote clients.
            pipe = self.k.CreateNamedPipeW(
                path,
                3 | 0x40000000 | 0x80000,
                8,
                1,
                65536,
                65536,
                0,
                c.byref(attributes),
            )
            if pipe == INVALID_HANDLE:
                refuse(Refusal.UNAVAILABLE)
        finally:
            try:
                self.free(descriptor)
            except PrivateIPCError:
                if pipe is not None:
                    self.close(pipe)
                raise
        if pipe is None:
            refuse(Refusal.UNAVAILABLE)
        return pipe


_native: Native | None = None


def native() -> Native:
    global _native
    if _native is None:
        _native = Native()
    return _native


def validate_path(path: str) -> None:
    """Only local named pipes; no UNC host aliases or namespace traversal."""
    prefix = "\\\\.\\pipe\\"
    if (
        not isinstance(path, str)
        or not path.startswith(prefix)
        or not path[len(prefix) :]
        or len(path) > 256
        or any(char in path[len(prefix) :] for char in "\\/\0")
    ):
        refuse(Refusal.UNAVAILABLE)
