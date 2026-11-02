"""Fixed Linux guest mount observation; no writes or lifecycle actions."""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import time

MAX_TABLE_BYTES = 32 * 1024
MAX_SECONDS = 5


def read_mount_table() -> str:
    with open("/proc/self/mountinfo", "rb") as source:
        data = source.read(MAX_TABLE_BYTES + 1)
    if len(data) > MAX_TABLE_BYTES:
        raise ValueError("guest mount table exceeds its observation ceiling")
    return data.decode("utf-8", errors="strict")


def observe() -> dict[str, object]:
    started = time.monotonic()
    before = read_mount_table()
    roots: list[dict[str, object]] = []
    for path in ("/tmp", "/run"):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            filesystem = os.fstatvfs(descriptor)
            current = os.lstat(path)
            if not os.path.samestat(info, current):
                raise ValueError("guest volatile mount changed")
            roots.append({
                "path": path, "uid": info.st_uid, "gid": info.st_gid,
                "mode": stat.S_IMODE(info.st_mode),
                "device": f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}",
                "bytes": filesystem.f_blocks * filesystem.f_frsize,
                "readonly": bool(filesystem.f_flag & os.ST_RDONLY),
            })
        finally:
            os.close(descriptor)
    if before != read_mount_table() or time.monotonic() - started >= MAX_SECONDS:
        raise ValueError("guest mount observation changed or expired")
    return {"uid": os.geteuid(), "gid": os.getegid(), "mountinfo": before, "roots": roots}


def _expired(_signal: int, _frame: object) -> None:
    raise TimeoutError("guest mount observation expired")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        print(json.dumps(observe(), allow_nan=False))
    except (OSError, ValueError, TimeoutError):
        print("guest mount observation unavailable", file=sys.stderr)
        sys.exit(1)
