"""Conservative native-path classification for supported operational references."""

from pathlib import Path
import stat
import time


def _lexical(value: str | Path) -> Path:
    path = Path(value)
    if str(value).startswith(("\\\\", "//")):
        raise ValueError("network operational references require separate qualification")
    if not path.is_absolute():
        if ".." in path.parts:
            raise ValueError("relative parent-component references are unsupported")
    return path


def canonical(value: str | Path) -> Path:
    path = _lexical(value)
    return path.resolve() if path.is_absolute() else path


def under(value: str | Path, root: str | Path) -> bool:
    return canonical(value).is_relative_to(canonical(root))


class ReferenceSnapshot:
    """Reuse directory resolution only within one bounded, revalidated read."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.directories: dict[Path, tuple[Path, tuple | None]] = {}

    def _budget(self) -> None:
        if time.monotonic() > self.deadline:
            raise ValueError("operational reference inventory exceeds its budget")

    @staticmethod
    def _signature(path: Path) -> tuple | None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), getattr(info, "st_file_attributes", 0) & 0x400)

    def _directory(self, path: Path) -> Path:
        self._budget()
        if path not in self.directories:
            before = self._signature(path)
            resolved = canonical(path)
            if self._signature(path) != before:
                raise ValueError("operational reference directory changed during inspection")
            self.directories[path] = (resolved, before)
        return self.directories[path][0]

    def under(self, value: str, root: str) -> bool:
        path, root_path = _lexical(value), _lexical(root)
        if not root_path.is_absolute():
            raise ValueError("operational reference root must be absolute")
        resolved_root = self._directory(root_path)
        if not path.is_absolute():
            return path.is_relative_to(resolved_root)
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if path.name != ".." and (info is None or (
            stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400
        )):
            resolved = self._directory(path.parent) / path.name
        else:
            resolved = canonical(path)
        return resolved.is_relative_to(resolved_root)

    def verify(self) -> None:
        self._budget()
        for path, (resolved, signature) in self.directories.items():
            self._budget()
            if self._signature(path) != signature or canonical(path) != resolved:
                raise ValueError("operational reference directory changed during inspection")
            self._budget()
