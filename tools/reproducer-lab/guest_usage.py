"""Fixed non-root Linux guest probe; no cleanup, writes or resource creation."""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import time

ROOTS = ("/home/lab", "/lab/state", "/lab/workspace", "/lab/evidence")
MAX_ENTRIES = 100_000
MAX_OPEN_DIRECTORIES = 256
MAX_SECONDS = 5


def _expired(_signal: int, _frame: object) -> None:
    raise TimeoutError("guest storage measurement deadline exceeded")


def measure(roots: tuple[str, ...] = ROOTS) -> dict[str, object]:
    """Count conservative logical/allocated usage, without following symlinks."""
    started = time.monotonic()
    entries = 0
    usages: list[int] = []
    free: list[int] = []
    for root in roots:
        root_stat = os.lstat(root)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError("storage root is not a directory")
        seen: set[tuple[int, int]] = set()
        used = 0
        with _root_fd(root) as root_fd:
            opened_root = os.fstat(root_fd)
            if (opened_root.st_dev, opened_root.st_ino) != (root_stat.st_dev, root_stat.st_ino):
                raise ValueError("storage root changed during measurement")
            pending: list[tuple[int, int]] = [(os.dup(root_fd), 0)]
            try:
                while pending:
                    directory, depth = pending.pop()
                    try:
                        if depth > 128:
                            raise ValueError("storage traversal depth exceeded")
                        with os.scandir(directory) as listing:
                            for entry in listing:
                                entries += 1
                                if entries > MAX_ENTRIES or time.monotonic() - started >= MAX_SECONDS:
                                    raise TimeoutError("guest storage measurement bound exceeded")
                                info = entry.stat(follow_symlinks=False)
                                if info.st_dev != root_stat.st_dev:
                                    raise ValueError("nested filesystem is outside storage scope")
                                key = (info.st_dev, info.st_ino)
                                if key not in seen:
                                    seen.add(key)
                                    used += max(info.st_size, info.st_blocks * 512)
                                if stat.S_ISDIR(info.st_mode):
                                    if len(pending) >= MAX_OPEN_DIRECTORIES:
                                        raise ValueError("storage directory descriptor bound exceeded")
                                    child = os.open(
                                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory,
                                    )
                                    opened = os.fstat(child)
                                    if (opened.st_dev, opened.st_ino) != key:
                                        os.close(child)
                                        raise ValueError("directory changed during measurement")
                                    pending.append((child, depth + 1))
                    finally:
                        os.close(directory)
                filesystem = os.fstatvfs(root_fd)
                free.append(filesystem.f_bavail * filesystem.f_frsize)
                info = os.fstat(root_fd)
                usages.append(used + max(info.st_size, info.st_blocks * 512))
            finally:
                for directory, _depth in pending:
                    os.close(directory)
    return {"usage": usages, "free": free}


class _root_fd:
    def __init__(self, root: str) -> None:
        self.root = root
        self.fd = -1

    def __enter__(self) -> int:
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self.fd

    def __exit__(self, *_exception: object) -> None:
        os.close(self.fd)


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        print(json.dumps(measure(), allow_nan=False))
    except (OSError, ValueError, TimeoutError):
        # Do not publish arbitrary guest filenames or payloads to the host diagnostic.
        print("guest storage measurement unavailable", file=sys.stderr)
        sys.exit(1)
