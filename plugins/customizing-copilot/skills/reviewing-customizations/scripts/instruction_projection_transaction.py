"""Cooperative transaction; owned content is authoritative, not content CAS."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Callable


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        try:
            staged.unlink()
        except FileNotFoundError:
            pass


def create_exclusive(path: Path, content: bytes) -> None:
    """Publish complete bytes without replacing an existing directory entry."""
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(staged, path)
    finally:
        staged.unlink()


def current_regular_bytes(
    path: Path, is_reparse: Callable[[os.stat_result], bool]
) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or is_reparse(info)
    ):
        raise OSError(f"{path.as_posix()} is no longer a regular file")
    return path.read_bytes()


def write(
    changes: list[tuple[Path, bytes | None, bytes | None]],
    *,
    read: Callable[[Path], bytes | None],
    atomic_write: Callable[[Path, bytes], None],
    before_write: Callable[[], None] | None = None,
    validate_path: Callable[[Path], None] | None = None,
    after_write: Callable[[], None] | None = None,
) -> None:
    replaced: list[tuple[Path, bytes | None, bytes | None]] = []
    try:
        for path, content, expected_content in changes:
            if before_write is not None:
                before_write()
            if validate_path is not None:
                validate_path(path)
            if read(path) != expected_content:
                raise OSError(f"{path.as_posix()} changed after validation")
            if content is None:
                path.unlink()
            elif expected_content is None:
                create_exclusive(path, content)
            else:
                atomic_write(path, content)
            replaced.append((path, content, expected_content))
        if after_write is not None:
            after_write()
        for path, content, _ in changes:
            if validate_path is not None:
                validate_path(path)
            if read(path) != content:
                raise OSError("destination changed after transaction publication")
    except OSError as exc:
        rollback_errors: list[str] = []
        for path, written_content, previous_content in reversed(replaced):
            try:
                if validate_path is not None:
                    validate_path(path)
                if read(path) != written_content:
                    raise OSError("destination changed after the transaction write")
                if previous_content is None:
                    path.unlink()
                else:
                    atomic_write(path, previous_content)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.as_posix()}: {rollback_exc}")
        location = path.as_posix() if changes else "<empty transaction>"
        detail = f"transaction failed at {location}: {exc}"
        if rollback_errors:
            detail += "; rollback also failed: " + "; ".join(rollback_errors)
        raise OSError(detail) from exc
