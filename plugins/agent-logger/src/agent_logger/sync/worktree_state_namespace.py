"""Reserve typed collections without hiding an existing ordinary machine root."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from agent_logger.sync.lock import sync_lock
from agent_logger.sync.provenance import existing_real_directory, is_link_or_reparse
from agent_logger.sync.publication_directory import PublicationDirectory, publication_directory

DIRECTORY = "worktree-state"
_MARKER = ".collection-namespace.json"
_CONTENT = b'{"schema":"agent-logger.worktree-state-namespace","version":1}\n'
_GUARD = ".worktree-state-domain.lock"
_MAX_PREFIXES = 32


class NamespaceError(OSError):
    """A reserved domain marker exists but cannot qualify the directory."""


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
    return is_namespace_directory(root, DIRECTORY)


def is_namespace_directory(root: Path, name: str) -> bool:
    try:
        with publication_directory(root / name, create=False) as directory:
            try:
                directory.stat(_MARKER)
            except FileNotFoundError:
                return False
            if not _matches(directory):
                raise NamespaceError("typed-state namespace marker is invalid or unsupported")
            return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise NamespaceError("typed-state namespace marker cannot be qualified") from error


def _marked(root: Path, name: str) -> bool:
    try:
        path = root / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if is_link_or_reparse(path, info.st_mode):
                raise OSError("destination directory is unsafe: namespace ancestor is indirect")
            if not stat.S_ISDIR(info.st_mode):
                return False
        with publication_directory(path, create=False) as directory:
            try:
                directory.stat(_MARKER)
            except FileNotFoundError:
                return False
            return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise OSError(f"destination directory is unsafe: {root / name}: {error}") from error


def _prefixes(destination: Path, *, markers_only: bool = False) -> list[tuple[Path, str]]:
    if ".." in destination.parts:
        raise OSError("unsafe destination path: parent traversal is not supported")
    current = Path(destination.anchor)
    prefixes: dict[Path, tuple[Path, str]] = {}
    marked: set[Path] = set()
    for name in destination.parts[1:]:
        if name.casefold() == DIRECTORY.casefold():
            prefixes[current / name] = current, name
        current /= name
    if len(prefixes) > _MAX_PREFIXES:
        raise OSError("publication path has too many namespace prefixes")

    def inspect(directory: PublicationDirectory) -> None:
        try:
            directory.stat(_MARKER)
        except FileNotFoundError:
            return
        path = directory.path
        prefixes[path] = path.parent, path.name
        marked.add(path)
        if len(prefixes) > _MAX_PREFIXES:
            raise OSError("publication path has too many namespace prefixes")

    try:
        probe = destination
        try:
            info = destination.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISREG(info.st_mode) and not is_link_or_reparse(destination, info.st_mode):
                probe = destination.parent
        with publication_directory(probe, create=False, inspect=inspect):
            pass
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        raise OSError(f"destination directory is unsafe: {destination}: {error}") from error
    return [
        prefixes[path] for path in sorted(prefixes, key=lambda path: len(path.parts))
        if not markers_only or path in marked
    ]


def _destination(root: Path, machine: str) -> Path:
    relative = Path(machine)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise OSError("unsafe destination path: machine path must be relative without parent traversal")
    return root.expanduser().absolute() / relative


def namespace_overlap(root: Path, machine: str) -> bool:
    return bool(_prefixes(_destination(root, machine), markers_only=True))


def _existing_ancestor(root: Path) -> Path:
    for path in (root, *root.parents):
        if existing_real_directory(path) is not None:
            return path
    raise OSError("publication root has no real existing ancestor")


@contextmanager
def _domain_lock(root: Path) -> Iterator[None]:
    with sync_lock(root / _GUARD, timeout=10) as acquired:
        if not acquired:
            raise OSError("typed collection domain reservation is busy")
        yield


@contextmanager
def _reservation(
    destination: Path, *, root: Path, claiming: bool = False,
) -> Iterator[bool]:
    prefixes = _prefixes(destination)
    stable = _existing_ancestor(root) if prefixes else None
    with ExitStack() as stack:
        for index, (parent, name) in enumerate(prefixes):
            final_claim = claiming and index == len(prefixes) - 1
            if not final_claim and _marked(parent, name):
                yield False
                return
            # A real descendant makes an unmarked ancestor nonempty. These
            # publication legs never remove that structural ancestor.
            if not final_claim and stable is not None and parent / name in stable.parents:
                continue
            stack.enter_context(publication_directory(parent / name))
            stack.enter_context(_domain_lock(parent / name))
            if not final_claim and _marked(parent, name):
                yield False
                return
        yield True


@contextmanager
def ordinary_publication(root: Path, machine: str) -> Iterator[Path | None]:
    root = root.expanduser().absolute()
    with _reservation(_destination(root, machine), root=root) as allowed:
        yield root if allowed else None


def _claim_namespace(root: Path) -> None:
    with publication_directory(root / DIRECTORY) as directory:
        if _matches(directory):
            return
        if set(directory.names()) - {_GUARD}:
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


def claim_namespace(root: Path) -> None:
    root = root.expanduser().absolute()
    with _reservation(root / DIRECTORY, root=root, claiming=True) as allowed:
        if not allowed:
            raise ValueError("collection root overlaps an owned or partial typed-state namespace")
        _claim_namespace(root)
