"""Bounded Windows owner transfer; no capability advertisement or caller adoption."""

from __future__ import annotations

import json
import secrets
import struct

from .owner_section_native import SECTION_BYTES, SectionNative
from .owner_snapshot import SCHEMA as SCHEMA
from .owner_snapshot import OwnerIdentity as OwnerIdentity
from .owner_snapshot import WindowsOrigin
from .owner_snapshot import decode as decode
from .owner_snapshot import section_name as section_name

_PREFIX = b"AMO1"


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


def acquire(binding: str) -> WindowsOrigin:
    """Open read-only and pin the recorded living owner before returning."""
    return WindowsOrigin(binding)
