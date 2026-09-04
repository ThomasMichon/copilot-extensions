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
import logging
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from dropin_registry import Finding, ScanAuthority, WarningTracker
from plugin_activation import ActivationReport

from .registrar import ProfileDeclaration, RegistrarError, load_declaration

if TYPE_CHECKING:
    from .registrar_registry import CombinedRegistrarReport, RegistrarCandidate

#: The in-repo convention: a repo declares its supervised work here, discovered on
#: sync. Relative to the repo root.
INREPO_SUBDIR = ".agent-dispatch/registrar"

#: Declaration document suffixes, in precedence order (YAML-primary, JSON accepted).
_DECL_SUFFIXES = (".yaml", ".yml", ".json")

#: Env override for the registrar state dir (parity with the run-dir override), so a
#: test or an alternate deployment can relocate the pointer registry.
REGISTRAR_DIR_ENV = "AGENT_DISPATCH_REGISTRAR_DIR"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
log = logging.getLogger(__name__)


class RegistrarIndeterminateError(RegistrarError):
    """Trusted registrar state could not be read authoritatively."""


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


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

def _load_pointers_with_authority(
    base: Path | None = None,
) -> tuple[ScanAuthority, list[Pointer]]:
    """Read pointers and report absence from the same filesystem operation."""
    path = pointers_file(base)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ScanAuthority.ABSENT, []
    except UnicodeError as exc:
        raise RegistrarError(
            f"{path}: invalid pointer registry encoding: {exc}"
        ) from exc
    except OSError as exc:
        raise RegistrarIndeterminateError(
            f"{path}: pointer registry could not be read: {exc}"
        ) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistrarError(f"{path}: invalid pointer registry JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise RegistrarError(
            f"{path}: pointer registry must be a JSON list, got {type(raw).__name__}"
        )
    out: list[Pointer] = []
    for i, item in enumerate(raw):
        try:
            out.append(Pointer.from_dict(item))
        except RegistrarError as exc:
            raise RegistrarError(f"{path}[{i}]: {exc}") from exc
    return ScanAuthority.COMPLETE, out


def load_pointers(base: Path | None = None) -> list[Pointer]:
    """Read the persisted pointer registry (empty when the file is absent)."""
    return _load_pointers_with_authority(base)[1]


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
    existing = load_pointers(base)
    current = next((p for p in existing if p.name == name), None)
    if current == pointer:
        return pointer  # truly idempotent: identical entry, don't rewrite the file
    others = [p for p in existing if p.name != name]
    save_pointers([*others, pointer], base)
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


def _resolve_declaration_paths(
    declaration: ProfileDeclaration, directory: Path
) -> ProfileDeclaration:
    if declaration.kind != "emitter":
        return declaration
    spec = dict(declaration.spec)
    cwd = spec.get("cwd")
    is_absolute = (
        Path(cwd).is_absolute()
        or PurePosixPath(cwd).is_absolute()
        or PureWindowsPath(cwd).is_absolute()
    ) if isinstance(cwd, str) else False
    if not isinstance(cwd, str) or not cwd or is_absolute:
        return declaration
    spec["cwd"] = str((directory / cwd).resolve())
    return replace(declaration, spec=spec)


def read_declaration_file_set(
    path: str | Path,
    *,
    allow_plugin_companion: bool = False,
) -> tuple[ProfileDeclaration, ...]:
    """Read one document and expand it into one or more runtime declarations."""
    p = Path(path).expanduser()
    if p.suffix not in _DECL_SUFFIXES:
        raise RegistrarError(
            f"{p}: unrecognized declaration suffix {p.suffix!r}; "
            f"expected one of {list(_DECL_SUFFIXES)}"
        )
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RegistrarError(f"{p}: invalid declaration encoding: {exc}") from exc
    except OSError as exc:
        raise RegistrarIndeterminateError(
            f"{p}: declaration could not be read: {exc}"
        ) from exc
    data = dict(_decode(text, p.suffix, where=str(p)))
    if data.get("kind") == "reviewer-loop":
        from .reviewer_loops import expand_reviewer_loop

        declarations = expand_reviewer_loop(data)
    elif data.get("kind") == "repository-issue-loop":
        from .repository_issue_loops import expand_repository_issue_loop

        declarations = expand_repository_issue_loop(data)
    else:
        declarations = (
            load_declaration(
                data, allow_plugin_companion=allow_plugin_companion
            ),
        )
    return tuple(
        _resolve_declaration_paths(declaration, p.parent)
        for declaration in declarations
    )


def read_declaration_file(
    path: str | Path, *, allow_plugin_companion: bool = False
) -> ProfileDeclaration:
    """Read a document that represents exactly one runtime declaration."""
    declarations = read_declaration_file_set(
        path, allow_plugin_companion=allow_plugin_companion
    )
    if len(declarations) != 1:
        raise RegistrarError(
            f"{path}: expands to {len(declarations)} declarations; "
            "read it through registrar discovery"
        )
    return declarations[0]


def _iter_declaration_files(location: Path) -> list[Path]:
    """Declaration documents directly under ``location`` (sorted, deterministic)."""
    try:
        root_info = location.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RegistrarIndeterminateError(
            f"{location}: declaration directory could not be inspected: {exc}"
        ) from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or _is_reparse(root_info)
    ):
        raise RegistrarError(
            f"{location}: declaration location must be a regular non-reparse directory"
        )
    try:
        entries = sorted(location.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise RegistrarIndeterminateError(
            f"{location}: declaration directory could not be enumerated: {exc}"
        ) from exc

    accepted: list[Path] = []
    for entry in entries:
        if entry.suffix not in _DECL_SUFFIXES:
            continue
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RegistrarIndeterminateError(
                f"{entry}: declaration entry could not be inspected: {exc}"
            ) from exc
        if (
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not _is_reparse(info)
        ):
            accepted.append(entry)
    return accepted


def read_location(location: str | Path, *, owner: str | None = None) -> list[ProfileDeclaration]:
    """Read every declaration document directly under ``location``.

    Each declaration is stamped with ``owner`` provenance (when it does not carry its
    own). Missing/empty directories yield an empty list -- a pointer to a not-yet-synced
    repo is simply quiet, not an error.
    """
    loc = Path(location).expanduser()
    out: list[ProfileDeclaration] = []
    for f in _iter_declaration_files(loc):
        for decl in read_declaration_file_set(f):
            out.append(decl.with_owner(owner) if owner else decl)
    return out


def discover_trusted(
    pointers: Iterable[Pointer] | None = None,
    *,
    base: Path | None = None,
) -> tuple[ScanAuthority, list[ProfileDeclaration]]:
    """Aggregate the declared profile set across all pointers.

    With no ``pointers`` the persisted registry is used. Declarations are returned
    sorted by name. A **duplicate profile name** across locations is a conflict (two
    sources claiming the same unit) and is rejected -- the registry is one source of
    truth, so the ambiguity must be resolved at declaration time.
    """
    if pointers is None:
        authority, pts = _load_pointers_with_authority(base)
    else:
        authority, pts = ScanAuthority.COMPLETE, list(pointers)
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
    declarations = [
        decl for _, decl in sorted(by_name.values(), key=lambda item: item[1].name)
    ]
    return authority, declarations


def discover(
    pointers: Iterable[Pointer] | None = None,
    *,
    base: Path | None = None,
) -> list[ProfileDeclaration]:
    """Compatibility wrapper returning trusted declarations only."""
    return discover_trusted(pointers, base=base)[1]


def discover_repo(repo_root: str | Path, *, owner: str | None = None) -> list[ProfileDeclaration]:
    """Convenience: read a single repo's in-repo ``.agent-dispatch/registrar`` declarations.

    The repo-sync discovery unit -- given a synced repo root, read what it declares
    without touching the persisted pointer registry.
    """
    pointer = repo_pointer(repo_root, owner=owner)
    return discover([pointer])


# -- Legacy env-profile back-compat bridge (Phase 4 migration) ----------------
#
# The migration off the unit-per-profile model is gradual: a host may still carry
# its old ``supervisor.env`` (primary) + ``supervisors/*.env`` profiles while the
# single ``supervise serve`` daemon takes over. This bridge lets the daemon run
# those legacy profiles *as declarations* (via
# :func:`agent_dispatch.registrar.declaration_from_env`) so switching the unit to
# ``supervise serve`` reproduces existing supervision losslessly -- no behavior
# change until an operator migrates each profile to a first-class declaration.

#: The install dir that holds the legacy supervisor env files (``~/.agent-dispatch``),
#: overridable for tests/alternate deployments.
INSTALL_DIR_ENV = "AGENT_DISPATCH_INSTALL_DIR"


def install_dir() -> Path:
    """The agent-dispatch install dir (holds ``supervisor.env`` + ``supervisors/``)."""
    override = os.environ.get(INSTALL_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-dispatch"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` env file (``#`` comments + blanks skipped)."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def read_legacy_env_profiles(
    *,
    env_file: str | Path | None = None,
    profile_dir: str | Path | None = None,
) -> list[ProfileDeclaration]:
    """Read legacy ``AGENT_DISPATCH_SUPERVISE_*`` env profiles as declarations.

    Reads the primary ``env_file`` (default ``<install>/supervisor.env``) and every
    ``profile_dir/*.env`` (default ``<install>/supervisors/``), translating each into
    a :class:`ProfileDeclaration` via
    :func:`agent_dispatch.registrar.declaration_from_env`. The profile *name* is the
    file stem; provenance is stamped ``legacy-env:<name>`` when the profile does not
    carry its own owner.

    A profile with **no opt-in ``LABELS``** is skipped -- it is inert under the
    label-gated installer (a label-less supervisor would embody everything), so an
    empty default ``supervisor.env`` contributes nothing. A duplicate name (two env
    files sharing a stem) keeps the first read; the primary ``supervisor.env`` is read
    before the profile directory.
    """
    from .registrar import declaration_from_env

    base = install_dir()
    primary = Path(env_file) if env_file is not None else base / "supervisor.env"
    profiles = Path(profile_dir) if profile_dir is not None else base / "supervisors"

    sources: list[tuple[str, Path]] = []
    if primary.is_file():
        sources.append((primary.stem, primary))
    if profiles.is_dir():
        sources.extend((f.stem, f) for f in sorted(profiles.glob("*.env")))

    out: list[ProfileDeclaration] = []
    seen: set[str] = set()
    for name, path in sources:
        if name in seen:
            continue
        env = _parse_env_file(path)
        if not env.get("AGENT_DISPATCH_SUPERVISE_LABELS", "").strip():
            continue  # label-less -> inert; skip (matches the installer's gate)
        seen.add(name)
        decl = declaration_from_env(name, env)
        out.append(decl if decl.owner else decl.with_owner(f"legacy-env:{name}"))
    return out


def discover_with_legacy(
    *,
    base: Path | None = None,
    env_file: str | Path | None = None,
    profile_dir: str | Path | None = None,
) -> list[ProfileDeclaration]:
    """Pointer-discovered declarations plus the legacy env profiles, deduped by name.

    A first-class **declaration wins** over a legacy env profile of the same name, so
    migrating a profile to a declaration (and leaving the old ``*.env`` in place during
    transition) does not double-run it. Used by ``supervise serve --legacy-env``.
    """
    declared = discover(base=base)
    names = {d.name for d in declared}
    legacy = [
        d for d in read_legacy_env_profiles(env_file=env_file, profile_dir=profile_dir)
        if d.name not in names
    ]
    return [*declared, *legacy]


@dataclass(frozen=True)
class RegistrarDiscoveryReport:
    """Trusted-pointer and plugin-candidate state from one refresh."""

    trusted_authority: ScanAuthority
    trusted_error: str | None
    combined: CombinedRegistrarReport


class RegistrarSources:
    """Stateful runtime view across trusted pointers and plugin candidates.

    Trusted pointer failures retain only the last trusted set. Plugin candidates
    continue scanning and reconciling independently, so a damaged ``pointers.json``
    cannot freeze a confirmed plugin disablement or deletion.
    """

    def __init__(
        self,
        *,
        base: Path | None = None,
        dropins: Path | None = None,
        activation_source: Callable[[], ActivationReport] | None = None,
        warning_tracker: WarningTracker | None = None,
        trusted_warning_tracker: WarningTracker | None = None,
    ):
        self.base = base
        self.dropins = dropins
        self.activation_source = activation_source
        self.warning_tracker = warning_tracker or WarningTracker()
        self.trusted_warning_tracker = (
            trusted_warning_tracker or WarningTracker(limit=1)
        )
        self._trusted: list[ProfileDeclaration] = []
        self._plugin_entries: dict[str, RegistrarCandidate] = {}
        self.last_report: RegistrarDiscoveryReport | None = None

    def _trusted_error_finding(
        self,
        exc: Exception,
        *,
        reason: str,
    ) -> Finding:
        path = pointers_file(self.base)
        return Finding(
            registry="pointers.json",
            entry=str(path),
            status="indeterminate",
            reason=reason,
            remedy=(
                f"Run `agent-dispatch registrar doctor` and repair {path}; "
                "the runtime is retaining the last trusted declaration set."
            ),
            detail=str(exc),
        )

    @staticmethod
    def _emit_batch(batch, *, doctor: str) -> None:
        for finding in batch.emitted:
            target = f" -> {finding.target}" if finding.target else ""
            detail = f": {finding.detail}" if finding.detail else ""
            log.warning(
                "%s: %s: %s%s%s; run `%s`",
                finding.registry,
                finding.reason,
                finding.entry,
                target,
                detail,
                doctor,
            )
        if batch.suppressed:
            log.warning(
                "%s additional registrar finding(s) suppressed; run `%s`",
                batch.suppressed,
                doctor,
            )
        if batch.recovered:
            log.info(
                "%s registrar entry finding(s) recovered; current state is active again",
                batch.recovered,
            )

    def refresh(self, *, emit_warnings: bool = True) -> RegistrarDiscoveryReport:
        """Refresh both tiers while retaining uncertainty only within its tier."""
        from .registrar_registry import (
            combine_registrar_sources,
            scan_registrar_registry,
        )

        trusted_authority = ScanAuthority.COMPLETE
        trusted_error: str | None = None
        trusted_findings: list[Finding] = []
        try:
            trusted_authority, trusted = discover_trusted(base=self.base)
        except RegistrarIndeterminateError as exc:
            trusted = self._trusted
            trusted_authority = ScanAuthority.INDETERMINATE
            trusted_error = str(exc)
            trusted_findings.append(
                self._trusted_error_finding(exc, reason="registry-indeterminate")
            )
        except RegistrarError as exc:
            trusted = self._trusted
            trusted_authority = ScanAuthority.INDETERMINATE
            trusted_error = str(exc)
            trusted_findings.append(
                self._trusted_error_finding(exc, reason="invalid-entry")
            )
        else:
            self._trusted = list(trusted)

        activation = self.activation_source() if self.activation_source else None
        plugins = scan_registrar_registry(
            self.dropins,
            previous=self._plugin_entries,
            activation_report=activation,
        )
        self._plugin_entries = dict(plugins.entries)
        combined = combine_registrar_sources(trusted, plugins)
        report = RegistrarDiscoveryReport(
            trusted_authority=trusted_authority,
            trusted_error=trusted_error,
            combined=combined,
        )
        self.last_report = report

        if emit_warnings:
            self._emit_batch(
                self.trusted_warning_tracker.select(trusted_findings),
                doctor="agent-dispatch registrar doctor",
            )
            self._emit_batch(
                self.warning_tracker.select(combined.findings),
                doctor="agent-dispatch registrar doctor",
            )
        return report

    def discover(self) -> list[ProfileDeclaration]:
        """Return the reconciled trusted-plus-plugin declaration set."""
        report = self.refresh()
        return list(report.combined.declarations)

    def discover_with_legacy(
        self,
        *,
        env_file: str | Path | None = None,
        profile_dir: str | Path | None = None,
    ) -> list[ProfileDeclaration]:
        """Return current declarations plus non-conflicting legacy env profiles."""
        declared = self.discover()
        names = {declaration.name for declaration in declared}
        legacy = [
            declaration
            for declaration in read_legacy_env_profiles(
                env_file=env_file,
                profile_dir=profile_dir,
            )
            if declaration.name not in names
        ]
        return [*declared, *legacy]
