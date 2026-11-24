"""Serialized canonical KillSwitch mutations, shared by Bash and the Python CLI."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from typing import BinaryIO

_THREAD_LOCK = threading.Lock()

if sys.platform == "win32":
    import msvcrt

    def _lock_byte(stream: BinaryIO) -> None:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_byte(stream: BinaryIO) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_byte(stream: BinaryIO) -> None:
        fcntl.lockf(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)

    def _unlock_byte(stream: BinaryIO) -> None:
        fcntl.lockf(stream.fileno(), fcntl.LOCK_UN, 1, 0)


def state_revision(path: Path) -> str:
    """Match the controller's path-bound revision without interpreting audit data."""
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        marker = b"absent"
    else:
        marker = b"present\0" + content
    return hashlib.sha256(os.fsencode(path.absolute()) + b"\0" + marker).hexdigest()


@contextmanager
def mutation_lock(path: Path, *, timeout: float = 5.0) -> Iterator[None]:
    """Lock byte zero of a retained sibling, interoperable with FileStream.Lock."""
    deadline = time.monotonic() + timeout
    if not _THREAD_LOCK.acquire(timeout=timeout):
        raise TimeoutError("Automatic-update switch writer lock unavailable")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_name(path.name + ".lock")
        if path.is_symlink() or lock.is_symlink():
            raise OSError("Automatic-update switch or writer lock is linked")
        with lock.open("a+b") as stream:
            while True:
                try:
                    _lock_byte(stream)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Automatic-update switch writer lock unavailable") from error
                    time.sleep(min(0.025, remaining))
            try:
                yield
            finally:
                _unlock_byte(stream)
    finally:
        _THREAD_LOCK.release()


def mutate(
    path: Path, *, state: Mapping[str, object] | None, expected: str = "",
) -> bool:
    """Publish or clear one switch only after checking confirmation inside its lock."""
    if expected and re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise ValueError("A current automatic-update switch revision is required")
    path = path.expanduser()
    path = path.parent.resolve() / path.name
    with mutation_lock(path):
        if expected and state_revision(path) != expected:
            raise ValueError("Automatic-update switch changed; refresh before applying")
        if state is None:
            existed = path.exists()
            path.unlink(missing_ok=True)
            return existed
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=path.name + ".tmp.", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(state, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True


def main(argv: list[str]) -> int:
    """Private Bash adapter; user-facing syntax remains in the canonical guard."""
    action, selected, legacy, expected, who, reason = argv
    state = None if action == "off" else {
        "active": True, "reason": reason, "set_by": who,
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    mutate(Path(selected), state=state, expected=expected)
    if action == "off" and legacy:
        mutate(Path(legacy), state=None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError) as error:
        print(f"[bootstrap-killswitch] error: {error}", file=sys.stderr)
        raise SystemExit(1)
