"""Registrar discovery -- pointers to declaration locations + aggregation (Phase 2).

:mod:`agent_dispatch.registrar` is **pure** (a decoded mapping -> a
:class:`~agent_dispatch.registrar.ProfileDeclaration`). This module is the **I/O
layer** that finds and reads declaration *documents*:

* a cache-populate-style **pointer registry** -- a system, service, or repo records
  a lightweight *pointer* to a directory of declarations in its own footprint, and
  the supervisor aggregates every pointer (vision: *declarative-discovered-registrar*);
* the **in-repo ``.agent-dispatch/registrar/``** convention -- a repo carries its
  supervised work with its code, so it lights up on repo-sync and winds down when the
  repo (or declaration) is gone;
* the **aggregation** that reads every pointed location into the declared profile set
  the singleton supervisor reconciles.

There is **one source of truth** -- the declared documents. The pointer registry is a
thin index of *where to look*, not a second copy of the declarations. Persistence is a
single JSON file so ``registrar add-pointer`` (the CLI, a later slice) is a thin writer
over it.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .registrar import ProfileDeclaration, RegistrarError, load_declaration

#: The in-repo convention: a repo declares its supervised work here, discovered on
#: sync. Relative to the repo root.
INREPO_SUBDIR = ".agent-dispatch/registrar"

#: Declaration document suffixes, in precedence order (YAML-primary, JSON accepted).
_DECL_SUFFIXES = (".yaml", ".yml", ".json")

#: Env override for the registrar state dir (parity with the run-dir override), so a
#: test or an alternate deployment can relocate the pointer registry.
REGISTRAR_DIR_ENV = "AGENT_DISPATCH_REGISTRAR_DIR"


def registrar_dir() -> Path:
    """The directory holding the pointer registry (``~/.agent-dispatch/registrar``)."""
    override = os.environ.get(REGISTRAR_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-dispatch" / "registrar"


def pointers_file(base: Path | None = None) -> Path:
    """Path of the pointer-registry JSON file."""
    return (base or registrar_dir()) / "pointers.json"


@dataclass(frozen=True)
class Pointer:
    """A recorded location to read declarations from (the registry's atom).

    ``location`` is a directory of declaration documents. ``kind`` distinguishes a
    plain ``dir`` pointer from a ``repo`` pointer (whose ``location`` is a repo root
    and whose declarations live under :data:`INREPO_SUBDIR`). ``owner`` is provenance
    stamped onto every declaration read through this pointer.
    """

    name: str
    location: str
    kind: str = "dir"
    owner: str | None = None

    def resolved_location(self) -> Path:
        """The directory to scan for declaration documents.

        For a ``repo`` pointer that is the repo root's ``.agent-dispatch/registrar``;
        for a ``dir`` pointer it is the location itself.
        """
        base = Path(self.location).expanduser()
        return base / INREPO_SUBDIR if self.kind == "repo" else base

    def effective_owner(self) -> str:
        """Provenance token for declarations read here (explicit owner, else derived)."""
        if self.owner:
            return self.owner
        stem = Path(self.location).expanduser().name or self.name
        return f"repo:{stem}" if self.kind == "repo" else f"pointer:{self.name}"

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {"name": self.name, "location": self.location, "kind": self.kind}
        if self.owner:
            d["owner"] = self.owner
        return d

    @classmethod
    def from_dict(cls, data: Mapping) -> Pointer:
        if not isinstance(data, Mapping):
            raise RegistrarError(f"pointer: expected a mapping, got {type(data).__name__}")
        name = data.get("name")
        location = data.get("location")
        if not isinstance(name, str) or not name:
            raise RegistrarError("pointer: 'name' is required and must be a non-empty string")
        if not isinstance(location, str) or not location:
            raise RegistrarError("pointer: 'location' is required and must be a non-empty string")
        kind = data.get("kind", "dir")
        if kind not in ("dir", "repo"):
            raise RegistrarError(f"pointer.kind: must be 'dir' or 'repo', got {kind!r}")
        owner = data.get("owner")
        if owner is not None and not isinstance(owner, str):
            raise RegistrarError(f"pointer.owner: expected a string, got {owner!r}")
        return cls(name=name, location=location, kind=kind, owner=owner or None)


def repo_pointer(
    repo_root: str | Path, *, name: str | None = None, owner: str | None = None
) -> Pointer:
    """Build the in-repo pointer for ``repo_root`` (its ``.agent-dispatch/registrar``)."""
    root = Path(repo_root).expanduser()
    return Pointer(name=name or root.name, location=str(root), kind="repo", owner=owner)


# -- pointer-registry persistence (the thin index) ---------------------------

def load_pointers(base: Path | None = None) -> list[Pointer]:
    """Read the persisted pointer registry (empty when the file is absent)."""
    path = pointers_file(base)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistrarError(f"{path}: invalid pointer registry JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise RegistrarError(
            f"{path}: pointer registry must be a JSON list, got {type(raw).__name__}"
        )
    return [Pointer.from_dict(item) for item in raw]


def save_pointers(pointers: Iterable[Pointer], base: Path | None = None) -> Path:
    """Atomically write the pointer registry, returning its path."""
    path = pointers_file(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([p.to_dict() for p in pointers], indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pointers-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def add_pointer(
    name: str,
    location: str | Path,
    *,
    kind: str = "dir",
    owner: str | None = None,
    base: Path | None = None,
) -> Pointer:
    """Add (or replace) a pointer by ``name`` and persist. Returns the stored pointer.

    Idempotent: re-adding the same ``name`` with the same target is a no-op; re-adding
    with a *different* target replaces it (the pointer index has one entry per name).
    """
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise RegistrarError(f"pointer name {name!r}: use only letters, digits, '-' and '_'")
    pointer = Pointer(name=name, location=str(Path(location).expanduser()), kind=kind, owner=owner)
    Pointer.from_dict(pointer.to_dict())  # validate kind/shape via the loader
    existing = [p for p in load_pointers(base) if p.name != name]
    save_pointers([*existing, pointer], base)
    return pointer


def remove_pointer(name: str, base: Path | None = None) -> bool:
    """Remove a pointer by ``name``. Returns True if one was removed."""
    pointers = load_pointers(base)
    kept = [p for p in pointers if p.name != name]
    if len(kept) == len(pointers):
        return False
    save_pointers(kept, base)
    return True


# -- reading declaration documents -------------------------------------------

def _decode(text: str, suffix: str, *, where: str) -> Mapping:
    """Decode one declaration document by suffix (YAML-primary, JSON accepted)."""
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistrarError(f"{where}: invalid JSON: {exc}") from exc
    else:  # .yaml / .yml
        try:
            import yaml  # lazy: only YAML documents need it
        except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
            raise RegistrarError(
                f"{where}: reading a YAML declaration requires PyYAML; install it or use JSON"
            ) from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RegistrarError(f"{where}: invalid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RegistrarError(
            f"{where}: a declaration document must be a mapping, got {type(data).__name__}"
        )
    return data


def read_declaration_file(path: str | Path) -> ProfileDeclaration:
    """Read + validate one declaration document (``.yaml`` / ``.yml`` / ``.json``)."""
    p = Path(path).expanduser()
    if p.suffix not in _DECL_SUFFIXES:
        raise RegistrarError(
            f"{p}: unrecognized declaration suffix {p.suffix!r}; "
            f"expected one of {list(_DECL_SUFFIXES)}"
        )
    data = _decode(p.read_text(encoding="utf-8"), p.suffix, where=str(p))
    return load_declaration(data)


def _iter_declaration_files(location: Path) -> list[Path]:
    """Declaration documents directly under ``location`` (sorted, deterministic)."""
    if not location.is_dir():
        return []
    return sorted(
        (f for f in location.iterdir() if f.is_file() and f.suffix in _DECL_SUFFIXES),
        key=lambda f: f.name,
    )


def read_location(location: str | Path, *, owner: str | None = None) -> list[ProfileDeclaration]:
    """Read every declaration document directly under ``location``.

    Each declaration is stamped with ``owner`` provenance (when it does not carry its
    own). Missing/empty directories yield an empty list -- a pointer to a not-yet-synced
    repo is simply quiet, not an error.
    """
    loc = Path(location).expanduser()
    out: list[ProfileDeclaration] = []
    for f in _iter_declaration_files(loc):
        decl = read_declaration_file(f)
        out.append(decl.with_owner(owner) if owner else decl)
    return out


def discover(
    pointers: Iterable[Pointer] | None = None,
    *,
    base: Path | None = None,
) -> list[ProfileDeclaration]:
    """Aggregate the declared profile set across all pointers.

    With no ``pointers`` the persisted registry is used. Declarations are returned
    sorted by name. A **duplicate profile name** across locations is a conflict (two
    sources claiming the same unit) and is rejected -- the registry is one source of
    truth, so the ambiguity must be resolved at declaration time.
    """
    pts = list(pointers) if pointers is not None else load_pointers(base)
    by_name: dict[str, tuple[str, ProfileDeclaration]] = {}
    for pointer in pts:
        owner = pointer.effective_owner()
        for decl in read_location(pointer.resolved_location(), owner=owner):
            if decl.name in by_name:
                prior_owner = by_name[decl.name][0]
                raise RegistrarError(
                    f"duplicate profile name {decl.name!r}: declared by both "
                    f"{prior_owner!r} and {owner!r} -- names must be unique across the registry"
                )
            by_name[decl.name] = (owner, decl)
    return [decl for _, decl in sorted(by_name.values(), key=lambda item: item[1].name)]


def discover_repo(repo_root: str | Path, *, owner: str | None = None) -> list[ProfileDeclaration]:
    """Convenience: read a single repo's in-repo ``.agent-dispatch/registrar`` declarations.

    The repo-sync discovery unit -- given a synced repo root, read what it declares
    without touching the persisted pointer registry.
    """
    pointer = repo_pointer(repo_root, owner=owner)
    return discover([pointer])
