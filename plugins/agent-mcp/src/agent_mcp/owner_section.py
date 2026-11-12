"""Bounded Windows owner transfer; no capability advertisement or caller adoption."""

from __future__ import annotations

import json
import re
import secrets
import struct
from dataclasses import dataclass

from .custody_lifetime import SessionOwner
from .owner_section_native import READ, SECTION_BYTES, SectionNative

SCHEMA = "agent-mcp.windows-native-owner"
_PREFIX = b"AMO1"
_KEYS = {"schema", "version", "binding", "pid", "birth"}


def section_name(binding: str) -> str:
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


def decode(packet: bytes, binding: str) -> OwnerIdentity:
    """Refuse partial, oversized, noncanonical or differently bound records."""
    section_name(binding)
    if len(packet) != SECTION_BYTES or packet[:4] != _PREFIX:
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


class OwnerSection:
    """Prepare before creation, publish from the suspended child's actual handle.

    The existing creation owner supplies this read handle in its explicit handle
    list and calls publish before resuming the child. No process is created or
    resumed here. The private binding is not authentication against the same
    Windows principal and is never a durable ownership assertion.
    """

    def __init__(self) -> None:
        self.binding = secrets.token_hex(32)
        self._native = SectionNative()
        self._mapping = self._native.create(section_name(self.binding))
        self._readonly = None
        self._published = False
        try:
            self._readonly = self._native.readonly_duplicate(self._mapping)
        except BaseException:
            self.close()
            raise

    @property
    def inherited_handle(self) -> int:
        if self._readonly is None:
            raise ValueError("Native owner section is closed")
        return self._readonly

    def publish(self, process_handle: int, pid: int) -> OwnerIdentity:
        if self._mapping is None or self._published:
            raise ValueError("Native owner section is closed or already published")
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("Invalid native owner PID")
        native = self._native
        handle = native.duplicate_process(process_handle, pid)
        try:
            identity = native.identity(handle, pid)
            if identity.principal != native.current_sid():
                raise ValueError("Native owner principal differs")
            value = {
                "schema": SCHEMA, "version": 1, "binding": self.binding,
                "pid": pid, "birth": "windows:" + identity.start_time,
            }
            encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            packet = (_PREFIX + struct.pack("<I", len(encoded)) + encoded).ljust(
                SECTION_BYTES, b"\0",
            )
            result = decode(packet, self.binding)
            native.alive(handle)
            self._published = True
            native.write(self._mapping, packet)
            return result
        finally:
            native.close(handle)

    def close(self) -> None:
        try:
            if self._readonly is not None:
                self._native.close(self._readonly)
                self._readonly = None
        finally:
            if self._mapping is not None:
                self._native.close(self._mapping)
                self._mapping = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def acquire(binding: str) -> SessionOwner:
    """Open read-only and pin the recorded living owner before returning."""
    name = section_name(binding)
    native = SectionNative()
    mapping = native.require(native.k.OpenFileMappingW(READ, False, name))
    try:
        identity = decode(native.read(mapping), binding)
        handle = native.require(native.k.OpenProcess(0x1000 | 0x100000, False, identity.pid))
        try:
            actual = native.identity(handle, identity.pid)
            native.alive(handle)
            if (
                "windows:" + actual.start_time != identity.birth
                or actual.principal != native.current_sid()
            ):
                raise ValueError("Native owner section identity changed")
            owner = SessionOwner(identity.pid, identity.birth)
            try:
                owner.require_alive()
            except BaseException:
                owner.close()
                raise
            return owner
        finally:
            native.close(handle)
    finally:
        native.close(mapping)
