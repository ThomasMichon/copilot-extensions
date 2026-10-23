"""Reusable, named worker identities for declarative recipe loops.

A repository-issue-loop (or other declarative recipe) declaration can select a
worker identity by name instead of inlining its acting theme, focus, and rules
as ad hoc prompt prose. An identity is authored once, in the same
frontmatter-plus-markdown shape as an in-session sub-agent definition
(``*.agent.md``), and is independently revisable: sharpening its rules
improves every declaration that selects it.

Resolution order for a named identity (first hit wins):

1. A repo-local override: the canonical
   ``<repo>/.copilot-extensions/agent-dispatch/identities/<name>.identity.md``,
   then the legacy ``<repo>/.agent-dispatch/identities/<name>.identity.md``,
   with an explicit marketplace overlay under
   ``<repo>/.copilot-extensions/agent-dispatch/marketplaces/<marketplace-id>/identities/``.
   This lets an adopting repository carry its own private identity without
   touching this package.
2. The packaged built-in identities shipped inside this package:
   ``agent_dispatch/identities/<name>.identity.md`` -- included as package
   data so they resolve the same way from an editable checkout and an
   installed wheel/venv.

``list_worker_identities`` and ``describe_worker_identity`` expose the same
tiers read-only (backing ``agent-dispatch identities list|show``), so
introspection reports exactly what a declaration would resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

from .registrar import RegistrarError
from . import repo_config

_FRONTMATTER = "---"
_REPO_LOCAL_SUBDIR = repo_config.CANONICAL_REPO_CONFIG_DIR / "identities"
_LEGACY_REPO_LOCAL_SUBDIR = repo_config.LEGACY_REPO_CONFIG_DIR / "identities"
# Package data: src/agent_dispatch/worker_identities.py -> src/agent_dispatch/identities.
# Must live *inside* the agent_dispatch package (not a plugin-root sibling of
# src/) so it is actually included in the built wheel and resolves identically
# from an editable checkout and an installed venv.
_BUILTIN_DIR = Path(__file__).resolve().parent / "identities"


def resolve_identity_context(
    cwd: str | None, *, resolve_resources: bool = False, snapshot=None,
) -> str | None:
    """Validate an inherited source root; defer named-resource reads until use."""
    from .repository_resources import (
        RepositoryResourceSnapshot, is_repository_resource, validate_resource_arguments,
    )

    if cwd is None:
        return None
    if not isinstance(cwd, str) or not cwd:
        raise RegistrarError("worker_identity_cwd: expected a non-empty source root")
    validate_resource_arguments([cwd])
    if not is_repository_resource(cwd) and not any(
        cls(cwd).is_absolute() for cls in (Path, PurePosixPath, PureWindowsPath)
    ):
        raise RegistrarError("worker_identity_cwd: expected an absolute or named source root")
    if resolve_resources:
        return (snapshot or RepositoryResourceSnapshot(execution=False)).resolve(
            cwd, directory=True, execution=False,
        )
    return cwd


@dataclass(frozen=True)
class WorkerIdentity:
    """One reusable, named worker identity.

    ``rules`` is the identity's full markdown body -- its structural theme,
    focus, and behavioral rules -- rendered verbatim into a task prompt in
    place of a declaration's inlined ``worker_guidance`` prose.
    """

    name: str
    description: str
    rules: str
    source_path: str


def _parse_identity_file(path: Path) -> WorkerIdentity:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_FRONTMATTER):
        raise RegistrarError(
            f"worker identity {str(path)!r}: expected a '---' frontmatter header"
        )
    _, _, rest = text.partition(_FRONTMATTER)
    frontmatter_raw, sep, body = rest.partition(_FRONTMATTER)
    if not sep:
        raise RegistrarError(
            f"worker identity {str(path)!r}: unterminated frontmatter"
        )
    try:
        frontmatter = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:
        raise RegistrarError(
            f"worker identity {str(path)!r}: invalid frontmatter YAML: {exc}"
        ) from exc
    if not isinstance(frontmatter, dict):
        raise RegistrarError(
            f"worker identity {str(path)!r}: frontmatter must be a mapping"
        )
    name = frontmatter.get("name")
    description = frontmatter.get("description", "")
    if not isinstance(name, str) or not name:
        raise RegistrarError(
            f"worker identity {str(path)!r}: frontmatter 'name' must be a "
            "non-empty string"
        )
    if not isinstance(description, str):
        raise RegistrarError(
            f"worker identity {str(path)!r}: frontmatter 'description' must "
            "be a string"
        )
    rules = body.strip()
    if not rules:
        raise RegistrarError(
            f"worker identity {str(path)!r}: body (the rules) must not be empty"
        )
    return WorkerIdentity(
        name=name,
        description=description.strip(),
        rules=rules,
        source_path=str(path),
    )


SOURCE_OVERLAY = "overlay"
SOURCE_REPOSITORY = "repository"
SOURCE_LEGACY_REPOSITORY = "legacy-repository"
SOURCE_PACKAGED = "packaged"
_IDENTITY_SUFFIX = ".identity.md"


def _candidate_tiers(*, cwd: Path | None = None) -> list[tuple[str, Path]]:
    """The identity directories in resolution order (first hit wins).

    The single source of precedence truth for both ``load_worker_identity``
    and the read-only ``list_worker_identities`` introspection.
    """
    base = cwd if cwd is not None else Path.cwd()
    overlay = repo_config.overlay_repo_surface_dir(base, "identities")
    tiers: list[tuple[str, Path]] = []
    if overlay is not None:
        tiers.append((SOURCE_OVERLAY, overlay))
    tiers.extend(
        [
            (SOURCE_REPOSITORY, base / _REPO_LOCAL_SUBDIR),
            (SOURCE_LEGACY_REPOSITORY, base / _LEGACY_REPO_LOCAL_SUBDIR),
            (SOURCE_PACKAGED, _BUILTIN_DIR),
        ]
    )
    return tiers


def _candidate_paths(name: str, *, cwd: Path | None = None) -> list[Path]:
    filename = f"{name}{_IDENTITY_SUFFIX}"
    return [directory / filename for _, directory in _candidate_tiers(cwd=cwd)]


def load_worker_identity(name: str, *, cwd: Path | None = None) -> WorkerIdentity:
    """Resolve and parse a named worker identity.

    Raises ``RegistrarError`` if ``name`` does not resolve to any candidate
    path, or if the resolved file is malformed.
    """
    if not name or not isinstance(name, str):
        raise RegistrarError("worker_identity: expected a non-empty string")
    for candidate in _candidate_paths(name, cwd=cwd):
        if candidate.is_file():
            return _parse_identity_file(candidate)
    raise RegistrarError(
        f"worker_identity {name!r}: no identity file found (looked for "
        f"{[str(p) for p in _candidate_paths(name, cwd=cwd)]})"
    )


@dataclass(frozen=True)
class WorkerIdentityEntry:
    """One effective identity as resolution would see it (read-only view).

    ``name`` is the selector a declaration's ``worker_identity`` uses (the
    file stem). ``shadows`` lists lower-precedence files of the same name that
    the effective one overrides, as ``(source, path)`` pairs. ``identity`` is
    ``None`` and ``error`` is set when the effective file is malformed.
    """

    name: str
    source: str
    path: str
    shadows: tuple[tuple[str, str], ...]
    identity: WorkerIdentity | None
    error: str | None

    def to_dict(self, *, include_rules: bool = False) -> dict:
        out: dict = {
            "name": self.name,
            "description": self.identity.description if self.identity else None,
            "source": self.source,
            "path": self.path,
            "shadows": [{"source": s, "path": p} for s, p in self.shadows],
            "error": self.error,
        }
        if include_rules:
            out["rules"] = self.identity.rules if self.identity else None
        return out


def _resolution_hits(
    name: str, tiers: list[tuple[str, Path]]
) -> list[tuple[str, Path]]:
    """The files ``load_worker_identity(name)`` would consider, in order.

    Probed with the same ``is_file()`` lookups as runtime, so filename
    matching (e.g. case-insensitivity) follows the filesystem exactly.
    """
    hits: list[tuple[str, Path]] = []
    for source, directory in tiers:
        path = directory / f"{name}{_IDENTITY_SUFFIX}"
        if path.is_file():
            hits.append((source, path))
    return hits


def _file_key(path: Path) -> object:
    try:
        st = path.stat()
    except OSError:
        return str(path)
    return (st.st_dev, st.st_ino) if st.st_ino else str(path.resolve())


def _entry(name: str, hits: list[tuple[str, Path]]) -> WorkerIdentityEntry:
    (source, path), rest = hits[0], hits[1:]
    identity: WorkerIdentity | None = None
    error: str | None = None
    try:
        identity = _parse_identity_file(path)
    # ValueError covers UnicodeDecodeError and YAML scalar constructors
    # (e.g. an impossible timestamp) that escape ``yaml.YAMLError``.
    except (RegistrarError, OSError, ValueError) as exc:
        error = str(exc)
    return WorkerIdentityEntry(
        name=name,
        source=source,
        path=str(path),
        shadows=tuple((s, str(p)) for s, p in rest),
        identity=identity,
        error=error,
    )


def list_worker_identities(*, cwd: Path | None = None) -> list[WorkerIdentityEntry]:
    """Every identity name resolvable from ``cwd``, each at its effective tier.

    Uses the same tiers as ``load_worker_identity``, so the reported source
    and path are exactly what a declaration selecting that name resolves.
    A malformed effective file is reported via ``error`` rather than raising,
    so one bad override does not hide the rest of the listing. Read-only.

    On a case-insensitive filesystem, filename spellings that resolve to the
    same files (``Goal-Driven`` vs ``goal-driven``) collapse into one entry
    named by the spelling from the lowest-precedence tier (e.g. the packaged
    name); on a case-sensitive one they stay distinct identities.
    """
    tiers = _candidate_tiers(cwd=cwd)
    # name -> index of the lowest-precedence tier its file appears in.
    discovered: dict[str, int] = {}
    listed: set[str] = set()
    for index, (_, directory) in enumerate(tiers):
        if not directory.is_dir():
            continue
        for path in directory.glob(f"*{_IDENTITY_SUFFIX}"):
            if path.is_file():
                discovered[path.name[: -len(_IDENTITY_SUFFIX)]] = index
                listed.add(str(path))
    seen: set[tuple[object, ...]] = set()
    resolved: dict[str, list[tuple[str, Path]]] = {}
    for name in sorted(discovered, key=lambda n: (-discovered[n], n)):
        hits = _resolution_hits(name, tiers)
        if not hits:
            continue
        key = (name.casefold(), *(_file_key(path) for _, path in hits))
        # Only a spelling that resolved some tier through case-insensitive
        # matching (no literal directory entry; compared as strings because
        # Windows path equality ignores case) is an alias of another
        # spelling; linked files under distinct names stay separate selectors.
        if key in seen and any(str(path) not in listed for _, path in hits):
            continue
        seen.add(key)
        resolved[name] = hits
    return [_entry(name, resolved[name]) for name in sorted(resolved)]


def describe_worker_identity(name: str, *, cwd: Path | None = None) -> WorkerIdentityEntry:
    """Resolve ``name`` exactly as ``load_worker_identity`` does, plus provenance.

    Raises ``RegistrarError`` for an unknown or malformed identity.
    """
    identity = load_worker_identity(name, cwd=cwd)
    hits = _resolution_hits(name, _candidate_tiers(cwd=cwd))
    return WorkerIdentityEntry(
        name=name,
        source=hits[0][0],
        path=identity.source_path,
        shadows=tuple((s, str(p)) for s, p in hits[1:]),
        identity=identity,
        error=None,
    )
