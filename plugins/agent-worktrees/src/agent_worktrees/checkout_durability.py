"""Data and namespace barriers before relocation retires its original copy."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace(source: Path, target: Path, *, existing: bool = True) -> None:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        move.restype = ctypes.c_int
        if not move(str(source), str(target), 0x8 | (0x1 if existing else 0)):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        if existing:
            os.replace(source, target)
        else:
            _publish_exclusive(source, target)
        directory(target.parent)


def _publish_exclusive(source: Path, target: Path) -> None:
    native = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(native, "renameat2"):
        rename = native.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (-100, os.fsencode(source), -100, os.fsencode(target), 1)
    elif sys.platform == "darwin" and hasattr(native, "renamex_np"):
        rename = native.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(target), 4)
    else:
        raise OSError("atomic no-replace checkout publication is unavailable")
    rename.restype = ctypes.c_int
    if rename(*args):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def files(paths) -> None:
    parents = set()
    for path in paths:
        path = Path(path)
        if path.is_symlink():
            raise ValueError("relocation durability input is redirected")
        with path.open("r+b" if os.name == "nt" else "rb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        parents.add(path.parent)
    for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
        directory(parent)


def tree(root: Path) -> None:
    files(path for path in root.rglob("*") if path.is_file())
    for path in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda value: len(value.parts), reverse=True,
    ):
        directory(path)
    directory(root)
