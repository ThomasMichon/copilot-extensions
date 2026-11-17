"""Partition independently loaded physical programs without dropping source custody."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


class ProgramError(ValueError):
    """A declared program cannot be checked faithfully."""


class IdentityReader(Protocol):
    def __call__(self, path: Path, *, directory: bool = False) -> tuple[int, int]: ...


@dataclass(frozen=True)
class Program:
    root: str
    import_roots: tuple[str, ...] = ()


def independent_programs(
    files: tuple[str, ...], all_paths: tuple[str, ...], roots: tuple[str, ...],
    owns: Callable[[str], bool],
) -> tuple[tuple[str, ...], ...]:
    """Assign every selected physical file to exactly one unchanged owner program."""
    for index, root in enumerate(roots):
        if any(root == other or root.startswith(other + "/") or other.startswith(root + "/")
               for other in roots[:index]):
            raise ProgramError("Independent program roots must not overlap")
        if not any(path.startswith(root + "/") and owns(path) for path in all_paths):
            raise ProgramError(f"Independent program root has no owned Python source: {root}")
    if len(files) != len(set(files)) or not set(files) <= set(all_paths) or any(
        not owns(path) for path in files
    ):
        raise ProgramError("Independent programs require unique inventoried owner files")
    assigned: list[list[str]] = [[] for _ in range(len(roots) + 1)]
    for path in files:
        group = next(
            (index for index, root in enumerate(roots, 1) if path.startswith(root + "/")), 0,
        )
        assigned[group].append(path)
    return tuple(tuple(group) for group in assigned if group)


@dataclass(frozen=True)
class ProgramInputs:
    root: Path
    paths: tuple[str, ...]
    identity: IdentityReader
    directories: tuple[tuple[Path, tuple[int, int]], ...]
    files: tuple[tuple[Path, tuple[int, int], bytes], ...]

    @classmethod
    def capture(
        cls, root: Path, paths: tuple[str, ...], identity: IdentityReader,
    ) -> ProgramInputs:
        directories: dict[Path, tuple[int, int]] = {}
        files: list[tuple[Path, tuple[int, int], bytes]] = []
        for relative in paths:
            path = root / relative
            parent = Path(path.anchor)
            directories[parent] = identity(parent, directory=True)
            for part in path.parts[1:-1]:
                parent /= part
                current = identity(parent, directory=True)
                if parent in directories and directories[parent] != current:
                    raise ProgramError("Independent program directory changed during capture")
                directories[parent] = current
            before = identity(path)
            content = path.read_bytes()
            if identity(path) != before:
                raise ProgramError("Independent program file changed during capture")
            files.append((path, before, content))
        captured = cls(root, paths, identity, tuple(directories.items()), tuple(files))
        captured.verify()
        return captured

    def verify(self) -> None:
        for path, expected in self.directories:
            if self.identity(path, directory=True) != expected:
                raise ProgramError("Independent program directory identity changed")
        for path, expected, content in self.files:
            if (self.identity(path) != expected or path.read_bytes() != content
                    or self.identity(path) != expected):
                raise ProgramError("Independent program source changed during type checking")
