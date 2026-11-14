"""Bounded, non-executing source attribution for historical CPython bytecode."""
from __future__ import annotations

import struct

_OTHER = object()
_PENDING = object()
_NULL = object()
_UNHASHABLE = object()
_FORMATS = {
    b"o\r\r\n": 10,
    b"\xa7\r\r\n": 11,
    b"\xcb\r\r\n": 12,
    b"\xf3\r\r\n": 13,
}


class _Binary:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _Tuple:
    __slots__ = ("size", "strings", "hashable")

    def __init__(self, size, strings, hashable):
        self.size, self.strings, self.hashable = size, strings, hashable


class _Code:
    __slots__ = ("hashable",)

    def __init__(self, hashable):
        self.hashable = hashable


def _hashable(value):
    return value is not _UNHASHABLE and (
        not isinstance(value, (_Tuple, _Code)) or value.hashable
    )


def _strings(value):
    return isinstance(value, _Tuple) and value.strings


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

    def code(self, depth):
        numbers = [self.number() for _ in range(6 if self.old_code else 5)]
        if any(value < 0 for value in numbers) or numbers[1] > numbers[0]:
            raise ValueError("bytecode has invalid code counts")
        bytecode, constants, names = (self.value(depth + 1) for _ in range(3))
        if not isinstance(bytecode, _Binary) or len(bytecode.data) % 2:
            raise ValueError("bytecode has an invalid code bytes field")
        if not isinstance(constants, _Tuple) or not _strings(names):
            raise ValueError("bytecode has invalid constants or names")
        if self.old_code:
            variables, free, cells = (self.value(depth + 1) for _ in range(3))
            if not all(_strings(value) for value in (variables, free, cells)):
                raise ValueError("bytecode has invalid local/free/cell names")
            locals_count = variables.size
            if numbers[3] != locals_count:
                raise ValueError("bytecode local count differs")
        else:
            variables, kinds = self.value(depth + 1), self.value(depth + 1)
            if (
                not _strings(variables) or not isinstance(kinds, _Binary)
                or variables.size != len(kinds.data)
            ):
                raise ValueError("bytecode has invalid local names or kinds")
            locals_count = sum(bool(kind & 0x20) for kind in kinds.data)
        flags = numbers[-1]
        arguments = numbers[0] + numbers[2] + bool(flags & 4) + bool(flags & 8)
        if arguments > locals_count:
            raise ValueError("bytecode arguments exceed its locals")
        filename = self.value(depth + 1)
        if (
            not isinstance(filename, str) or not filename or len(filename) > 16384
            or "\0" in filename
        ):
            raise ValueError("bytecode has no supported source filename")
        self.filenames.add(filename)
        for _ in range(1 if self.old_code else 2):
            if not isinstance(self.value(depth + 1), str):
                raise ValueError("bytecode has an unsupported code name")
        if self.number() < 0:
            raise ValueError("bytecode has an invalid first line")
        for _ in range(1 if self.old_code else 2):
            if not isinstance(self.value(depth + 1), _Binary):
                raise ValueError("bytecode has an invalid line or exception field")
        return _Code(constants.hashable)

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
            digits = self.take(abs(self.number()) * 2)
            for index in range(1, len(digits), 2):
                if index % 8192 == 1:
                    self.check()
                if digits[index] & 0x80:
                    raise ValueError("bytecode has an invalid long digit")
        elif kind in "gy":
            self.take(8 if kind == "g" else 16)
        elif kind in "fx":
            for _ in range(1 if kind == "f" else 2):
                text = bytes(self.take(self.take(1)[0])).decode("ascii")
                if not text or text.strip() != text or "\0" in text or "_" in text:
                    raise ValueError("bytecode has an invalid float encoding")
                try:
                    float(text)
                except ValueError:
                    raise ValueError("bytecode has an invalid float encoding") from None
        elif kind in "sutaAzZ":
            size = self.take(1)[0] if kind in "zZ" else self.number()
            encoded = self.take(size)
            result = _Binary(encoded) if kind == "s" else bytes(encoded).decode(
                "ascii" if kind in "aAzZ" else "utf-8",
            )
        elif kind in "([<>)":
            count = self.take(1)[0] if kind == ")" else self.number()
            if not 0 <= count <= 65536 - self.objects:
                raise ValueError("bytecode exceeds its container budget")
            strings, hashable = True, True
            for _ in range(count):
                value = self.value(depth + 1)
                if value is _NULL:
                    raise ValueError("bytecode has a null container member")
                strings &= isinstance(value, str)
                hashable &= _hashable(value)
                if kind in "<>" and not _hashable(value):
                    raise ValueError("bytecode has an unhashable set member")
            if kind in "()":
                result = _Tuple(count, strings, hashable)
            elif kind in "[<":
                result = _UNHASHABLE
        elif kind == "{":
            while (key := self.value(depth + 1)) is not _NULL:
                if not _hashable(key):
                    raise ValueError("bytecode has an unhashable dictionary key")
                if self.value(depth + 1) is _NULL:
                    raise ValueError("bytecode has a null dictionary value")
            result = _UNHASHABLE
        elif kind == "c":
            result = self.code(depth)
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
        not isinstance(reader.value(), _Code) or reader.offset != len(data)
        or len(reader.filenames) != 1
    ):
        raise ValueError("bytecode source attribution is ambiguous")
    return next(iter(reader.filenames))
