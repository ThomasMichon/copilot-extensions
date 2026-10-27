"""Attached, finite-admission owner for the native install transaction inode."""

from __future__ import annotations

import math
import os
import stat
import sys
import time
from pathlib import Path


def _caller_closed() -> bool:
    """Observe pipe EOF while admission is busy, not only after acquiring."""
    if sys.stdin is None:
        return True
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetFileType.argtypes = (wintypes.HANDLE,)
        kernel.GetFileType.restype = wintypes.DWORD
        kernel.PeekNamedPipe.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        kernel.PeekNamedPipe.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        if kernel.GetFileType(handle) != 3:
            return False
        if kernel.PeekNamedPipe(handle, None, 0, None, None, None):
            return False
        error = ctypes.get_last_error()
        if error in (109, 232):
            return True
        raise ctypes.WinError(error)
    import select

    return bool(select.select([sys.stdin], [], [], 0)[0]) and not sys.stdin.buffer.read(1)


def own(root: Path, timeout: float) -> None:
    """Announce OS custody, retaining it until the caller closes the input pipe."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError("Install transaction admission requires a finite positive timeout")
    if not root.is_absolute() or ".." in root.parts:
        raise RuntimeError("Install transaction requires an absolute ordinary root")
    if root == Path(root.anchor) or Path.home().is_relative_to(root):
        raise RuntimeError("Install transaction refuses a filesystem root or user-home ancestor")
    for path in (root, *root.parents):
        if os.path.lexists(path):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("Install transaction refuses redirected ancestry")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and (root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o022):
        raise RuntimeError("Install transaction requires exclusive operator custody")
    identity = (root.stat().st_dev, root.stat().st_ino)
    lock = root / ".install.lock"
    deadline = time.monotonic() + timeout
    while True:
        if _caller_closed():
            return
        descriptor = None
        try:
            if os.path.lexists(lock):
                info = lock.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    raise RuntimeError("Install transaction refuses linked lock custody")
            descriptor = os.open(
                lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            opened = os.fstat(descriptor)
            current = lock.stat()
            if (opened.st_dev, opened.st_ino) != (
                current.st_dev,
                current.st_ino,
            ) or opened.st_nlink != 1:
                raise RuntimeError("Install transaction inode changed during admission")
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (root.stat().st_dev, root.stat().st_ino) != identity:
                raise RuntimeError("Install root identity changed during admission")
            break
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Install transaction is busy; refusing to proceed unlocked"
                ) from None
            time.sleep(0.05)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
    try:
        sys.stdout.buffer.write(b"locked\n")
        sys.stdout.buffer.flush()
        while sys.stdin.buffer.read(1):
            pass
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        own(Path(sys.argv[1]), float(sys.argv[2]))
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
