"""Checkout-local evidence for reusing a successful static publication."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
import uuid

_READS: ContextVar[dict | None] = ContextVar("static_instruction_reads", default=None)
_SCHEMA = "agent-worktrees.static-preparation"
_VERSION = 2


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


def _hash_file(path: Path, deadline: float | None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            _check(deadline)
            block = handle.read(1024 * 1024)
            _check(deadline)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def stamp(
    path: Path, *, content_digest: bool = False, allow_links: bool = False,
    deadline: float | None = None,
) -> list | None:
    _check(deadline)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    value = [
        str(path.resolve()), info.st_mode, info.st_dev, info.st_ino,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    ]
    regular = stat.S_ISREG(info.st_mode)
    if content_digest and allow_links and stat.S_ISLNK(info.st_mode):
        regular = stat.S_ISREG(path.stat().st_mode)
    if content_digest and regular:
        value.append(_hash_file(path, deadline))
    _check(deadline)
    return value


def observe(path: Path) -> None:
    reads = _READS.get()
    if reads is not None:
        key, value = str(path), stamp(path)
        previous = reads.get(key)
        if key in reads and (previous[:7] if previous is not None else None) != value:
            raise ValueError("instruction sources changed during preparation; retry publication")
        if key not in reads:
            reads[key] = value


def remember_bytes(path: Path, content: bytes) -> None:
    reads = _READS.get()
    if reads is None:
        return
    key = str(path)
    value = [*reads[key][:7], hashlib.sha256(content).hexdigest()]
    if len(reads[key]) > 7 and reads[key] != value:
        raise ValueError("instruction sources changed during preparation; retry publication")
    reads[key] = value


@contextmanager
def capture():
    reads: dict = {}
    token = _READS.set(reads)
    try:
        yield reads
    finally:
        _READS.reset(token)


def matches(evidence: dict, *, deadline: float | None = None) -> bool:
    return all(
        stamp(
            Path(path), content_digest=isinstance(value, list) and len(value) > 7,
            allow_links=isinstance(value, list) and len(value) > 1 and stat.S_ISLNK(value[1]),
            deadline=deadline,
        ) == value
        for path, value in evidence.items()
    )


def generation(selection: list, reads: dict) -> str:
    return hashlib.sha256(json.dumps(
        [selection, reads], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _ownership_digest(owners: dict) -> str:
    return hashlib.sha256(json.dumps(
        owners, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def ownership_history(root: Path, home: Path, *, deadline: float | None = None) -> dict:
    """Previous source/byte ownership is history, not reusable readiness."""
    try:
        _check(deadline)
        path = _directory(root) / "prepared.json"
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        owners = data.get("owners")
        _check(deadline)
        if (
            data.get("schema") == _SCHEMA and data.get("version") == _VERSION
            and data.get("root") == str(root) and data.get("home") == str(home)
            and data.get("readiness") in ("ready", "partial")
            and isinstance(owners, dict)
            and data.get("ownership_digest") == _ownership_digest(owners)
        ):
            return owners
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        pass
    return {}


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
    """Detect index/HEAD changes without starting Git on an unchanged launch."""
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
        value = stamp(path, content_digest=True, allow_links=True, deadline=deadline)
        # Creating local metadata/lock files is not a Git index or HEAD change.
        result[str(path)] = value[:4] if value is not None and stat.S_ISDIR(value[1]) else value
    return result


def require_git_state(root: Path, validated: dict, *, deadline: float | None = None) -> None:
    if git_state(root, deadline=deadline) != validated:
        raise ValueError("Git protection evidence changed after preflight; retry preparation")


def output_state(root: Path, namespace: str, *, deadline: float | None = None) -> dict:
    output = root / namespace
    paths = {output, *output.parents}
    paths = {path for path in paths if path != root and path.is_relative_to(root)}
    if output.exists():
        paths.update(output.rglob("*"))
    result = {}
    for path in sorted(paths):
        value = stamp(path, content_digest=True, deadline=deadline)
        if value is not None and (
            stat.S_ISLNK(value[1]) or not (stat.S_ISREG(value[1]) or stat.S_ISDIR(value[1]))
        ):
            raise ValueError("instruction output is not a regular file or directory")
        result[str(path)] = value
    return result


def read(
    root: Path, home: Path, selection: list, *, deadline: float | None = None,
) -> Publication | None:
    from .static_instructions import pair_pending_path

    try:
        _check(deadline)
        if pair_pending_path(root).exists():
            return None
        path = _directory(root) / "prepared.json"
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        _check(deadline)
        if (
            data.get("schema") != _SCHEMA or data.get("version") != _VERSION
            or data.get("readiness") != "ready"
            or data.get("root") != str(root) or data.get("home") != str(home)
            or data.get("selection") != selection
            or not isinstance(data.get("inputs"), dict)
            or not isinstance(data.get("outputs"), dict)
            or not isinstance(data.get("generation"), str)
            or type(data.get("count")) is not int or data["count"] < 0
            or not isinstance(data.get("owners"), dict)
            or data.get("ownership_digest") != _ownership_digest(data["owners"])
            or data["count"] != len(data["owners"])
        ):
            return None
        if (
            not matches(data["inputs"], deadline=deadline) or not matches(data["outputs"], deadline=deadline)
            or data.get("git") != git_state(root, deadline=deadline)
            or data["generation"] != generation(selection, data["inputs"])
            or data.get("publication_revision", "") != publication_revision(root)
        ):
            return None
        _check(deadline)
        return Publication(
            unchanged=data["count"], generation=data["generation"], reused=True,
            publication_revision=data.get("publication_revision", ""),
        )
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None


def save(
    root: Path, home: Path, selection: list, reads: dict, desired: dict, namespace: str,
    *, validated_git: dict, revision: str, deadline: float | None = None,
    ready: bool, sources: dict, previous_owners: dict,
) -> str:
    from .local_cache_worker import _write

    require_git_state(root, validated_git, deadline=deadline)
    identity = generation(selection, reads) if ready else ""
    directory = _directory(root)
    directory.mkdir(exist_ok=True)
    outputs = output_state(root, namespace, deadline=deadline)
    expected = {
        str(root / namespace / relative): hashlib.sha256(content).hexdigest()
        for relative, content in desired.items()
    }
    actual = {
        path: value[-1] for path, value in outputs.items()
        if value is not None and stat.S_ISREG(value[1])
    }
    if (ready and actual != expected) or any(actual.get(path) != digest for path, digest in expected.items()):
        raise ValueError("instruction output changed before readiness was recorded")
    owners = {} if ready else {
        relative: record for relative, record in previous_owners.items()
        if isinstance(record, dict) and isinstance(relative, str)
        and isinstance(record.get("source"), str) and isinstance(record.get("sha256"), str)
        and actual.get(str(root / namespace / relative)) == record.get("sha256")
    }
    owners.update({
        relative: {"source": sources[relative], "sha256": hashlib.sha256(content).hexdigest()}
        for relative, content in desired.items()
    })
    data = {
        "schema": _SCHEMA, "version": _VERSION, "root": str(root), "home": str(home),
        "readiness": "ready" if ready else "partial",
        "selection": selection, "inputs": reads, "count": len(desired),
        "generation": identity, "outputs": outputs,
        "git": validated_git, "model_admission": "unknown",
        "publication_revision": revision,
        "owners": owners, "ownership_digest": _ownership_digest(owners),
    }
    require_git_state(root, validated_git, deadline=deadline)
    _write(directory / "prepared.json", json.dumps(data) + "\n")
    return identity
