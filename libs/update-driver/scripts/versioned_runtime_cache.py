"""Runtime-owned cache maintenance, independent of successful selection."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys


def _macos_archive_acl(path):
    import ctypes
    import errno

    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    library.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    library.acl_get_file.restype = ctypes.c_void_p
    library.acl_get_entry.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
    ]
    library.acl_get_entry.restype = ctypes.c_int
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int
    acl = library.acl_get_file(os.fsencode(path), 0x100)
    if not acl:
        raise PermissionError("cache maintenance archive ACL is unavailable")
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        if library.acl_get_entry(acl, 0, ctypes.byref(entry)) != -1 or ctypes.get_errno() != errno.EINVAL:
            raise PermissionError("cache maintenance archive ACL is unsupported")
    finally:
        library.acl_free(acl)


def _protected_archive_path(archive):
    if os.name == "nt":
        return archive
    # Import reopens the filename. A protected leaf alone does not prevent
    # another principal replacing it through a writable parent.
    archive = archive.resolve(strict=True)
    descriptor = os.open(archive.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent = Path(archive.anchor)
        for name in archive.parts[1:-1]:
            info = os.fstat(descriptor)
            if info.st_uid not in (0, os.geteuid()) or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise PermissionError("cache maintenance archive ancestry is not protected")
            if sys.platform == "darwin":
                _macos_archive_acl(parent)
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
            parent /= name
        info = os.fstat(descriptor)
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise PermissionError("cache maintenance archive ancestry is not protected")
        if sys.platform == "darwin":
            _macos_archive_acl(parent)
    finally:
        os.close(descriptor)
    return archive


def _collector(*, windows_owned_file=None):
    archive = Path(__file__).with_name("daemon_instance_runtime.pyz")
    try:
        info = archive.lstat()
    except FileNotFoundError:
        from agent_procutil import python_cache_collect
    else:
        archive = _protected_archive_path(archive)
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("cache maintenance source archive is redirected")
        descriptor = os.open(
            archive, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev, opened.st_ino,
            ) != (info.st_dev, info.st_ino):
                raise ValueError("cache maintenance source archive changed")
            if os.name == "nt":
                if windows_owned_file is None or not windows_owned_file(descriptor):
                    raise PermissionError("cache maintenance source archive is not protected")
            elif opened.st_uid not in (0, os.geteuid()) or opened.st_mode & 0o022:
                raise PermissionError("cache maintenance source archive is not protected")
            elif sys.platform == "darwin":
                _macos_archive_acl(archive)
            # Existing pre-install lifecycle distribution, not a retired runtime.
            sys.path.insert(0, str(archive))
            try:
                from agent_procutil import python_cache_collect
            finally:
                sys.path.pop(0)
        finally:
            os.close(descriptor)
    return python_cache_collect


def maintain(root, *, cleanup_lease, protected, version_dir, windows_owned_file=None):
    @contextmanager
    def retirement(slot, *, check):
        check()
        with cleanup_lease(root, slot) as admitted:
            check()
            if not admitted:
                yield "runtime-busy"
            elif slot.lower() in protected(check=check):
                yield "runtime-protected"
            else:
                try:
                    version_dir(root, slot).lstat()
                except FileNotFoundError:
                    yield ""
                else:
                    yield "runtime-present"

    try:
        result = _collector(windows_owned_file=windows_owned_file).collect(        root, retirement=retirement)
        if result.namespaces or result.reasons or result.pending:
            print("gc: python-cache " + json.dumps(asdict(result), sort_keys=True), file=sys.stderr)
    except Exception as error:
        # Cache cleanup never vetoes ready incoming content or completed GC.
        # Do not expose private paths from an arbitrary filesystem exception.
        print(f"gc: python-cache maintenance unavailable ({type(error).__name__})", file=sys.stderr)


def gc(root, *, candidates, cleanup_lease, protected, version_dir, is_link,
       slot_age_days, remove_slot, min_age_days, windows_owned_file=None):
    removed = []
    for version in candidates(root):
        with cleanup_lease(root, version) as admitted:
            if not admitted or version.lower() in protected():
                continue
            directory = version_dir(root, version)
            if min_age_days > 0 and not is_link(directory) and slot_age_days(root, version) < min_age_days:
                continue
            if remove_slot(directory, label="gc"):
                removed.append(version)
    maintain(
        root, cleanup_lease=cleanup_lease, protected=protected, version_dir=version_dir,
        windows_owned_file=windows_owned_file,
    )
    return removed
