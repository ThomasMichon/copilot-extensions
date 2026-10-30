"""Serialized, permission-preserving writes for the native configuration owner."""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml

_held = threading.local()


@contextmanager
def config_file_lock(config_path: Path):
    """Reentrant within one owning thread; the kernel releases crashed writers."""
    path = config_path.with_name(f".{config_path.name}.session-mode.lock")
    key = (os.getpid(), os.path.normcase(str(path.resolve())))
    held = getattr(_held, "paths", set())
    if key in held:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as stream:
        deadline = time.monotonic() + 5
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    stream.seek(1 << 30)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ValueError("configuration is busy; retry the update") from exc
                time.sleep(0.05)
        _held.paths = held | {key}
        try:
            yield
        finally:
            _held.paths = held
            if sys.platform == "win32":
                stream.seek(1 << 30)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_write_config(path: Path, data: dict, *, expected: bytes | None) -> None:
    """Caller holds config_file_lock across the complete read/modify/write."""
    permissions = stat.S_IMODE(path.stat().st_mode) if expected is not None else 0o600
    encoded = yaml.safe_dump(data, sort_keys=False, allow_unicode=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            os.chmod(staged, permissions)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if (path.read_bytes() if path.exists() else None) != expected:
            raise ValueError("configuration changed during update; retry from current policy")
        os.replace(staged, path)
        if yaml.safe_load(path.read_text(encoding="utf-8")) != data:
            raise ValueError("configuration readback failed")
    finally:
        staged.unlink(missing_ok=True)
