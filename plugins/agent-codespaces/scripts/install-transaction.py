"""Attached, finite-admission owner for the native install transaction inode."""
from __future__ import annotations

import os
import math
import stat
import sys
import time
from pathlib import Path


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
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt" and (root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o022):
        raise RuntimeError("Install transaction requires exclusive operator custody")
    identity = (root.stat().st_dev, root.stat().st_ino)
    lock = root / ".install.lock"
    deadline = time.monotonic() + timeout
    while True:
        descriptor = None
        try:
            if os.path.lexists(lock):
                info = lock.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise RuntimeError("Install transaction refuses linked lock custody")
            descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            opened = os.fstat(descriptor)
            current = lock.stat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or opened.st_nlink != 1:
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
                raise RuntimeError("Install transaction is busy; refusing to proceed unlocked") from None
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
        raise SystemExit(1)
