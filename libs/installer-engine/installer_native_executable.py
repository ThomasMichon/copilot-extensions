"""Bounded native-ELF selection; executable trust remains the native owner's."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import struct
from typing import BinaryIO


def _read(stream: BinaryIO, offset: int, size: int, limit: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > limit:
        raise ValueError("protected UV has an invalid native executable layout")
    stream.seek(offset)
    content = stream.read(size)
    if len(content) != size:
        raise ValueError("protected UV native executable is incomplete")
    return content


def require_native_executable(
    path: Path,
    *,
    check_interpreter: Callable[[Path], None],
) -> None:
    """Reject script interpreters and check the native ELF loader, if declared."""
    limit = path.stat().st_size
    with path.open("rb") as stream:
        identity = _read(stream, 0, 16, limit)
        if (
            identity[:4] != b"\x7fELF"
            or identity[4] not in (1, 2)
            or identity[5] not in (1, 2)
            or identity[6] != 1
        ):
            raise ValueError("protected UV requires a native ELF executable")
        wide = identity[4] == 2
        order = "<" if identity[5] == 1 else ">"
        size = 64 if wide else 52
        header = _read(stream, 0, size, limit)
        kind, _machine, version = struct.unpack_from(order + "HHI", header, 16)
        offset = struct.unpack_from(
            order + ("Q" if wide else "I"), header, 32 if wide else 28
        )[0]
        declared, width, count = struct.unpack_from(
            order + "HHH", header, 52 if wide else 40
        )
        if (
            kind not in (2, 3)
            or version != 1
            or declared != size
            or width != (56 if wide else 32)
            or not 1 <= count <= 128
            or offset < size
        ):
            raise ValueError("protected UV has an invalid native executable layout")
        table = _read(stream, offset, width * count, limit)
        loader_seen = False
        for index in range(count):
            entry = table[index * width : (index + 1) * width]
            if struct.unpack_from(order + "I", entry)[0] != 3:
                continue
            location = struct.unpack_from(
                order + ("Q" if wide else "I"), entry, 8 if wide else 4
            )[0]
            length = struct.unpack_from(
                order + ("Q" if wide else "I"), entry, 32 if wide else 16
            )[0]
            if loader_seen or not 2 <= length <= 4096:
                raise ValueError("protected UV has an invalid native interpreter")
            loader_seen = True
            raw = _read(stream, location, length, limit)
            try:
                name = raw[:-1].decode("ascii")
            except UnicodeError as error:
                raise ValueError(
                    "protected UV has an invalid native interpreter"
                ) from error
            loader = Path(name)
            if (
                raw[-1:] != b"\0"
                or "\0" in name
                or not name.startswith("/")
                or loader == Path("/")
                or str(loader) != name
                or ".." in loader.parts
                or name.startswith("//")
                or any(char in name for char in "\r\n")
            ):
                raise ValueError("protected UV has an invalid native interpreter")
            check_interpreter(loader)
