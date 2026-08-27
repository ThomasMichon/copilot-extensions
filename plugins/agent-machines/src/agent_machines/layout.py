"""Diagnose and migrate agent-machines requirement-package layouts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import discover
from .manifest import ManifestError, load_package


@dataclass
class LayoutFinding:
    level: str
    code: str
    message: str


@dataclass
class LayoutReport:
    repo: str
    path: str
    status: str
    package_count: int = 0
    findings: list[LayoutFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(finding.level == "error" for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


@dataclass
class MigrationMove:
    source: str
    target: str


@dataclass
class MigrationResult:
    repo: str
    path: str
    status: str
    dry_run: bool
    changed: bool
    moves: list[MigrationMove] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _legacy_moves(repo_path: Path, repo_name: str) -> list[tuple[Path, Path]]:
    legacy = repo_path / discover.LEGACY_MACHINE_STATE_DIR
    if legacy.exists() and not legacy.is_dir():
        raise ManifestError(f"{legacy}: legacy package path is not a directory")
    if not legacy.exists():
        return []

    root = repo_path / discover.MACHINE_STATE_ROOT
    moves: list[tuple[Path, Path]] = []
    target_names: dict[str, Path] = {}
    package_names: dict[str, Path] = {}
    for entry in sorted(legacy.iterdir()):
        if not entry.is_file():
            raise ManifestError(
                f"{entry}: legacy migration supports direct YAML files and README.md only"
            )
        if entry.suffix.casefold() in {".yaml", ".yml"}:
            package = load_package(entry, source_repo=repo_name)
            if package.name in package_names:
                raise ManifestError(
                    f"{entry}: package {package.name!r} duplicates "
                    f"{package_names[package.name]}; canonical packages require "
                    "unique names"
                )
            package_names[package.name] = entry
            target = root / discover.ALL_PACKAGES_DIR / entry.name
        elif entry.name.casefold() == "readme.md":
            target = root / "README.md"
        else:
            raise ManifestError(
                f"{entry}: unsupported legacy entry; move or remove it before migration"
            )
        folded = str(target).casefold()
        if folded in target_names:
            raise ManifestError(
                f"{entry}: migration target collides with {target_names[folded]}"
            )
        if target.exists():
            raise ManifestError(f"{target}: migration target already exists")
        target_names[folded] = entry
        moves.append((entry, target))
    return moves


def inspect_repo_layout(repo_path: Path, repo_name: str, machine: str) -> LayoutReport:
    repo_path = repo_path.expanduser().absolute()
    root = repo_path / discover.MACHINE_STATE_ROOT
    legacy = repo_path / discover.LEGACY_MACHINE_STATE_DIR
    findings: list[LayoutFinding] = []

    if root.exists() and not root.is_dir():
        return LayoutReport(
            repo_name,
            str(repo_path),
            "malformed",
            findings=[LayoutFinding(
                "error", "invalid-layout", f"{root}: canonical package path is not a directory"
            )],
        )
    if legacy.exists() and not legacy.is_dir():
        return LayoutReport(
            repo_name,
            str(repo_path),
            "malformed",
            findings=[LayoutFinding(
                "error", "invalid-layout", f"{legacy}: legacy package path is not a directory"
            )],
        )

    if root.is_dir():
        mixed = legacy.is_dir()
        if mixed:
            findings.append(LayoutFinding(
                "error",
                "mixed-layout",
                f"{legacy} is ignored because {root} exists; finish or revert the migration",
            ))
        try:
            packages = discover.packages_in_repo(repo_path, repo_name, machine)
        except ManifestError as exc:
            findings.append(LayoutFinding("error", "invalid-layout", str(exc)))
            packages = []
        if mixed:
            status = "mixed"
        else:
            status = (
                "canonical"
                if not any(f.level == "error" for f in findings)
                else "malformed"
            )
        return LayoutReport(
            repo_name, str(repo_path), status, len(packages), findings
        )

    if legacy.is_dir():
        try:
            packages = discover.packages_in_repo(repo_path, repo_name, machine)
        except ManifestError as exc:
            return LayoutReport(
                repo_name,
                str(repo_path),
                "malformed",
                0,
                [LayoutFinding("error", "legacy-not-migratable", str(exc))],
            )
        try:
            moves = _legacy_moves(repo_path, repo_name)
        except ManifestError as exc:
            findings.append(LayoutFinding(
                "advisory",
                "legacy-not-migratable",
                f"{exc}; migrate this repo manually",
            ))
        else:
            code = "legacy-layout" if moves else "empty-legacy-layout"
            message = (
                f"run `agent-machines migrate --repo {repo_name}` to preview migration"
                if moves
                else "legacy directory is empty; remove it or add canonical packages"
            )
            findings.append(LayoutFinding("advisory", code, message))
        return LayoutReport(
            repo_name, str(repo_path), "legacy", len(packages), findings
        )

    return LayoutReport(repo_name, str(repo_path), "absent")


def _adopted_repos(
    registry: dict | None = None,
    projects: dict | None = None,
) -> list[tuple[str, Path]]:
    reg = registry if registry is not None else discover.read_registry()
    proj = projects if projects is not None else discover.read_projects()
    repos = reg.get("repos") or {}
    srcroot = reg.get("srcroot") or {}
    platform = discover.current_platform()
    targets: list[tuple[str, Path]] = []
    for name in (proj.get("projects") or {}):
        entry = repos.get(name) or {}
        path = discover.resolve_repo_path(
            name,
            entry if isinstance(entry, dict) else {},
            srcroot,
            platform,
        )
        if path is not None:
            targets.append((str(name), path))
    return targets


def resolve_repo(
    value: str,
    registry: dict | None = None,
    projects: dict | None = None,
) -> tuple[str, Path]:
    for name, path in _adopted_repos(registry, projects):
        if name == value:
            if not path.is_dir():
                raise ManifestError(f"registered repo {name!r} is unavailable at {path}")
            return name, path
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve().name, candidate.resolve()
    raise ManifestError(
        f"repo {value!r} is neither a directory nor an adopted project name"
    )


def inspect_layouts(
    machine: str,
    repo: str | None = None,
    registry: dict | None = None,
    projects: dict | None = None,
) -> list[LayoutReport]:
    if repo:
        name, path = resolve_repo(repo, registry, projects)
        return [inspect_repo_layout(path, name, machine)]

    reports: list[LayoutReport] = []
    for name, path in _adopted_repos(registry, projects):
        if not path.is_dir():
            reports.append(LayoutReport(
                name,
                str(path),
                "unavailable",
                findings=[LayoutFinding(
                    "advisory",
                    "repo-unavailable",
                    f"registered repo path does not exist: {path}",
                )],
            ))
            continue
        reports.append(inspect_repo_layout(path, name, machine))
    return reports


def migrate_repo_layout(
    repo_path: Path,
    repo_name: str,
    *,
    apply: bool = False,
) -> MigrationResult:
    repo_path = repo_path.expanduser().absolute()
    root = repo_path / discover.MACHINE_STATE_ROOT
    legacy = repo_path / discover.LEGACY_MACHINE_STATE_DIR

    if root.exists() and not root.is_dir():
        raise ManifestError(f"{root}: canonical package path is not a directory")
    if root.exists():
        if legacy.exists():
            raise ManifestError(
                f"refusing mixed-layout migration: both {root} and {legacy} exist"
            )
        return MigrationResult(
            repo_name, str(repo_path), "already-canonical", not apply, False
        )
    if not legacy.exists():
        return MigrationResult(
            repo_name, str(repo_path), "no-layout", not apply, False
        )

    planned = _legacy_moves(repo_path, repo_name)
    moves = [MigrationMove(str(source), str(target)) for source, target in planned]
    if not planned:
        return MigrationResult(
            repo_name, str(repo_path), "no-layout", not apply, False
        )
    if not apply:
        return MigrationResult(
            repo_name, str(repo_path), "would-migrate", True, True, moves
        )

    completed: list[tuple[Path, Path]] = []
    try:
        (root / discover.ALL_PACKAGES_DIR).mkdir(parents=True, exist_ok=False)
        for source, target in planned:
            if target.exists():
                raise FileExistsError(f"migration target appeared during apply: {target}")
            source.replace(target)
            completed.append((source, target))
        legacy.rmdir()
    except OSError as exc:
        rollback_errors: list[str] = []
        for source, target in reversed(completed):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for directory in (root / discover.ALL_PACKAGES_DIR, root):
            try:
                if directory.exists() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        detail = f"; rollback failures: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise ManifestError(f"migration failed: {exc}{detail}") from exc

    return MigrationResult(
        repo_name, str(repo_path), "migrated", False, True, moves
    )
