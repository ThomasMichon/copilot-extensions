"""Stdlib-only Windows owner snapshot, safe to embed before provider discovery."""

import ctypes as c
from dataclasses import dataclass
import json
import os
import re
import struct

SCHEMA = "agent-mcp.windows-native-owner"
SECTION_BYTES = 4096
_KEYS = {"schema", "version", "binding", "pid", "birth"}


def section_name(binding):
    if not isinstance(binding, str) or re.fullmatch(r"[0-9a-f]{64}", binding) is None:
        raise ValueError("Invalid native owner section binding")
    return "Local\\agent-mcp-owner-v1-" + binding


@dataclass(frozen=True)
class OwnerIdentity:
    pid: int
    birth: str


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate native owner field")
        result[key] = value
    return result


def decode(packet, binding):
    """Refuse partial, oversized, noncanonical or differently bound records."""
    section_name(binding)
    if len(packet) != SECTION_BYTES or packet[:4] != b"AMO1":
        raise ValueError("Invalid native owner section frame")
    size = struct.unpack_from("<I", packet, 4)[0]
    if not 0 < size <= SECTION_BYTES - 8 or any(packet[8 + size:]):
        raise ValueError("Invalid native owner section length")
    try:
        value = json.loads(packet[8:8 + size].decode("ascii"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid native owner section JSON") from error
    if (
        not isinstance(value, dict) or set(value) != _KEYS
        or value["schema"] != SCHEMA or type(value["version"]) is not int
        or value["version"] != 1 or value["binding"] != binding
        or type(value["pid"]) is not int or not 0 < value["pid"] <= 0xFFFFFFFF
        or not isinstance(value["birth"], str)
        or re.fullmatch(r"windows:[1-9][0-9]{0,19}", value["birth"]) is None
        or int(value["birth"].split(":")[1]) >= 1 << 64
    ):
        raise ValueError("Invalid native owner section identity")
    return OwnerIdentity(value["pid"], value["birth"])


class _WindowsAPI:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Native owner snapshot requires Windows")
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.a = c.WinDLL("advapi32", use_last_error=True)
        pointer, integer = c.c_void_p, c.c_uint32
        for library, name, arguments, result in (
            (self.k, "OpenFileMappingW", [integer, c.c_int32, c.c_wchar_p], pointer),
            (self.k, "MapViewOfFile", [pointer, integer, integer, integer, c.c_size_t], pointer),
            (self.k, "UnmapViewOfFile", [pointer], c.c_int32),
            (self.k, "CloseHandle", [pointer], c.c_int32),
            (self.k, "GetCurrentProcess", [], pointer),
            (self.k, "OpenProcess", [integer, c.c_int32, integer], pointer),
            (self.k, "GetProcessId", [pointer], integer),
            (self.k, "GetProcessTimes", [pointer] + [c.POINTER(c.c_uint64)] * 4, c.c_int32),
            (self.k, "WaitForSingleObject", [pointer, integer], integer),
            (self.a, "OpenProcessToken", [pointer, integer, c.POINTER(pointer)], c.c_int32),
            (self.a, "GetTokenInformation",
             [pointer, integer, pointer, integer, c.POINTER(integer)], c.c_int32),
            (self.a, "IsValidSid", [pointer], c.c_int32),
            (self.a, "GetLengthSid", [pointer], integer),
        ):
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    def require(self, value):
        if not value:
            raise c.WinError(c.get_last_error())
        return value

    def close(self, handle):
        if handle is not None:
            self.require(self.k.CloseHandle(handle))

    def principal(self, process):
        token = c.c_void_p()
        self.require(self.a.OpenProcessToken(process, 8, c.byref(token)))
        try:
            size = c.c_uint32()
            if self.a.GetTokenInformation(token, 1, None, 0, c.byref(size)):
                raise ValueError("Invalid native owner principal size")
            if c.get_last_error() != 122 or not 0 < size.value <= 65536:
                raise ValueError("Native owner principal is unavailable")
            buffer = c.create_string_buffer(size.value)
            self.require(self.a.GetTokenInformation(token, 1, buffer, size, c.byref(size)))
            sid = c.cast(buffer, c.POINTER(c.c_void_p)).contents.value
            self.require(self.a.IsValidSid(sid))
            length = self.a.GetLengthSid(sid)
            if not 0 < length <= 68:
                raise ValueError("Invalid native owner principal")
            return c.string_at(sid, length)
        finally:
            self.close(token.value)

    def identity(self, handle, expected):
        times = [c.c_uint64() for _ in range(4)]
        self.require(self.k.GetProcessTimes(handle, *(c.byref(value) for value in times)))
        if (
            self.k.GetProcessId(handle) != expected.pid
            or "windows:" + str(times[0].value) != expected.birth
            or self.principal(handle) != self.principal(self.k.GetCurrentProcess())
        ):
            raise ValueError("Native owner section identity changed")


class WindowsOrigin:
    """Pin the live native owner before any installed provider is inspected."""

    def __init__(self, binding):
        name = section_name(binding)
        self._native = native = _WindowsAPI()
        self._handle = None
        mapping = native.require(native.k.OpenFileMappingW(4, False, name))
        try:
            view = native.require(native.k.MapViewOfFile(mapping, 4, 0, 0, SECTION_BYTES))
            try:
                identity = decode(c.string_at(view, SECTION_BYTES), binding)
            finally:
                native.require(native.k.UnmapViewOfFile(view))
            self.pid, self.birth = identity.pid, identity.birth
            self._handle = native.require(native.k.OpenProcess(0x101000, False, self.pid))
            try:
                native.identity(self._handle, identity)
                self.require_alive()
            except BaseException:
                self.close()
                raise
        finally:
            native.close(mapping)

    def require_alive(self):
        if self._handle is None:
            raise ValueError("Native owner snapshot is closed")
        result = self._native.k.WaitForSingleObject(self._handle, 0)
        if result == 0:
            raise ProcessLookupError("Originating owner exited before MCP entry")
        if result != 258:
            raise OSError("Originating owner observation unavailable")

    def close(self):
        if self._handle is not None:
            self._native.close(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
