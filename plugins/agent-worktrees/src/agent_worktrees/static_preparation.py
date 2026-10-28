"""Copy notifications, destination ownership and tracked-file race protection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import stat
import uuid


@dataclass(frozen=True)
class Publication:
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    generation: str = ""
    reused: bool = False
    failures: tuple[str, ...] = ()
    publication_revision: str = ""


def _check(deadline: float | None) -> None:
    if deadline is not None:
        from .static_instructions import _remaining

        _remaining(deadline)


def stamp(path: Path, *, deadline: float | None = None) -> list | None:
    _check(deadline)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    value = [
        str(path.resolve()), info.st_mode, info.st_dev, info.st_ino,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    ]
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        target = path.stat()
        followed = [target.st_mode, target.st_dev, target.st_ino]
        if not stat.S_ISDIR(target.st_mode):
            followed.extend((target.st_size, target.st_mtime_ns, target.st_ctime_ns))
        value.append(followed)
    _check(deadline)
    return value


def ownership_history(root: Path, home: Path, *, deadline: float | None = None) -> dict:
    """Source names protect unresolved providers during partial discovery."""
    try:
        _check(deadline)
        path = _directory(root) / "owners.json"
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        owners = data.get("owners")
        _check(deadline)
        if (
            data.get("version") == 1
            and data.get("root") == str(root) and data.get("home") == str(home)
            and isinstance(owners, dict)
        ):
            return owners
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        pass
    return {}


def save_owners(root: Path, home: Path, sources: dict, *, previous_owners: dict) -> None:
    from .local_cache_worker import _write

    owners = dict(previous_owners)
    owners.update({relative: {"source": source} for relative, source in sources.items()})
    directory = _directory(root)
    directory.mkdir(exist_ok=True)
    _write(directory / "owners.json", json.dumps({
        "version": 1, "root": str(root), "home": str(home), "owners": owners,
    }) + "\n")


def _directory(root: Path) -> Path:
    from .local_cache_worker import metadata_directory

    directory = metadata_directory(root)
    if directory is None:
        raise ValueError("cannot resolve static preparation metadata")
    return directory


def publication_revision(root: Path) -> str:
    try:
        path = _directory(root) / "publication.json"
        if not path.is_file() or path.stat().st_size > 4096:
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("revision")
        if (
            data.get("version") == 1 and data.get("root") == str(root)
            and isinstance(value, str) and len(value) == 32
            and all(character in "0123456789abcdef" for character in value)
        ):
            return value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""


def record_publication(root: Path, *, changed: bool) -> str:
    """Record a possible output mutation before writes, never complete readiness."""
    if not changed:
        return publication_revision(root)
    from .local_cache_worker import _write

    revision = uuid.uuid4().hex
    directory = _directory(root)
    directory.mkdir(exist_ok=True)
    _write(directory / "publication.json", json.dumps({
        "version": 1, "root": str(root), "revision": revision,
    }) + "\n")
    return revision


def git_state(root: Path, *, deadline: float | None = None) -> dict:
    """Protect the Git index checked before generated-file replacement."""
    _check(deadline)
    admin = _directory(root).parent
    common_pointer = admin / "commondir"
    common = admin
    if common_pointer.is_file():
        common = (admin / common_pointer.read_text(encoding="utf-8").strip()).resolve()
    paths = {root / ".git", common_pointer, admin / "index", admin / "HEAD",
             admin / "config.worktree", common / "config", common / "packed-refs",
             common / "info" / "exclude"}
    from .static_instructions import NAMESPACE

    output = root / NAMESPACE
    paths.update(parent / ".gitignore" for parent in (output, *output.parents)
                 if parent == root or parent.is_relative_to(root))
    for directory in {admin, common}:
        paths.update(directory.glob("sharedindex.*"))
    head = admin / "HEAD"
    # Symbolic refs can themselves name symbolic refs. Bound malformed cycles.
    for _ in range(8):
        _check(deadline)
        if not head.is_file():
            break
        value = head.read_text(encoding="utf-8").strip()
        if not value.startswith("ref: "):
            break
        relative = Path(value[5:])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid Git symbolic reference")
        head = common / relative
        paths.add(head)
    else:
        raise ValueError("cyclic Git symbolic reference")
    result = {}
    for path in sorted(paths):
        value = stamp(path, deadline=deadline)
        # Creating local metadata/lock files is not a Git index or HEAD change.
        result[str(path)] = value[:4] if value is not None and stat.S_ISDIR(value[1]) else value
    return result


def require_git_state(root: Path, validated: dict, *, deadline: float | None = None) -> None:
    if git_state(root, deadline=deadline) != validated:
        raise ValueError("Git protection evidence changed after preflight; retry preparation")
