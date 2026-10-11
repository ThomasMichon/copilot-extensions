"""Compare-before-replace transaction with injected filesystem safety checks."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable


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
            else:
                atomic_write(path, content)
            replaced.append((path, content, expected_content))
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
        detail = f"transaction failed at {path.as_posix()}: {exc}"
        if rollback_errors:
            detail += "; rollback also failed: " + "; ".join(rollback_errors)
        raise OSError(detail) from exc
