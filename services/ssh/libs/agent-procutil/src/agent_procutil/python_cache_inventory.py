"""Bounded no-follow classification and removal of one legacy bytecode tree."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import re
import stat
from typing import Any

from . import python_cache_files as files
from . import python_cache_permissions as permissions
from . import python_cache_pyc as pyc


@dataclass
class Tree:
    directories: dict[tuple[str, ...], tuple[tuple[int, int], tuple[str, ...]]] = field(default_factory=dict)
    leaves: dict[tuple[str, ...], tuple[tuple[int, int], tuple[int, int, int]]] = field(default_factory=dict)
    bytes: int = 0
    newest: float = 0
    content_newest: float = 0


def _checked(entry: files.Entry) -> None:
    entry.check_budget()
    if stat.S_ISDIR(entry.info.st_mode):
        permissions.check(entry.path, private=False)
    else:
        permissions.check_file(entry.path, private=False)
    entry.verify()


def birth(entry: files.Entry) -> list[int | str]:
    """Use native creation identity, not a mutable directory change time."""
    _checked(entry)
    if entry.windows is None:
        value = getattr(entry.info, "st_birthtime", None)
        if value is None:
            raise ValueError("namespace creation identity unavailable")
        return [*files.identity(entry.info), str(value)]
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("created", ctypes.c_longlong), ("accessed", ctypes.c_longlong),
            ("written", ctypes.c_longlong), ("changed", ctypes.c_longlong),
            ("attributes", wintypes.DWORD),
        ]

    kernel, handle = entry.windows
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    value = Basic()
    if not kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(value), ctypes.sizeof(value)):
        native: Any = ctypes
        raise native.WinError(native.get_last_error())
    if value.created <= 0:
        raise ValueError("namespace creation identity unavailable")
    return [*files.identity(entry.info), str(value.created)]


def inspect(namespace: files.Entry, work: Any) -> Tree:
    result = Tree()

    def walk(directory: files.Entry, relative: tuple[str, ...]) -> None:
        _checked(directory)
        before = directory.verify()
        names = []
        with directory.names() as iterator:
            while True:
                work.entry()
                item = next(iterator, None)
                if item is None:
                    break
                names.append(item.name)
        result.directories[relative] = (files.identity(before), tuple(sorted(names)))
        result.newest = max(result.newest, before.st_mtime)
        for name in sorted(names):
            work.entry()
            with files.child(directory, name, read=True) as entry:
                _checked(entry)
                child = (*relative, name)
                result.newest = max(result.newest, entry.info.st_mtime)
                if stat.S_ISDIR(entry.info.st_mode):
                    if len(child) > 64:
                        raise ValueError("legacy bytecode tree too deep")
                    walk(entry, child)
                    continue
                if entry.info.st_nlink != 1 or not re.fullmatch(r".+\.pyc(?:\.[0-9]+)?", name):
                    raise ValueError("non-bytecode-content")
                if not re.search(r"\.pyc\.[0-9]+$", name):
                    header = entry.read(16, prefix=True)
                    if len(header) != 16 or int.from_bytes(header[4:8], "little") not in (0, 1, 3):
                        raise ValueError("unsupported-bytecode-header")
                    tag = pyc.cache_tag(header)
                    if not re.search(r"\." + re.escape(tag) + r"(?:\.opt-[0-9]+)?\.pyc$", name):
                        raise ValueError("unsupported-bytecode-name")
                current = entry.verify()
                result.content_newest = max(result.content_newest, current.st_mtime)
                stamp = (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                if stamp != (entry.info.st_size, entry.info.st_mtime_ns, entry.info.st_ctime_ns):
                    raise ValueError("bytecode-changed-during-inspection")
                result.leaves[child] = (files.identity(current), stamp)
                result.bytes += current.st_size
        after = directory.verify()
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("namespace-changed-during-inspection")

    walk(namespace, ())
    return result


def remove(namespace: files.Entry, tree: Tree, work: Any) -> None:
    def walk(directory: files.Entry, relative: tuple[str, ...]) -> None:
        expected, names = tree.directories[relative]
        if files.identity(directory.verify()) != expected:
            raise ValueError("namespace-changed-during-removal")
        for name in names:
            work.entry()
            child = (*relative, name)
            record = tree.directories.get(child)
            identity = record[0] if record else tree.leaves[child][0]
            with files.child(directory, name, expected=identity, delete=True) as entry:
                _checked(entry)
                if record:
                    walk(entry, child)
                else:
                    current = entry.verify()
                    stamp = (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                    if stamp != tree.leaves[child][1]:
                        raise ValueError("bytecode-changed-during-removal")
                    entry.remove()
                    work.report.files += 1
                    work.report.bytes += current.st_size
        work.entry()
        directory.remove()

    walk(namespace, ())
