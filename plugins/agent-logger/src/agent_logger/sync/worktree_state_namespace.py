"""Reserve typed collections without hiding an existing ordinary machine root."""

from __future__ import annotations

import os
from pathlib import Path

from agent_logger.sync.publication_directory import PublicationDirectory, publication_directory

DIRECTORY = "worktree-state"
_MARKER = ".collection-namespace.json"
_CONTENT = b'{"schema":"agent-logger.worktree-state-namespace","version":1}\n'


def _matches(directory: PublicationDirectory) -> bool:
    try:
        with directory.open(_MARKER, protected=True) as stream:
            return os.fstat(stream.fileno()).st_nlink == 1 and stream.read(
                len(_CONTENT) + 1,
            ) == _CONTENT
    except FileNotFoundError:
        return False


def has_namespace(root: Path) -> bool:
    """A marker is a format discriminator, never collection or deletion authority."""
    try:
        with publication_directory(root / DIRECTORY, create=False) as directory:
            return _matches(directory)
    except FileNotFoundError:
        return False


def claim_namespace(root: Path) -> None:
    with publication_directory(root / DIRECTORY) as directory:
        if _matches(directory):
            return
        if directory.names():
            raise ValueError("typed collection namespace contains unowned or partial evidence")
        if directory.fd is None:
            raise OSError("typed collection namespace requires native directory barriers")
        try:
            with directory.create(_MARKER, mode=0o600) as stream:
                stream.write(_CONTENT)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory.fd)
        except FileExistsError:
            if not _matches(directory):
                raise ValueError("concurrent typed collection namespace is incomplete")
