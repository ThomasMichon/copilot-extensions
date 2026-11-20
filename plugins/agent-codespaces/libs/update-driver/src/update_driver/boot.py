"""Read exact native kernel boot identity, never uptime or process-age inference."""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


class ExecutionDomainEvidenceError(RuntimeError):
    """Native continuity evidence is unsupported, unavailable or inconsistent."""


@dataclass(frozen=True)
class ExecutionDomainBootEvidence:
    """An observation, not migration authority or installation admission."""

    version: int
    platform: str
    source: str
    domain: str
    boot: str


def parse_execution_domain_boot_evidence(value: object) -> ExecutionDomainBootEvidence:
    """Strictly decode the version-1 observation; parsing cannot attest its source."""
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "platform", "source", "domain", "boot"}
        or type(value["version"]) is not int or value["version"] != 1
        or not isinstance(value["platform"], str) or value["platform"] != "windows"
        or not isinstance(value["source"], str)
        or value["source"] != "windows-vm-generation-counter"
        or not isinstance(value["domain"], str) or len(value["domain"]) != 32
        or any(character not in "0123456789abcdef" for character in value["domain"])
        or value["domain"] in ("0" * 32, "f" * 32)
        or not valid_boot_identity(value["boot"])
        or not value["boot"].startswith("windows:")
    ):
        raise ExecutionDomainEvidenceError("invalid native execution-domain boot evidence")
    return ExecutionDomainBootEvidence(
        value["version"], value["platform"], value["source"], value["domain"], value["boot"],
    )


def _windows_generation_counter() -> str:
    import ctypes

    try:
        native = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        create = native.CreateFileW
        create.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ]
        create.restype = ctypes.c_void_p
        read = native.DeviceIoControl
        read.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
        ]
        read.restype = ctypes.c_int
        close = native.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        handle = create(r"\\.\VmGenerationCounter", 0xC0000000, 3, None, 3, 0, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise OSError(getattr(ctypes, "get_last_error")(), "VM generation device unavailable")
        try:
            data = (ctypes.c_ubyte * 16)()
            returned = ctypes.c_uint32()
            # Zero input selects a finite read, never the wait-for-change operation.
            if not read(handle, 0x32C004, None, 0, data, 16, ctypes.byref(returned), None):
                raise OSError(getattr(ctypes, "get_last_error")(), "VM generation read unavailable")
            value = bytes(data).hex()
            if returned.value != 16 or value in ("0" * 32, "f" * 32):
                raise ExecutionDomainEvidenceError("invalid native execution-domain identity")
        finally:
            if not close(handle):
                raise OSError(getattr(ctypes, "get_last_error")(), "VM generation handle close failed")
    except (OSError, AttributeError) as error:
        raise ExecutionDomainEvidenceError(
            "native execution-domain evidence unavailable; no identity fallback",
        ) from error
    return value


def native_execution_domain_boot_evidence() -> ExecutionDomainBootEvidence:
    """Sample native continuity without probing unsupported venues or changing state."""
    if os.name != "nt" or sys.platform != "win32":
        raise ExecutionDomainEvidenceError(
            "unsupported native execution-domain platform; no qualified provider",
        )
    try:
        before = kernel_boot_identity()
        if not valid_boot_identity(before) or not before.startswith("windows:"):
            raise ExecutionDomainEvidenceError("invalid native execution-domain boot evidence")
        first = _windows_generation_counter()
        second = _windows_generation_counter()
        after = kernel_boot_identity()
    except (OSError, RuntimeError) as error:
        if isinstance(error, ExecutionDomainEvidenceError):
            raise
        raise ExecutionDomainEvidenceError("native execution-domain boot unavailable") from error
    if first != second or before != after:
        raise ExecutionDomainEvidenceError("incoherent native execution-domain boot evidence")
    return parse_execution_domain_boot_evidence({
        "version": 1, "platform": "windows", "source": "windows-vm-generation-counter",
        "domain": first, "boot": after,
    })


def valid_boot_identity(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        platform, identifier = value.split(":", 1)
        parsed = uuid.UUID(identifier)
        return (
            platform in {"windows", "linux", "darwin"} and parsed.int != 0
            and str(parsed) == identifier
        )
    except (ValueError, AttributeError):
        return False


def kernel_boot_identity() -> str:
    """Return kernel-provided boot UUID or refuse unsupported/unavailable evidence."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class BootEnvironment(ctypes.Structure):
            _fields_ = [
                ("identifier", ctypes.c_ubyte * 16),
                ("firmware_type", wintypes.ULONG),
                ("boot_flags", ctypes.c_uint64),
            ]

        query = getattr(ctypes, "WinDLL")("ntdll").NtQuerySystemInformation
        query.argtypes = [
            wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        ]
        query.restype = wintypes.LONG
        data, returned = BootEnvironment(), wintypes.ULONG()
        status = query(90, ctypes.byref(data), ctypes.sizeof(data), ctypes.byref(returned))
        if status < 0 or returned.value < 16:
            raise RuntimeError("native Windows boot identity query unavailable")
        value = "windows:" + str(uuid.UUID(bytes_le=bytes(data.identifier)))
    elif sys.platform.startswith("linux"):
        value = "linux:" + Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii",
        ).strip().lower()
    elif sys.platform == "darwin":
        import ctypes

        query = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).sysctlbyname
        query.argtypes = [
            ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p, ctypes.c_size_t,
        ]
        query.restype = ctypes.c_int
        data = ctypes.create_string_buffer(37)
        size = ctypes.c_size_t(len(data))
        if query(b"kern.bootsessionuuid", data, ctypes.byref(size), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "native Darwin boot identity query unavailable")
        if size.value != len(data) or data.raw[-1:] != b"\0":
            raise RuntimeError("native Darwin boot identity is malformed")
        value = "darwin:" + data.raw[:-1].decode("ascii").lower()
    else:
        raise RuntimeError("native boot identity is unsupported on this platform")
    if not valid_boot_identity(value):
        raise RuntimeError("native boot identity is malformed")
    return value
