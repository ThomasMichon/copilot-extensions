"""Bounded, non-executing source attribution for historical CPython bytecode."""
from __future__ import annotations

import struct

_OTHER = object()
_PENDING = object()
_NULL = object()
_CODE = object()
_FORMATS = {
    b"o\r\r\n": 10,
    b"\xa7\r\r\n": 11,
    b"\xcb\r\r\n": 12,
    b"\xf3\r\r\n": 13,
}


class _Reader:
    def __init__(self, data: bytes, check):
        self.data, self.check = memoryview(data), check
        self.offset = 16
        self.objects = 0
        self.references: list[object] = []
        self.filenames: set[str] = set()
        self.old_code = _FORMATS[data[:4]] == 10

    def take(self, size: int):
        self.check()
        if size < 0 or size > len(self.data) - self.offset:
            raise ValueError("bytecode has a truncated or oversized field")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def number(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def value(self, depth: int = 0):
        self.check()
        self.objects += 1
        if depth > 64 or self.objects > 65536:
            raise ValueError("bytecode exceeds its object budget")
        tag = self.take(1)[0]
        reference = None
        if tag & 0x80:
            if len(self.references) >= 65536:
                raise ValueError("bytecode exceeds its reference budget")
            reference = len(self.references)
            self.references.append(_PENDING)
        kind = chr(tag & 0x7f)
        result: object = _OTHER
        if kind == "r":
            index = self.number()
            if reference is not None or not 0 <= index < len(self.references):
                raise ValueError("bytecode has an invalid reference")
            result = self.references[index]
            if result is _PENDING:
                raise ValueError("bytecode has a cyclic or unfinished reference")
        elif kind == "0":
            if reference is not None:
                raise ValueError("bytecode has a referenced null")
            result = _NULL
        elif kind in "NFTS.":
            pass
        elif kind in "iI":
            self.take(4 if kind == "i" else 8)
        elif kind == "l":
            self.take(abs(self.number()) * 2)
        elif kind in "gy":
            self.take(8 if kind == "g" else 16)
        elif kind in "fx":
            for _ in range(1 if kind == "f" else 2):
                self.take(self.take(1)[0])
        elif kind in "sutaAzZ":
            size = self.take(1)[0] if kind in "zZ" else self.number()
            encoded = self.take(size)
            if kind != "s" and size <= 16384:
                result = bytes(encoded).decode("ascii" if kind in "aAzZ" else "utf-8")
        elif kind in "([<>)":
            count = self.take(1)[0] if kind == ")" else self.number()
            if not 0 <= count <= 65536 - self.objects:
                raise ValueError("bytecode exceeds its container budget")
            for _ in range(count):
                if self.value(depth + 1) is _NULL:
                    raise ValueError("bytecode has a null container member")
        elif kind == "{":
            while self.value(depth + 1) is not _NULL:
                if self.value(depth + 1) is _NULL:
                    raise ValueError("bytecode has a null dictionary value")
        elif kind == "c":
            self.take(24 if self.old_code else 20)
            # code, consts, names, then the version's local/free/cell fields.
            for _ in range(6 if self.old_code else 5):
                if self.value(depth + 1) is _NULL:
                    raise ValueError("bytecode has a null code field")
            filename = self.value(depth + 1)
            if not isinstance(filename, str) or not filename or "\0" in filename:
                raise ValueError("bytecode has no supported source filename")
            self.filenames.add(filename)
            for _ in range(1 if self.old_code else 2):
                if not isinstance(self.value(depth + 1), str):
                    raise ValueError("bytecode has an unsupported code name")
            self.take(4)
            for _ in range(1 if self.old_code else 2):
                if self.value(depth + 1) is _NULL:
                    raise ValueError("bytecode has a null line or exception field")
            result = _CODE
        else:
            raise ValueError("bytecode has an unsupported marshal tag")
        if reference is not None:
            self.references[reference] = result
        return result


def cache_tag(data: bytes) -> str:
    """Identify only the native-fixture-qualified CPython release formats."""
    version = _FORMATS.get(data[:4])
    if version is None:
        raise ValueError("bytecode magic is unsupported")
    return "cpython-3" + str(version)


def source(data: bytes, *, check=lambda: None) -> str:
    """Return one exact compiler filename, without loading a marshal object.

    Only the known CPython 3.10--3.13 release code layouts are admitted.
    Historical magic/layouts, oversized inputs and divergent nested code
    filenames remain unknown, not candidates for automatic deletion.
    """
    check()
    if (
        not 16 <= len(data) <= 8 * 1024 * 1024 or data[:4] not in _FORMATS
        or int.from_bytes(data[4:8], "little") not in (0, 1, 3)
    ):
        raise ValueError("bytecode magic, header or size is unsupported")
    reader = _Reader(data, check)
    if (
        reader.value() is not _CODE or reader.offset != len(data)
        or len(reader.filenames) != 1
    ):
        raise ValueError("bytecode source attribution is ambiguous")
    return next(iter(reader.filenames))
