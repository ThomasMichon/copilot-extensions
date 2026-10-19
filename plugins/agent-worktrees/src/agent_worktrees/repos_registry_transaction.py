"""Compare-before-replace boundary shared by all repository registry writers."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from .repos_registry_reader import RepositoryResolutionError

if TYPE_CHECKING:
    from .repos import ReposRegistry


class RegistryConflictError(RepositoryResolutionError):
    """A registry changed after it was read; the operation must be restarted."""


def revision(content: bytes | None) -> str:
    return hashlib.sha256(content).hexdigest() if content is not None else "absent"


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@contextmanager
def registry_lock(path: Path):
    from . import pr_authority
    from .tracking import _RecordLock

    with pr_authority.guard(), _RecordLock(path, timeout=10.0, require_sidecar=True):
        yield


def write_checked(path: Path, registry: ReposRegistry, content: str) -> None:
    from .tracking import _atomic_write

    with registry_lock(path):
        current = read_bytes(path)
        baseline = registry._baseline
        expected = (str(path.resolve()), revision(current))
        if baseline is None:
            if current is not None:
                raise RegistryConflictError(
                    "repository registry must be read before replacing an existing file"
                )
        elif baseline != expected:
            raise RegistryConflictError(
                "repository registry changed; read again before retrying the operation"
            )
        _atomic_write(path, content)
        registry._baseline = (str(path.resolve()), revision(content.encode("utf-8")))
