"""Canonical physical-resource state and Linux process exclusion, not deployment authority."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys
from typing import Any, Iterator

from .komodo_profile import ordinary_path
from .planning import PlanError, canonical, decode, digest
from .private_state import private_directory, read_private_file


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def _complete_pending(path: Path, content: bytes) -> None:
    ordinary_path(path.parent)
    if path.exists():
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    else:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1
                or not 0 <= info.st_size <= len(content)
                or _identity(path) != (info.st_dev, info.st_ino)):
            raise PlanError("Komodo pending publication has foreign custody")
        previous = bytearray()
        while len(previous) < info.st_size:
            piece = os.read(descriptor, min(65536, info.st_size - len(previous)))
            if not piece:
                raise PlanError("Komodo pending publication changed during recovery")
            previous.extend(piece)
        after = os.fstat(descriptor)
        if ((after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                != (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                or not content.startswith(previous)):
            raise PlanError("Komodo pending publication conflicts with selected content")
        offset = len(previous)
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise PlanError("Komodo pending publication did not complete")
            offset += written
        os.fsync(descriptor)
        if _identity(path) != (info.st_dev, info.st_ino):
            raise PlanError("Komodo pending publication custody changed")
    finally:
        os.close(descriptor)


def _immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if read_private_file(path, 1024 * 1024) != content:
            raise PlanError("Komodo retained immutable evidence changed")
        return
    pending = path.with_name(path.name + ".pending")
    _complete_pending(pending, content)
    identity = _identity(pending)
    os.link(pending, path, follow_symlinks=False)
    if _identity(path) != identity or _identity(pending) != identity:
        raise PlanError("Komodo immutable publication custody changed")
    if read_private_file(path, 1024 * 1024) != content:
        raise PlanError("Komodo immutable publication content changed")
    os.unlink(pending)
    _flush(path.parent)


def _flush(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class KomodoState:
    """One host-owner root per physical daemon/project; aliases share this lock."""

    def __init__(self, root: Path, descriptor: int) -> None:
        self.root, self.descriptor = root, descriptor
        self.identity = _identity(root)
        info = os.fstat(descriptor)
        self.lock_identity = info.st_dev, info.st_ino

    @classmethod
    @contextmanager
    def open(cls, host_runtime: Path, *, daemon: str, project: str) -> Iterator[KomodoState]:
        if sys.platform != "linux":
            raise PlanError("Komodo substrate owner requires an admitted Linux host")
        import fcntl

        ordinary_path(host_runtime)
        private_directory(host_runtime)
        root = host_runtime / digest({"daemon": daemon, "project": project})
        private_directory(root)
        lock = root / "owner.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1
                    or _identity(lock) != (info.st_dev, info.st_ino)):
                raise PlanError("Komodo physical resource lock has foreign custody")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PlanError("Komodo physical resource owner is busy") from None
            owner = cls(root, descriptor)
            owner.check()
            yield owner
            owner.check()
        finally:
            os.close(descriptor)

    def check(self) -> None:
        ordinary_path(self.root)
        private_directory(self.root)
        if (_identity(self.root) != self.identity
                or _identity(self.root / "owner.lock") != self.lock_identity):
            raise PlanError("Komodo physical resource custody changed")

    def read(self) -> dict[str, Any] | None:
        self.check()
        path = self.root / "selection.json"
        if not path.exists():
            return None
        value = decode(read_private_file(path, 1024 * 1024))
        if not isinstance(value, dict):
            raise PlanError("Komodo retained selection is invalid")
        return value

    def save(self, value: dict[str, Any]) -> None:
        self.check()
        content = canonical(value)
        pending = self.root / "selection.pending"
        _complete_pending(pending, content)
        current = self.root / "selection.json"
        if current.exists():
            read_private_file(current, 1024 * 1024)
        self.check()
        os.replace(pending, current)
        _flush(self.root)

    def profile(self, content: bytes) -> Path:
        self.check()
        slots = self.root / "deployments"
        private_directory(slots)
        slot = slots / digest({"profile": content.decode("utf-8")})
        private_directory(slot)
        path = slot / "compose.json"
        _immutable(path, content)
        return path

    def retain(self, value: dict[str, Any]) -> str:
        self.check()
        identifier = digest(value)
        directory = self.root / "receipts"
        private_directory(directory)
        path = directory / (identifier + ".json")
        content = canonical(value)
        _immutable(path, content)
        return identifier
